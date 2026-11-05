"""재사용 규칙을 다음 실행에 쓰는 단계 — 매핑 쪽 (FS F03-6·F09, TD 10-2·10-3, 결정 M6-D2, M6-6a). Workspace에 섞어 쓴다.

- 업로드·재시도 때: 변경 감지가 고른 양식이 이 파일의 양식과 **같을 때만**(헤더 구성 동일 — 규칙은 다른 양식으로
  옮기지 않는다) 그 양식의 효력 있는 매핑 규칙을 자동 제안(확신 등급 `reuse`)으로 올린다. 스키마 변경이 감지된 컬럼은 뺀다.
- 매핑 확정 때(F03-6): 같은 양식의 매핑 규칙과 확정 매핑이 다르면(`mapping_changed`), 또는 헤더 구성이 바뀌어 같은
  출처·표준 테이블의 다른 양식에만 규칙이 있으면(`header_changed`, AC-10-2) 재사용 규칙 영향을 고르게 한다.
  선택지: `this_run`(이번 실행만 새 매핑, 규칙 그대로) · `new_version`(새 규칙 버전 승인 — 최종 확인 때 기록, 발행 시 효력).
  "기존 규칙 유지"는 화면에서 규칙대로 되돌려 다시 확정하는 것이므로 서버 선택지가 아니다.
- 미리보기 기본 선택(M6-D4, M6-6b): 효력 있는 표준화 규칙이 있는 양식의 셀은 규칙과 맞을 때만 미리 선택한다 —
  셀의 모든 진단 코드에 대해 같은 묶음 ID의 규칙이 있고, 규칙의 설정 해시·변환 ID가 지금과 같고, 원문 형태 서명이
  규칙의 승인 서명 집합에 있고, 그 컬럼에 스키마 변경이 감지되지 않았을 때(AC-09-2). 규칙이 없는 양식은 지금처럼 전체 선택.
  미리 선택도 최종 확인 없이는 적용되지 않는다(AC-09-3). 보류 셀은 후보가 아니라 처음부터 선택 대상이 아니다.
- 승인 취소(SC12, M6-7): 다음 실행부터 그 규칙은 쓰이지 않는다(AC-09-4). 준비 중인 실행에도 바로 반영한다 —
  표준화 규칙: 저장된 적용 계획에서 그 규칙과 맞는 셀을 제외 셀로 돌리고 계획을 다시 기록한다(실행 승인 무효 → 재확인).
  매핑 규칙: 매핑을 아직 확정하지 않은 실행에서 그 규칙으로 올린 제안을 처음 제안으로 되돌린다.
  확정된 매핑은 사용자가 확정한 것이므로 그대로 둔다. 끝난 실행과 과거 결과 버전은 바꾸지 않는다.
"""

from __future__ import annotations

import json

from prep import reuse, templates
from prep.apply import build_apply_plan, group_id
from prep.apply import candidates as apply_candidates
from prep.errors import FlowError
from prep.mapping import apply_reuse, propose
from prep.profile import signature

IMPACT_CHOICES = ("this_run", "new_version")
PREPARED = ("diagnosed", "awaiting_approval")  # 적용 계획이 실행 승인을 기다리는 상태


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
        if not d or cat is None or not self.reuse_enabled:
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
        if not self.reuse_enabled:
            return []
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

    def _standardize_rules(self, run: dict) -> dict[int, tuple[dict, set[str]]]:
        """업로드 항목 → (그 양식의 효력 있는 표준화 규칙 {묶음 ID: 규칙}, 스키마 변경이 걸린 논리 컬럼).
        효력 있는 표준화 규칙이 없는 양식의 항목은 빠진다(기본 전체 선택)."""
        cat = self._catalog(run)
        links = templates.for_run(self.store, run["db_run"])
        if cat is None or not links or not self.reuse_enabled:
            return {}
        config = self.ledger.config_hash(run["db_run"])
        items = (run.get("drift") or {}).get("items", {})
        out = {}
        for f in run["files"]:
            tid = links.get(f["item_id"])
            rules = [r for r in reuse.effective(self.store, tid)  # 이 실행이 승인한 규칙은 이 실행의 근거가 아니다
                     if r["kind"] == "standardize" and r["approved_in_run"] != run["db_run"]] if tid else []
            if not rules:
                continue
            usable = {}
            for r in rules:
                s = r["spec"]
                now = reuse.transform_id(s["code"], cat.rules.get(s["table"], {}).get(s["column"], {}))
                if r["config_hash"] == config and r["transform_id"] == now:
                    usable[r["rule_key"]] = r
            e = items.get(str(f["item_id"])) or {}
            raw = changed_columns(e.get("changes") or [])
            columns = f["mapping"]["columns"]
            changed = {columns[h] for h in raw if columns.get(h)}
            changed |= {c["logical"] for c in e.get("changes") or [] if c.get("logical") and c["kind"] != "raw_headers"}
            out[f["item_id"]] = (usable, changed)
        return out

    def _reuse_marks(self, run: dict, cells: dict) -> tuple[dict, set]:
        """(규칙과 맞는 셀 {셀: True}, 효력 있는 표준화 규칙이 있는 양식의 셀 집합)."""
        ruled = self._standardize_rules(run)
        marks, governed = {}, set()
        for key, v in cells.items():
            item = (v.get("source") or {}).get("item_id")
            if item not in ruled:
                continue
            governed.add(key)
            usable, changed = ruled[item]
            sig = signature(v["before"] or "")
            if key[2] not in changed and all(
                    sig in usable.get(group_id(key[0], key[2], c), {}).get("spec", {}).get("signatures", ())
                    for c in v["codes"]):
                marks[key] = True
        return marks, governed

    def _default_selection(self, run: dict, cells: dict) -> dict | None:
        """M6-D4 기본 선택. 규칙이 있는 양식이 없으면 None(전체 선택)."""
        marks, governed = self._reuse_marks(run, cells)
        if not governed:
            return None
        chosen = {k for k in cells if k not in governed or k in marks}
        approved = {group_id(k[0], k[2], c) for k in chosen for c in cells[k]["codes"]}
        excluded = [list(k) for k in sorted(set(cells) - chosen)
                    if any(group_id(k[0], k[2], c) in approved for c in cells[k]["codes"])]
        return {"view_hash": run["view_hash"], "approved_groups": sorted(approved), "excluded_cells": excluded}

    # ---- 재사용 규칙 목록·승인 취소 (SC12, M6-7) ----
    def _rule_view(self, r: dict, runs: dict[int, dict]) -> dict:
        return {"id": r["id"], "kind": r["kind"], "rule_key": r["rule_key"], "version": r["version"],
                "state": r["state"], "spec": r["spec"], "transform_id": r["transform_id"],
                "approved_at": r["approved_at"], "run": runs.get(r["approved_in_run"], {}).get("id"),
                "revoked_at": r["revoked_at"], "revoked_reason": r["revoked_reason"]}

    def reuse_units(self) -> list[dict]:
        """비교·재사용 단위(출처·표준 테이블·양식)별 규칙 목록 — 규칙이 하나라도 있는 단위만."""
        runs = self._public_ids()
        out = []
        for s in self.store.sources():
            for t in templates.for_source(self.store, s["id"]):
                rules = reuse.for_template(self.store, t["id"])
                if rules:
                    out.append({"template_id": t["id"], "source": {"id": s["id"], "name": s["name"]},
                                "table": t["std_table"], "headers": t["headers"],
                                "rules": [self._rule_view(r, runs) for r in rules]})
        return out

    def revoke_reuse_rule(self, rule_id: int, reason: str) -> dict:
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
            raise FlowError("bad_revoke", "승인 취소 사유는 1~500자다")
        if reuse.get(self.store, rule_id) is None:
            raise FlowError("rule_not_found", "없는 재사용 규칙이다", 404)
        try:
            rule = reuse.revoke(self.store, rule_id, reason)
        except reuse.ReuseError as exc:
            raise FlowError("not_revocable", str(exc), 409) from exc
        affected = []
        for public in sorted(r["id"] for r in self._public_ids().values()):
            run = self.load(public)
            if self._revoke_in_run(run, rule):
                affected.append(run["id"])
        return {"rule": self._rule_view(rule, self._public_ids()), "runs": affected}

    def _revoke_in_run(self, run: dict, rule: dict) -> bool:
        tid = rule["template_id"]
        if rule["kind"] == "mapping":
            return self._revoke_mapping(run, tid)
        if run["status"] not in PREPARED:
            return False
        path = self.run_dir(run["id"]) / "apply-plan.json"
        links = templates.for_run(self.store, run["db_run"])
        items = {i for i, t in links.items() if t == tid}
        if not path.is_file() or not items:
            return False  # 계획이 아직 없으면 기본 선택을 만들 때 취소가 이미 반영된다
        plan = json.loads(path.read_text(encoding="utf-8"))
        report, blocked = self._report(run["id"]), self._blocked(run["id"])
        cells, _ = apply_candidates(report, blocked)
        sigs = set(rule["spec"]["signatures"])
        off = [k for k in map(tuple, plan["allow"])
               if (cells[k].get("source") or {}).get("item_id") in items
               and any(group_id(k[0], k[2], c) == rule["rule_key"] for c in cells[k]["codes"])
               and signature(cells[k]["before"] or "") in sigs]
        if not off:
            return False
        selection = {"view_hash": run["view_hash"], "approved_groups": plan["approved_groups"],
                     "excluded_cells": sorted(plan["excluded_cells"] + [list(k) for k in off])}
        self._store_plan(run, build_apply_plan(report, run["view_hash"], selection, extra_blocked=blocked))
        self.store.record_event("run", run["db_run"], "reuse_revoked_applied",
                                {"rule": rule["id"], "deselected": len(off)})
        return True

    def _revoke_mapping(self, run: dict, template_id: int) -> bool:
        if run["status"] != "needs_confirmation" or not run.get("drift"):
            return False
        cat = self._catalog(run)
        changed = []
        for f in run["files"]:
            e = run["drift"]["items"].get(str(f["item_id"])) or {}
            p = f.get("proposal")
            if f["confirmed"] or not p or e.get("template_id") != template_id or p["table"].get("basis") != "reuse":
                continue
            f["proposal"] = propose(f["sheet"] or f["name"], f["headers"], self._rows(f), cat)
            f["mapping"] = {"table": f["proposal"]["table"]["table"],
                            "columns": {c["original"]: c["logical"] for c in f["proposal"]["columns"]}}
            changed.append(f["item_id"])
        if not changed:
            return False
        self.save(run)
        self.store.record_event("run", run["db_run"], "reuse_revoked_applied",
                                {"template": template_id, "items": changed})
        return True
