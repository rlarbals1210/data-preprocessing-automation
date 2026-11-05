"""재사용 규칙을 다음 실행에 쓰는 단계 — 매핑 쪽 (FS F03-6·F09, TD 10-2·10-3, 결정 M6-D2, M6-6a). Workspace에 섞어 쓴다.

- 업로드·재시도 때: 변경 감지가 고른 양식이 이 파일의 양식과 **같을 때만**(헤더 구성 동일 — 규칙은 다른 양식으로
  옮기지 않는다) 그 양식의 효력 있는 매핑 규칙을 자동 제안(확신 등급 `reuse`)으로 올린다. 스키마 변경이 감지된 컬럼은 뺀다.
- 매핑 확정 때(F03-6): 같은 양식의 매핑 규칙과 확정 매핑이 다르면(`mapping_changed`), 또는 헤더 구성이 바뀌어 같은
  출처·표준 테이블의 다른 양식에만 규칙이 있으면(`header_changed`, AC-10-2) 재사용 규칙 영향을 고르게 한다.
  선택지: `this_run`(이번 실행만 새 매핑, 규칙 그대로) · `new_version`(새 규칙 버전 승인 — 최종 확인 때 기록, 발행 시 효력).
  "기존 규칙 유지"는 화면에서 규칙대로 되돌려 다시 확정하는 것이므로 서버 선택지가 아니다.
"""

from __future__ import annotations

from prep import reuse, templates
from prep.errors import FlowError
from prep.mapping import apply_reuse

IMPACT_CHOICES = ("this_run", "new_version")


def changed_columns(changes: list[dict]) -> set[str]:
    """스키마 변경이 걸린 원래 헤더(컬럼 단위 변화만 — 헤더 목록 전체 변화 표시는 제외)."""
    out: set[str] = set()
    for c in changes:
        if c["kind"] == "raw_headers":
            continue
        for k in ("column", "baseline_column"):
            if c.get(k):
                out.add(c[k])
        if c["kind"].startswith("rename"):
            out.update(x for x in (c.get("before"), c.get("after")) if isinstance(x, str))
    return out


def _mapping_rule(store, template_id: int) -> dict | None:
    return next((r for r in reuse.effective(store, template_id) if r["kind"] == "mapping"), None)


class ReuseMixin:
    def _apply_mapping_reuse(self, run: dict) -> None:
        d, cat = run.get("drift"), self._catalog(run)
        if not d or cat is None:
            return
        for f in run["files"]:
            e = d["items"].get(str(f["item_id"]))
            if not e or not e.get("template_id") or e["state"] not in ("no_change", "changed"):
                continue
            t = templates.get(self.store, e["template_id"])
            if templates.header_signature(f["headers"])[0] != t["header_signature"]:
                continue  # 같은 양식에서만(AC-09-1)
            rule = _mapping_rule(self.store, t["id"])
            if rule is None:
                continue
            f["proposal"] = apply_reuse(f["proposal"], f["headers"], rule, changed_columns(e["changes"]), cat)
            f["mapping"] = {"table": f["proposal"]["table"]["table"],
                            "columns": {c["original"]: c["logical"] for c in f["proposal"]["columns"]}}

    def _mapping_impacts(self, run: dict, files: list[dict]) -> list[dict]:
        source = self._source_of(run)
        by_id = {f["item_id"]: f for f in run["files"]}
        out = []
        for x in files:
            table = x.get("table")
            if not table:
                continue
            f = by_id[x["item_id"]]
            sig, normalized = templates.header_signature(f["headers"])
            same = templates.for_source(self.store, source, table)
            own = next((t for t in same if t["header_signature"] == sig), None)
            rule = _mapping_rule(self.store, own["id"]) if own else None
            if rule is not None:
                now = x.get("columns") or {}
                was = rule["spec"]["columns"]
                diff = [{"column": h, "rule": was.get(h), "now": now.get(h)} for h in f["headers"]
                        if was.get(h) != now.get(h)]
                if diff or rule["spec"]["table"] != table:
                    out.append({"item_id": f["item_id"], "kind": "mapping_changed", "template_id": own["id"],
                                "version": rule["version"], "diff": diff})
                continue
            prior = []
            for t in same:
                if own and t["id"] == own["id"]:
                    continue
                rules = reuse.effective(self.store, t["id"])
                if rules:
                    prior.append({"template_id": t["id"], "headers": t["headers"], "rules": len(rules)})
            if prior:
                out.append({"item_id": f["item_id"], "kind": "header_changed", "headers": normalized, "prior": prior})
        return out

    def _check_impacts(self, run: dict, files: list[dict], choices: dict | None) -> dict:
        impacts = self._mapping_impacts(run, files)
        chosen = {str(k): v for k, v in (choices or {}).items()}
        if any(chosen.get(str(i["item_id"])) not in IMPACT_CHOICES for i in impacts):
            raise FlowError("reuse_impact_needed", "재사용 규칙 영향을 골라야 한다", 409, {"impacts": impacts})
        return {str(i["item_id"]): i | {"choice": chosen[str(i["item_id"])]} for i in impacts}
