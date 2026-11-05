"""매핑 전 스키마 변경 확인 단계 (FS F10·6절, SC03, TD 10-3, M6-4). Workspace에 섞어 쓴다.

- 업로드(또는 재시도) 때 읽기에 성공한 항목마다 같은 출처의 비교 후보 양식을 고르고 기준 프로파일과 비교한다.
  항목 상태: `no_change`(변경 없음) · `changed`(변경 있음) · `choose`(후보 동률 — 사용자가 고름) ·
  `baseline_create`(겹치는 양식 없음, 또는 사용자가 "기준 생성"을 고름) · `no_baseline`(양식은 있으나 발행한 기준 없음).
- `changed`·`choose`가 하나라도 있으면 확인이 필요하고, 확인 전에는 매핑을 확정할 수 없다(FS 6절).
  변경이 없거나 첫 실행이면 확인 없이 지나간다(SC03 건너뜀).
- 비교 기준 선택은 후보 목록 안에서만 하며 매핑을 확정한 뒤에는 바꿀 수 없다. 확인은 event에 남긴다.
- 변경 감지는 알림이다. 어떤 값도 바꾸지 않는다(AC-10-3).
"""

from __future__ import annotations

import hashlib
import json

from prep import drift, templates
from prep.errors import FlowError

NEEDS_CONFIRMATION = ("changed", "choose")


class DriftMixin:
    def _source_of(self, run: dict) -> int:
        return self.store.conn.execute("SELECT source_id FROM batch WHERE id = ?",
                                       (run["batch_id"],)).fetchone()["source_id"]

    def _drift_item(self, f: dict, template_id: int, cat) -> dict:
        t = templates.get(self.store, template_id)
        base = templates.baseline(self.store, template_id)
        entry = {"template_id": template_id, "std_table": t["std_table"], "baseline": None, "changes": []}
        if base is None:
            return entry | {"state": "no_baseline"}
        profile = drift.pick_baseline(f["headers"], base["profiles"])
        result = drift.compare(f["headers"], self._rows(f), profile, cat.rules.get(t["std_table"], {}))
        return entry | {"state": result["status"], "changes": result["changes"],
                        "baseline": {"result_version_id": base["result_version_id"], "run_id": base["run_id"]}}

    def _check_drift(self, run: dict) -> None:
        """업로드·재시도 직후 호출한다. 설정 없음 실행은 비교 단위가 없어 건너뛴다."""
        cat = self._catalog(run)
        if cat is None:
            run["drift"] = None
            return
        known = templates.for_source(self.store, self._source_of(run))
        items = {}
        for f in run["files"]:
            if f["read_error"]:
                continue
            c = drift.candidates(f["headers"], known)
            entry = {"item_id": f["item_id"], "candidates": c["ranked"], "reason": c["reason"], "chosen_by": "auto"}
            if c["choice"] is None:
                entry |= {"state": "choose" if c["reason"] == "tie" else "baseline_create", "template_id": None,
                          "std_table": None, "baseline": None, "changes": []}
            else:
                entry |= self._drift_item(f, c["choice"], cat)
            items[str(f["item_id"])] = entry
        required = any(e["state"] in NEEDS_CONFIRMATION for e in items.values())
        run["drift"] = {"items": items, "required": required, "confirmed": not required}
        self.store.record_event("run", run["db_run"], "drift_checked",
                                {"required": required, "items": _summary(items)})

    def _drift_gate(self, run: dict) -> None:
        d = run.get("drift")
        if d and d["required"] and not d["confirmed"]:
            raise FlowError("drift_unconfirmed", "스키마 변경을 먼저 확인해야 한다", 409)

    def _drift_editable(self, run: dict) -> dict:
        self._open(run)
        d = run.get("drift")
        if not d:
            raise FlowError("no_drift", "이 실행에는 스키마 변경 확인 단계가 없다", 409)
        if run["status"] != "needs_confirmation" or any(f["confirmed"] and not f["read_error"] for f in run["files"]):
            raise FlowError("mapping_confirmed", "매핑을 확정한 뒤에는 비교 기준을 바꿀 수 없다", 409)
        return d

    def drift_view(self, run_id: str) -> dict:
        run = self.load(run_id)
        d = run.get("drift")
        if not d:
            return {"required": False, "confirmed": True, "items": []}
        public = {db: r["id"] for db, r in self._public_ids().items()}
        files = {f["item_id"]: f for f in run["files"]}
        items = []
        for key, e in d["items"].items():
            f = files[int(key)]
            base = e.get("baseline")
            items.append(e | {"name": f["name"], "sheet": f.get("sheet"), "headers": f["headers"],
                              "baseline": None if base is None else base | {"run": public.get(base["run_id"])}})
        return {"required": d["required"], "confirmed": d["confirmed"], "items": items}

    def drift_select(self, run_id: str, item_id: int, template_id: int | None) -> dict:
        """비교 기준 고르기. template_id가 None이면 "기준 생성"(이번 파일로 새 기준을 만든다)."""
        run = self.load(run_id)
        d = self._drift_editable(run)
        entry = d["items"].get(str(item_id))
        if entry is None:
            raise FlowError("bad_item", "비교할 파일이 아니다")
        if template_id is None:
            entry |= {"state": "baseline_create", "template_id": None, "std_table": None, "baseline": None,
                      "changes": []}
        else:
            if template_id not in {c["template_id"] for c in entry["candidates"]}:
                raise FlowError("bad_template", "비교 후보 양식이 아니다")
            f = next(f for f in run["files"] if f["item_id"] == item_id)
            entry |= self._drift_item(f, template_id, self._catalog(run))
        entry["chosen_by"] = "user"
        d["required"] = d["required"] or any(e["state"] in NEEDS_CONFIRMATION for e in d["items"].values())
        d["confirmed"] = False if d["required"] else d["confirmed"]
        self.store.record_event("run", run["db_run"], "drift_selected",
                                {"item": item_id, "template": template_id, "state": entry["state"]})
        self.save(run)
        return self.drift_view(run_id)

    def drift_confirm(self, run_id: str) -> dict:
        run = self.load(run_id)
        d = self._drift_editable(run)
        pending = [int(k) for k, e in d["items"].items() if e["state"] == "choose"]
        if pending:
            raise FlowError("drift_choice_needed", f"비교 기준을 고르지 않은 파일이 있다: {pending}", 409)
        summary = _summary(d["items"])
        digest = hashlib.sha256(json.dumps(d["items"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        d["confirmed"] = True
        self.store.record_event("run", run["db_run"], "drift_confirmed", {"items": summary, "hash": digest})
        self.save(run)
        return self.drift_view(run_id)


def _summary(items: dict) -> list[dict]:
    return [{"item": e["item_id"], "state": e["state"], "template": e["template_id"],
             "baseline": (e.get("baseline") or {}).get("result_version_id"), "changes": len(e["changes"])}
            for e in items.values()]
