"""적용 계획 (docs/technical-design.md 8-2·8-3절, FS F06, M1-9).

사용자의 선택(승인한 규칙 묶음, 제외한 셀)을 받아 서버가 최종 허용 셀 목록을 만든다.
- 허용 셀은 반드시 현재 뷰의 최신 후보 − 보류의 부분집합이다. 없는 묶음·후보가 아닌 셀·보류 셀·이전 뷰의 선택은 거부(T-08).
- 보류는 사용자 선택과 무관하게 진단 결과로 정한다(같은 셀에 업무 위반·사람 검토가 있으면 보류, 동일 행 중복만이면 보류 안 함).
  정합성 보류(extra_blocked)는 M4에서 더한다.
- 한 셀이 여러 묶음(같은 컬럼의 서로 다른 규칙)에 속하면 그 묶음이 모두 승인돼야 적용한다(보수적).
- 미리보기 건수와 실행 건수는 같은 적용 계획에서 나온다(AC-06-5).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

PROBLEM = {"business_rule_violation", "human_review"}
Cell = tuple[str, int, str]


class ApplyError(ValueError):
    pass


def _h(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def group_id(table: str, column: str, code: str) -> str:
    return f"{table}.{column}.{code}"


def candidates(report: dict, extra_blocked: Iterable[Cell] = ()) -> tuple[dict, dict]:
    """(후보 셀, 보류 셀). 값: before·after(논리 null은 None)·codes·source."""
    blocked = {(i["table"], i["record"], c) for i in report["issues"]
               if i["category"] in PROBLEM and i["code"] != "duplicate_row" for c in i["columns"]}
    blocked |= {tuple(k) for k in extra_blocked}
    cells: dict[Cell, dict] = {}
    held: dict[Cell, dict] = {}
    for i in report["issues"]:
        if i["category"] != "standardization_candidate":
            continue
        key = (i["table"], i["record"], i["columns"][0])
        target = held if key in blocked else cells
        entry = target.setdefault(key, {"before": i["values"][key[2]], "after": i.get("suggested_value"),
                                        "codes": [], "source": i.get("source")})
        if entry["after"] != i.get("suggested_value"):
            raise ApplyError(f"한 셀에 서로 다른 제안: {key}")
        if i["code"] not in entry["codes"]:
            entry["codes"].append(i["code"])
    return cells, held


def candidates_hash(cells: dict, held: dict) -> str:
    """후보 해시(TD 4-2): 원본 위치·논리 컬럼·원문·제안값·변환 근거 + 보류 목록."""
    def rows(d):
        return sorted([list(k), v["before"], v["after"], sorted(v["codes"])] for k, v in d.items())
    return _h({"candidates": rows(cells), "held": rows(held)})


def groups(cells: dict, held: dict, examples: int = 3) -> list[dict]:
    out: dict[str, dict] = {}
    for bucket, name in ((cells, "cells"), (held, "held")):
        for (table, _record, column), v in sorted(bucket.items()):
            for code in v["codes"]:
                g = out.setdefault(group_id(table, column, code), {
                    "id": group_id(table, column, code), "table": table, "column": column, "code": code,
                    "cells": 0, "held": 0, "examples": []})
                g[name] += 1
                ex = {"before": v["before"], "after": "" if v["after"] is None else v["after"]}
                if name == "cells" and len(g["examples"]) < examples and ex not in g["examples"]:
                    g["examples"].append(ex)
    return sorted(out.values(), key=lambda g: (-g["cells"], g["table"], g["column"], g["code"]))


def build_apply_plan(report: dict, view_hash: str, selection: dict | None = None,
                     extra_blocked: Iterable[Cell] = ()) -> dict:
    """selection: {view_hash, approved_groups: [id, ...] | None(전체), excluded_cells: [[table, record, column], ...]}."""
    cells, held = candidates(report, extra_blocked)
    all_groups = {g["id"] for g in groups(cells, held) if g["cells"]}
    selection = selection or {}
    if selection.get("view_hash", view_hash) != view_hash:
        raise ApplyError("이전 진단의 선택이다. 미리보기를 다시 불러오세요")
    approved = all_groups if selection.get("approved_groups") is None else set(selection["approved_groups"])
    unknown = approved - all_groups
    if unknown:
        raise ApplyError(f"현재 후보에 없는 규칙 묶음: {sorted(unknown)[:5]}")
    excluded = set()
    for k in selection.get("excluded_cells") or []:
        if not (isinstance(k, list | tuple) and len(k) == 3):
            raise ApplyError(f"제외 셀 형식이 잘못됐다: {k!r}")
        key = (str(k[0]), int(k[1]), str(k[2]))
        if key in held:
            raise ApplyError(f"보류 셀은 선택할 수 없다: {key}")
        if key not in cells:
            raise ApplyError(f"현재 후보가 아닌 셀: {key}")
        excluded.add(key)
    allow = sorted(k for k, v in cells.items()
                   if k not in excluded and all(group_id(k[0], k[2], c) in approved for c in v["codes"]))
    plan = {
        "view_hash": view_hash,
        "candidates_hash": candidates_hash(cells, held),
        "approved_groups": sorted(approved),
        "excluded_cells": sorted([list(k) for k in excluded]),
        "allow": [list(k) for k in allow],
        "counts": {"candidates": len(cells), "held": len(held), "allowed": len(allow),
                   "not_approved": len(cells) - len(allow)},
    }
    plan["hash"] = _h({k: plan[k] for k in ("view_hash", "candidates_hash", "allow")})
    return plan


def verify_plan(plan: dict, report: dict, view_hash: str, extra_blocked: Iterable[Cell] = ()) -> None:
    """실행 직전: 적용 계획이 현재 뷰·후보와 맞는지 다시 확인한다(TD 4-2, AC-06-4)."""
    cells, held = candidates(report, extra_blocked)
    if plan["view_hash"] != view_hash:
        raise ApplyError("뷰가 바뀌었다. 다시 진단·승인하세요")
    if plan["candidates_hash"] != candidates_hash(cells, held):
        raise ApplyError("후보가 바뀌었다. 미리보기를 다시 확인하세요")
    if not {tuple(k) for k in plan["allow"]} <= set(cells):
        raise ApplyError("허용 셀이 현재 후보 − 보류의 부분집합이 아니다")
    if plan["hash"] != _h({k: plan[k] for k in ("view_hash", "candidates_hash", "allow")}):
        raise ApplyError("적용 계획이 변조됐다")
