"""테이블 간 업무 정합성 검사 (docs/technical-design.md 9절·부록 A, FS F05, M4).

규칙은 JSON AST다(문자열을 실행하지 않음). 모든 규칙은 기준 테이블(anchor)의 각 행에 대해 평가한다.

- validate(): 규칙 문법 검증기(T-10 문법). 종류·필수 키·표현식·경로·조건·논리 컬럼 존재를 본다.
- evaluate(): 기준 행마다 ok / violation / not_applicable / not_computable(사유) 판정.
  값은 공급자(provider)가 준다 — 원문 / 가상(모든 표준화 후보 적용) / 승인(현재 적용 계획). 평가기는 같고 공급자만 바꾼다.
- 자동 수정·제안값을 만들지 않는다. 연결을 확정할 수 없으면 위반으로 확정하지 않고 계산 불가로 둔다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from diagnostics.engine import normalize

RULE_KEYS = {
    "row_equation": {"left", "right"},
    "parent_sum_equation": {"left", "right", "empty_child"},
    "cumulative_le": {"limit", "total", "empty_child"},
    "time_order": {"earlier", "later"},
    "status_requires": {"requires"},
}
OPTIONAL_KEYS = {"time_order": {"each", "missing_earlier", "empty_child"}}
COMMON_KEYS = {"id", "type", "enabled", "anchor", "description"}
OPTIONAL_COMMON = {"when"}
EMPTY_CHILD = {"ok", "violation", "not_computable"}
REASONS = ("parse_error", "missing_value", "missing_reference", "duplicate_parent", "ambiguous_join",
           "missing_column", "excluded_target")
VALUE_KEYS = {"col", "const", "add", "sub", "mul", "sum", "count", "max", "min"}
AGGREGATES = {"sum", "max", "min"}
OPS = {"eq", "le", "ge"}

_INT = re.compile(r"-?[0-9]+")
_ISO_TZ = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|[+-][0-9]{2}:[0-9]{2})")


class RuleError(ValueError):
    pass


# ---------------------------------------------------------------- 문법 검증 (M4-1)

def validate(doc: dict, columns: dict[str, list[str]]) -> list[str]:
    """규칙 문서의 문법 오류 목록(비면 통과). columns: 표준 테이블 → 논리 컬럼 목록(schema)."""
    errors: list[str] = []
    if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("rules"), list):
        return ["최상위는 {version: 1, rules: [...]} 이어야 한다"]
    seen = set()
    for n, rule in enumerate(doc["rules"]):
        where = f"rules[{n}]"
        if not isinstance(rule, dict):
            errors.append(f"{where}: 규칙은 객체여야 한다")
            continue
        rid = rule.get("id")
        where = f"{rid or where}"
        if not isinstance(rid, str) or not rid:
            errors.append(f"{where}: id가 없다")
        elif rid in seen:
            errors.append(f"{where}: id가 겹친다")
        seen.add(rid)
        kind = rule.get("type")
        if kind not in RULE_KEYS:
            errors.append(f"{where}: 알 수 없는 type {kind!r}")
            continue
        allowed = COMMON_KEYS | OPTIONAL_COMMON | RULE_KEYS[kind] | OPTIONAL_KEYS.get(kind, set())
        missing = (COMMON_KEYS | RULE_KEYS[kind]) - set(rule)
        extra = set(rule) - allowed
        if missing:
            errors.append(f"{where}: 필수 키 없음 {sorted(missing)}")
        if extra:
            errors.append(f"{where}: 모르는 키 {sorted(extra)}")
        if not isinstance(rule.get("enabled"), bool):
            errors.append(f"{where}: enabled는 true/false")
        anchor = rule.get("anchor")
        if anchor not in columns:
            errors.append(f"{where}: anchor 테이블이 표준에 없다: {anchor!r}")
            continue
        check = _Checker(columns, where, errors)
        if "when" in rule:
            check.cond(rule["when"], anchor)
        if "empty_child" in rule and rule["empty_child"] not in EMPTY_CHILD:
            errors.append(f"{where}: empty_child는 {sorted(EMPTY_CHILD)} 중 하나")
        if kind in ("row_equation", "parent_sum_equation"):
            check.value(rule.get("left"), anchor)
            check.value(rule.get("right"), anchor)
            if kind == "row_equation" and (_has_aggregate(rule.get("left")) or _has_aggregate(rule.get("right"))):
                errors.append(f"{where}: row_equation에는 집계를 쓰지 않는다")
        elif kind == "cumulative_le":
            check.value(rule.get("limit"), anchor)
            total = rule.get("total")
            if not (isinstance(total, dict) and set(total) == {"sum"}):
                errors.append(f"{where}: total은 sum 집계여야 한다")
            else:
                check.value(total, anchor)
        elif kind == "time_order":
            each = rule.get("each", False)
            if not isinstance(each, bool):
                errors.append(f"{where}: each는 true/false")
            if "missing_earlier" in rule and rule["missing_earlier"] not in EMPTY_CHILD:
                errors.append(f"{where}: missing_earlier는 {sorted(EMPTY_CHILD)} 중 하나")
            for side in ("earlier", "later"):
                v = rule.get(side)
                if each and side == "earlier":
                    if not (isinstance(v, dict) and set(v) == {"path", "col"}):
                        errors.append(f"{where}: each=true면 earlier는 {{path, col}}")
                    else:
                        check.path_col(v, anchor, "earlier")
                else:
                    check.value(v, anchor)
        elif kind == "status_requires":
            reqs = rule.get("requires")
            if not isinstance(reqs, list) or not reqs:
                errors.append(f"{where}: requires는 비어 있지 않은 목록")
            else:
                for r in reqs:
                    if not (isinstance(r, dict) and set(r) == {"count", "op", "value"}):
                        errors.append(f"{where}: requires 항목은 {{count, op, value}}")
                        continue
                    if r["op"] not in OPS:
                        errors.append(f"{where}: op는 {sorted(OPS)} 중 하나")
                    if not isinstance(r["value"], int) or isinstance(r["value"], bool):
                        errors.append(f"{where}: value는 정수")
                    check.value({"count": r["count"]}, anchor)
    return errors


def _has_aggregate(expr) -> bool:
    if not isinstance(expr, dict):
        return False
    if set(expr) & (AGGREGATES | {"count"}):
        return True
    return any(_has_aggregate(e) for k in ("add", "sub", "mul") for e in expr.get(k, []) or [])


@dataclass
class _Checker:
    columns: dict[str, list[str]]
    where: str
    errors: list[str]

    def err(self, msg: str) -> None:
        self.errors.append(f"{self.where}: {msg}")

    def column(self, table: str, col) -> None:
        if not isinstance(col, str) or col not in self.columns.get(table, []):
            self.err(f"{table}에 없는 컬럼 {col!r}")

    def cond(self, c, table: str) -> None:
        if not isinstance(c, dict) or not c:
            self.err("조건은 객체")
            return
        if set(c) in ({"col", "in"}, {"col", "not_in"}):
            self.column(table, c["col"])
            values = c.get("in", c.get("not_in"))
            if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
                self.err("in/not_in 값은 비어 있지 않은 문자열 목록")
        elif set(c) == {"present"}:
            self.column(table, c["present"])
        elif set(c) in ({"all"}, {"any"}):
            items = c.get("all", c.get("any"))
            if not isinstance(items, list) or not items:
                self.err("all/any는 비어 있지 않은 목록")
            else:
                for x in items:
                    self.cond(x, table)
        else:
            self.err(f"모르는 조건 {sorted(c)}")

    def path(self, steps, table: str) -> list[str]:
        """경로를 검사하고 지나는 테이블 목록(시작 포함)을 돌려준다."""
        tables = [table]
        if not isinstance(steps, list) or not steps:
            self.err("path는 비어 있지 않은 목록")
            return tables
        for s in steps:
            if not isinstance(s, dict) or not {"to", "on"} <= set(s) or set(s) - {"to", "on", "where"}:
                self.err("경로 단계는 {to, on, where?}")
                return tables
            to = s["to"]
            if to not in self.columns:
                self.err(f"경로 대상 테이블이 표준에 없다: {to!r}")
                return tables
            on = s["on"]
            if not isinstance(on, list) or not on or not all(isinstance(p, list) and len(p) == 2 for p in on):
                self.err("on은 [[현재 컬럼, 대상 컬럼], ...]")
            else:
                for a, b in on:
                    self.column(tables[-1], a)
                    self.column(to, b)
            if "where" in s:
                self.cond(s["where"], to)
            tables.append(to)
        return tables

    def path_col(self, v: dict, table: str, name: str) -> None:
        tables = self.path(v.get("path"), table)
        col = v.get("col")
        if isinstance(col, str) and "." in col:
            t, c = col.split(".", 1)
            if t not in tables[1:]:
                self.err(f"{name}: {t}는 경로에 없는 테이블")
            else:
                self.column(t, c)
        else:
            self.column(tables[-1], col)

    def value(self, v, table: str) -> None:
        if not isinstance(v, dict) or len(v) != 1 or not set(v) <= VALUE_KEYS:
            self.err(f"값 표현식은 {sorted(VALUE_KEYS)} 중 키 하나: {v!r}")
            return
        (k, arg), = v.items()
        if k == "col":
            self.column(table, arg)
        elif k == "const":
            if not isinstance(arg, int) or isinstance(arg, bool):
                self.err("const는 정수")
        elif k in ("add", "mul"):
            if not isinstance(arg, list) or len(arg) < 2:
                self.err(f"{k}는 표현식 2개 이상")
            else:
                for x in arg:
                    self.value(x, table)
        elif k == "sub":
            if not isinstance(arg, list) or len(arg) != 2:
                self.err("sub는 표현식 2개")
            else:
                for x in arg:
                    self.value(x, table)
        elif k == "count":
            if not (isinstance(arg, dict) and set(arg) == {"path"}):
                self.err("count는 {path}")
            else:
                self.path(arg["path"], table)
        else:  # sum·max·min
            if not (isinstance(arg, dict) and set(arg) == {"path", "col"}):
                self.err(f"{k}는 {{path, col}}")
            else:
                self.path_col(arg, table, k)


# ---------------------------------------------------------------- 평가 (M4-2)

class NotComputable(Exception):
    def __init__(self, reason: str, cells: list | None = None):
        super().__init__(reason)
        self.reason = reason
        self.cells = cells or []


Cell = tuple[str, int, str]
Provider = Callable[[str, int, str], "str | None"]


@dataclass
class Data:
    """평가 입력: 표준 테이블 → 행 목록(뷰 레코드 순서, 레코드 = 1부터). 값은 공급자가 정한다."""
    rows: dict[str, list[dict[str, str]]]
    types: dict[str, dict[str, str]]  # 테이블 → 논리 컬럼 → schema 타입
    keys: dict[str, list[str]]  # 테이블 → 기본 키 컬럼
    overrides: dict[Cell, str | None] = field(default_factory=dict)  # 공급자: 셀 → 바뀐 값(None = 논리 null)
    columns: dict[str, set[str]] | None = None  # 테이블 → 실제로 있는 논리 컬럼(없으면 첫 행에서)
    compare: dict[str, dict[str, dict]] = field(default_factory=dict)  # 논리 설정 컬럼 규칙(비교 정규화 strip·casefold)

    def __post_init__(self):
        if self.columns is None:
            self.columns = {t: set(rows[0]) if rows else set() for t, rows in self.rows.items()}

    def value(self, table: str, record: int, col: str) -> str | None:
        key = (table, record, col)
        if key in self.overrides:
            return self.overrides[key]
        return self.rows[table][record - 1].get(col)

    def present(self, table: str) -> bool:
        return table in self.rows

    def has_column(self, table: str, col: str) -> bool:
        return col in (self.columns or {}).get(table, set())


def provider_overrides(cells: dict[Cell, dict], allow: set[Cell] | None = None) -> dict[Cell, str | None]:
    """후보 셀(apply.candidates의 cells) → 공급자 덮어쓰기. allow가 None이면 가상(전체), 아니면 승인(허용 셀만)."""
    return {k: v["after"] for k, v in cells.items() if allow is None or k in allow}


def _parse(kind: str, raw: str | None, cell: Cell):
    if raw is None or raw.strip() == "":
        raise NotComputable("missing_value", [cell])
    if kind == "integer":
        if not _INT.fullmatch(raw):
            raise NotComputable("parse_error", [cell])
        return int(raw)
    if kind == "datetime":
        if not _ISO_TZ.fullmatch(raw):
            raise NotComputable("parse_error", [cell])
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return raw


class _Eval:
    def __init__(self, data: Data):
        self.d = data
        self.index: dict[tuple[str, tuple[str, ...]], dict[tuple, list[int]]] = {}

    def lookup(self, table: str, cols: tuple[str, ...], key: tuple) -> list[int]:
        idx = self.index.get((table, cols))
        if idx is None:
            idx = defaultdict(list)
            for r in range(1, len(self.d.rows[table]) + 1):
                idx[tuple(self.d.value(table, r, c) for c in cols)].append(r)
            self.index[(table, cols)] = idx
        return idx.get(key, [])

    def cond(self, c: dict, table: str, record: int) -> bool:
        if "all" in c:
            return all(self.cond(x, table, record) for x in c["all"])
        if "any" in c:
            return any(self.cond(x, table, record) for x in c["any"])
        if "present" in c:
            v = self.d.value(table, record, c["present"])
            return v is not None and v.strip() != ""
        v = self.d.value(table, record, c["col"])
        if v is not None:  # 범주 비교는 엔진의 허용값 검사와 같은 비교 정규화(설정의 strip·casefold)로 한다
            v = normalize(v, self.d.compare.get(table, {}).get(c["col"], {}))
        return v in c["in"] if "in" in c else v not in c["not_in"]

    def chains(self, steps: list[dict], table: str, record: int, used: set[Cell]) -> list[tuple[tuple[str, int], ...]]:
        """기준 행에서 경로를 따라간 연결(단계별 행) 목록. 표준 기본 키로 붙는 단계가 여러 행이면 ambiguous_join."""
        chains = [((table, record),)]
        for s in steps:
            to = s["to"]
            if not self.d.present(to):
                raise NotComputable("missing_reference")
            on = s["on"]
            if any(not self.d.has_column(to, b) for _, b in on):
                raise NotComputable("missing_column")
            nxt = []
            for chain in chains:
                cur_t, cur_r = chain[-1]
                key = tuple(self.d.value(cur_t, cur_r, a) for a, _ in on)
                for (a, _), v in zip(on, key, strict=True):
                    used.add((cur_t, cur_r, a))
                    if v is None or v.strip() == "":
                        raise NotComputable("missing_value", [(cur_t, cur_r, a)])
                hits = self.lookup(to, tuple(b for _, b in on), key)
                if len(hits) > 1 and [b for _, b in on] == self.d.keys.get(to):
                    raise NotComputable("ambiguous_join", [(to, h, on[0][1]) for h in hits])
                for h in hits:
                    if "where" in s and not self.cond(s["where"], to, h):
                        continue
                    nxt.append(chain + ((to, h),))
            chains = nxt
        return chains

    def column_values(self, spec: dict, table: str, record: int, used: set[Cell]):
        """경로 + 컬럼 → [(값, 셀)], 연결 수."""
        chains = self.chains(spec["path"], table, record, used)
        col = spec["col"]
        if "." in col:  # 경로 앞 단계 테이블의 컬럼(부록 A 보충)
            t, c = col.split(".", 1)
            pick = [next(rr for tt, rr in ch[1:] if tt == t) for ch in chains]
        else:
            t, c = spec["path"][-1]["to"], col
            pick = [ch[-1][1] for ch in chains]
        if chains and not self.d.has_column(t, c):
            raise NotComputable("missing_column")
        out = []
        for r in pick:
            cell = (t, r, c)
            used.add(cell)
            out.append((self.d.value(t, r, c), cell))
        return out, len(chains), t, c

    def value(self, v: dict, table: str, record: int, used: set[Cell]):
        (k, arg), = v.items()
        if k == "col":
            if not self.d.has_column(table, arg):
                raise NotComputable("missing_column")
            cell = (table, record, arg)
            used.add(cell)
            return _parse(self.d.types[table].get(arg, "string"), self.d.value(*cell), cell)
        if k == "const":
            return arg
        if k in ("add", "mul", "sub"):
            vals = [self.value(x, table, record, used) for x in arg]
            if k == "add":
                return sum(vals)
            if k == "sub":
                return vals[0] - vals[1]
            out = 1
            for x in vals:
                out *= x
            return out
        if k == "count":
            return len(self.chains(arg["path"], table, record, used))
        values, n, t, c = self.column_values(arg, table, record, used)
        if n == 0:
            raise _EmptyChild()
        kind = self.d.types[t].get(c, "string")
        parsed = [_parse(kind, raw, cell) for raw, cell in values]
        if k == "sum":
            return sum(parsed)
        return max(parsed) if k == "max" else min(parsed)


class _EmptyChild(Exception):
    pass


def _cmp(op: str, a, b) -> bool:
    return a == b if op == "eq" else (a <= b if op == "le" else a >= b)


def _fmt(x):
    return x.isoformat() if isinstance(x, datetime) else x


def _required_columns(rule: dict) -> set[str]:
    """기준 테이블에서 필요한 컬럼(값·조건·경로 첫 단계 키)."""
    cols: set[str] = set()

    def walk_cond(c):
        if "all" in c or "any" in c:
            for x in c.get("all", c.get("any")):
                walk_cond(x)
        else:
            cols.add(c.get("col", c.get("present")))

    def walk_value(v):
        if not isinstance(v, dict):
            return
        (k, arg), = v.items()
        if k == "col":
            cols.add(arg)
        elif k in ("add", "sub", "mul"):
            for x in arg:
                walk_value(x)
        elif k in ("sum", "max", "min", "count") or "path" in v:
            path = arg["path"] if k in ("sum", "max", "min", "count") else v["path"]
            cols.update(a for a, _ in path[0]["on"])

    if "when" in rule:
        walk_cond(rule["when"])
    for key in ("left", "right", "limit", "total", "later"):
        if key in rule:
            walk_value(rule[key])
    if "earlier" in rule:
        e = rule["earlier"]
        if set(e) == {"path", "col"}:
            cols.update(a for a, _ in e["path"][0]["on"])
        else:
            walk_value(e)
    for r in rule.get("requires", []):
        cols.update(a for a, _ in r["count"]["path"][0]["on"])
    return cols


def _path_tables(rule: dict) -> set[str]:
    out = set()

    def walk(x):
        if isinstance(x, dict):
            if "path" in x and isinstance(x["path"], list):
                out.update(s["to"] for s in x["path"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk({k: v for k, v in rule.items() if k not in ("id", "anchor", "description")})
    return out


def _first_keys(rule: dict) -> set[tuple[str, ...]]:
    """경로 첫 단계에서 기준 행이 쓰는 키 컬럼 묶음(부모 키 중복 판정용)."""
    keys = set()

    def walk(x):
        if isinstance(x, dict):
            if "path" in x and isinstance(x["path"], list) and x["path"]:
                keys.add(tuple(a for a, _ in x["path"][0]["on"]))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk({k: v for k, v in rule.items() if k not in ("id", "anchor", "description", "when")})
    return keys


def evaluate(doc: dict, data: Data, basis: str = "raw") -> dict:
    """규칙 전체 평가. 결과: 규칙별 상태·건수, 위반 묶음, 계산 불가 목록(사유·원인 셀)."""
    ev = _Eval(data)
    out_rules, violations, blocked = [], [], []
    for rule in doc["rules"]:
        rid, anchor = rule["id"], rule["anchor"]
        summary = {"id": rid, "description": rule.get("description", ""), "anchor": anchor, "type": rule["type"],
                   "status": "ran", "reason": None, "counts": {"ok": 0, "violation": 0, "not_applicable": 0,
                                                               "not_computable": {}}}
        out_rules.append(summary)
        if not rule["enabled"]:
            summary.update(status="disabled")
            continue
        if not data.present(anchor):
            summary.update(status="not_run", reason="excluded_target")
            continue
        missing_tables = sorted(t for t in _path_tables(rule) if not data.present(t))
        missing_cols = sorted(c for c in _required_columns(rule) if not data.has_column(anchor, c))
        if missing_cols:
            summary.update(status="not_run", reason="missing_column")
            continue
        if missing_tables:
            summary.update(status="not_run", reason="missing_reference")
            continue
        dup_keys = {}
        for cols in _first_keys(rule):
            seen = defaultdict(int)
            for r in range(1, len(data.rows[anchor]) + 1):
                seen[tuple(data.value(anchor, r, c) for c in cols)] += 1
            if list(cols) == data.keys.get(anchor):
                dup_keys[cols] = {k for k, n in seen.items() if n > 1}
        for r in range(1, len(data.rows[anchor]) + 1):
            used: set[Cell] = set()
            result = _evaluate_row(ev, rule, anchor, r, used, dup_keys)
            status = result["status"]
            if status == "not_computable":
                summary["counts"]["not_computable"][result["reason"]] = \
                    summary["counts"]["not_computable"].get(result["reason"], 0) + 1
                blocked.append({"rule": rid, "table": anchor, "record": r, "reason": result["reason"],
                                "cells": sorted(result.get("cells", []))})
                continue
            summary["counts"][status] += 1
            if status == "violation":
                rows = sorted({(t, rec) for t, rec, _ in used} | {(anchor, r)})
                violations.append({"rule": rid, "table": anchor, "record": r, "basis": basis,
                                   "left": _fmt(result.get("left")), "right": _fmt(result.get("right")),
                                   "detail": result.get("detail"),
                                   "rows": [list(x) for x in rows],
                                   "cells": [list(c) for c in sorted(result.get("value_cells", used))]})
    return {"basis": basis, "rules": out_rules, "violations": violations, "not_computable": blocked}


def _evaluate_row(ev: _Eval, rule: dict, anchor: str, r: int, used: set[Cell], dup_keys: dict) -> dict:
    d = ev.d
    if "when" in rule and not ev.cond(rule["when"], anchor, r):
        return {"status": "not_applicable"}
    for cols, dups in dup_keys.items():
        if tuple(d.value(anchor, r, c) for c in cols) in dups:
            return {"status": "not_computable", "reason": "duplicate_parent",
                    "cells": [(anchor, r, c) for c in cols]}
    kind = rule["type"]
    value_cells: set[Cell] = set()
    try:
        if kind in ("row_equation", "parent_sum_equation"):
            try:
                left = ev.value(rule["left"], anchor, r, value_cells)
                right = ev.value(rule["right"], anchor, r, value_cells)
            except _EmptyChild:
                return _empty(rule, value_cells, used)
            used |= value_cells
            ok = left == right
            return {"status": "ok" if ok else "violation", "left": left, "right": right,
                    "value_cells": value_cells}
        if kind == "cumulative_le":
            limit = ev.value(rule["limit"], anchor, r, value_cells)
            try:
                total = ev.value(rule["total"], anchor, r, value_cells)
            except _EmptyChild:
                return _empty(rule, value_cells, used)
            used |= value_cells
            return {"status": "ok" if total <= limit else "violation", "left": total, "right": limit,
                    "detail": "total_le_limit", "value_cells": value_cells}
        if kind == "time_order":
            later = ev.value(rule["later"], anchor, r, value_cells)
            if rule.get("each"):
                values, n, t, c = ev.column_values(rule["earlier"], anchor, r, value_cells)
                if n == 0:
                    return _empty(rule, value_cells, used)
                used |= value_cells
                bad = []
                for raw, cell in values:
                    if raw is None or raw.strip() == "":
                        policy = rule.get("missing_earlier", "not_computable")
                        if policy == "violation":
                            bad.append(cell)
                            continue
                        if policy == "not_computable":
                            raise NotComputable("missing_value", [cell])
                        continue
                    if _parse(d.types[t].get(c, "string"), raw, cell) > later:
                        bad.append(cell)
                status = "violation" if bad else "ok"
                return {"status": status, "left": None, "right": _fmt(later), "detail": "each",
                        "value_cells": value_cells}
            try:
                earlier = ev.value(rule["earlier"], anchor, r, value_cells)
            except _EmptyChild:
                return _empty(rule, value_cells, used)
            used |= value_cells
            return {"status": "ok" if earlier <= later else "violation", "left": earlier, "right": later,
                    "value_cells": value_cells}
        # status_requires
        details = []
        for req in rule["requires"]:
            n = ev.value({"count": req["count"]}, anchor, r, used)
            details.append({"count": n, "op": req["op"], "value": req["value"]})
        ok = all(_cmp(x["op"], x["count"], x["value"]) for x in details)
        bad = next((x for x in details if not _cmp(x["op"], x["count"], x["value"])), None)
        return {"status": "ok" if ok else "violation", "left": None if bad is None else bad["count"],
                "right": None if bad is None else bad["value"], "detail": details,
                "value_cells": {(anchor, r, c) for c in _required_columns(rule)}}
    except NotComputable as exc:
        return {"status": "not_computable", "reason": exc.reason, "cells": exc.cells}


def _empty(rule: dict, value_cells: set, used: set) -> dict:
    used |= value_cells
    policy = rule.get("empty_child", "not_computable")
    if policy == "not_computable":
        return {"status": "not_computable", "reason": "missing_reference", "cells": []}
    return {"status": policy, "detail": "empty_child", "value_cells": value_cells}
