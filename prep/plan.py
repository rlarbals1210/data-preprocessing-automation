"""실행 계획 (docs/technical-design.md 5-2절, FS F03·F04, M1-6).

확정 매핑으로 각 표준 테이블에 실제로 있는 논리 컬럼을 구하고, 논리 설정의 규칙을 하나씩 판정한다.
- 컬럼이 없음 → missing_column
- 참조 대상 테이블이 실행 대상에 없음 → excluded_target (참조 오류로 바꾸지 않는다)
- 실행 안 하는 컬럼에 기대는 required_if·unique·references → dependency_skipped
실행할 규칙만으로 엔진 검증기를 통과하는 완결된 설정을 만든다. 빠진 컬럼을 빈 값으로 채우지 않는다.
테이블 판정: 진단 가능(실행할 규칙 1개 이상) / 발행 가능(필수 표준 컬럼이 모두 있고 진단 가능) / 발행 제외(사유).
규칙 단위는 엔진 rule_execution과 같은 (table, kind, columns[, target])이다.
"""

from __future__ import annotations

import copy
import hashlib
import json

from diagnostics.engine import column_rule_kinds, validate_config

MISSING_COLUMN = "missing_column"
EXCLUDED_TARGET = "excluded_target"
DEPENDENCY_SKIPPED = "dependency_skipped"


def _rule(table: str, kind: str, columns: list[str], target: str | None = None) -> dict:
    r = {"table": table, "kind": kind, "columns": list(columns)}
    if target is not None:
        r["target"] = target
    return r


def build_plan(logical: dict, present: dict[str, set[str]]) -> dict:
    """logical: 논리 설정 전체. present: 실행 대상 표준 테이블 → 매핑된 논리 컬럼 집합."""
    executed, skipped, tables = [], [], {}
    specs = logical.get("tables", {})
    for name in present:
        if name not in specs:
            raise ValueError(f"논리 설정에 없는 표준 테이블: {name}")
    for name, cols in present.items():
        spec = specs[name]
        columns = {}
        for col, rule in spec["columns"].items():
            if col not in cols:
                skipped += [_rule(name, k, [col]) | {"reason": MISSING_COLUMN} for k in column_rule_kinds(rule)]
                continue
            rule = copy.deepcopy(rule)
            if "required_if" in rule and rule["required_if"]["column"] not in cols:
                rule.pop("required_if")
                skipped.append(_rule(name, "required_if", [col]) | {"reason": DEPENDENCY_SKIPPED})
            columns[col] = rule
        new: dict = {"columns": columns}
        unique = []
        for group in spec.get("unique", []):
            if all(c in columns for c in group):
                unique.append(list(group))
            else:
                skipped.append(_rule(name, "unique", group) | {"reason": DEPENDENCY_SKIPPED})
        if unique:
            new["unique"] = unique
        tables[name] = new
    for name in present:
        refs = []
        for ref in specs[name].get("references", []):
            target = ref["table"]
            if target not in present:
                reason = EXCLUDED_TARGET
            elif not all(c in tables[name]["columns"] for c in ref["columns"]) or not all(
                    c in tables[target]["columns"] for c in ref["target_columns"]):
                reason = DEPENDENCY_SKIPPED
            else:
                refs.append(copy.deepcopy(ref))
                continue
            skipped.append(_rule(name, "reference", ref["columns"], target) | {"reason": reason})
        if refs:
            tables[name]["references"] = refs
    for name, spec in tables.items():
        for col, rule in spec["columns"].items():
            executed += [_rule(name, k, [col]) for k in column_rule_kinds(rule)]
        executed += [_rule(name, "unique", g) for g in spec.get("unique", [])]
        executed += [_rule(name, "reference", r["columns"], r["table"]) for r in spec.get("references", [])]
    config = {"version": logical.get("version", 1), "tables": tables} if tables else {}
    if config:
        validate_config(copy.deepcopy(config))
    judgments = {}
    for name in tables:
        required = [c for c, r in specs[name]["columns"].items() if r.get("required") is True]
        missing = [c for c in required if c not in present[name]]
        diagnosable = any(r["table"] == name for r in executed)
        reason = None if diagnosable and not missing else ("no_rules" if not diagnosable else "missing_required")
        judgments[name] = {"diagnosable": diagnosable, "publishable": reason is None,
                           "missing_required": missing, "reason": reason}
    plan = {"config": config, "executed": executed, "skipped": skipped, "tables": judgments,
            "completeness": "partial" if skipped else "full"}
    plan["hash"] = plan_hash(plan)
    return plan


def plan_hash(plan: dict) -> str:
    """실행 계획 해시(TD 4-2): 실행할 규칙·실행 안 할 규칙과 사유·엔진 설정. 승인에 묶인다."""
    body = {k: plan[k] for k in ("config", "executed", "skipped", "tables")}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
