"""규칙 기반 테이블 분류·컬럼 매핑 (FS F03, QE 3-1·3-2, M1-5).

등급: exact(정규화 후 표준 이름과 같음) · alias(승인된 별칭) — 이 둘만 자동 제안.
similar(difflib ≥ 0.8 후보가 정확히 하나) · shape(값 모양이 표준 컬럼 규칙과 맞음) — 확인 필요.
none — 후보 없음(추가 컬럼). 일대일 위반·정규화 충돌은 등급과 관계없이 확인 필요.
서비스는 정답 매핑을 읽지 않는다. 입력은 헤더·값·스냅샷의 schema/logical 설정/aliases뿐이다.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter, defaultdict

from diagnostics.engine import missing, normalize, parse

SIMILAR_MIN = 0.8
SHAPE_MIN_VALUES = 5
SHAPE_MIN_RATIO = 0.8
TABLE_AUTO_RATIO = 0.7
TABLE_AUTO_GAP = 0.3
_SEPARATORS = re.compile(r"[\s_\-./]")


def norm(name: str) -> str:
    """QE 3-1: NFKC → 소문자 → 앞뒤 공백 제거 → 공백·_·-·.·/ 제거."""
    return _SEPARATORS.sub("", unicodedata.normalize("NFKC", name).lower().strip())


class Catalog:
    """스냅샷의 schema.json·logical-diagnostics.json·aliases.json으로 만든 표준 목록."""

    def __init__(self, schema: dict, logical: dict, aliases: dict):
        self.columns = {t: [f["name"] for f in spec["fields"]] for t, spec in schema["tables"].items()}
        self.rules = {t: spec["columns"] for t, spec in logical["tables"].items()}
        self.required = {t: [c for c, r in cols.items() if r.get("required") is True] for t, cols in
                         self.rules.items()}
        self.table_aliases = {t: {norm(t)} | {norm(a) for a in aliases["tables"].get(t, {}).get("aliases", [])}
                              for t in self.columns}
        self.column_aliases: dict[str, dict[str, str]] = {}
        for t in self.columns:
            amap = {}
            for logical_name, entries in aliases["tables"].get(t, {}).get("columns", {}).items():
                for e in entries:
                    amap[norm(e["alias"])] = logical_name
            self.column_aliases[t] = amap

    def known(self, name: str, table: str) -> str | None:
        """exact 또는 alias로 연결되는 표준 컬럼."""
        n = norm(name)
        for c in self.columns[table]:
            if norm(c) == n:
                return c
        return self.column_aliases[table].get(n)


def classify_table(name: str, headers: list[str], cat: Catalog) -> dict:
    """QE 3-2: 이름 일치면 자동, 아니면 헤더 비율(1위 ≥ 70%, 2위와 30%p 이상)일 때만 자동."""
    stem = norm(re.sub(r"\.[^.]+$", "", name))
    named = [t for t, al in cat.table_aliases.items() if stem in al]
    if len(named) == 1:
        return {"table": named[0], "auto": True, "basis": "name", "scores": {}}
    real = [h for h in headers if h.strip()]
    scores = {}
    if real:
        for t in cat.columns:
            hit = sum(1 for h in real if cat.known(h, t))
            if hit:
                scores[t] = round(hit / len(real), 4)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    if ranked:
        top, gap = ranked[0], ranked[0][1] - (ranked[1][1] if len(ranked) > 1 else 0)
        if top[1] >= TABLE_AUTO_RATIO and gap >= TABLE_AUTO_GAP:
            return {"table": top[0], "auto": True, "basis": "headers", "scores": dict(ranked[:3])}
        return {"table": top[0], "auto": False, "basis": "headers", "scores": dict(ranked[:3])}
    return {"table": None, "auto": False, "basis": None, "scores": {}}


def _shape_ok(values: list[str], rule: dict) -> bool:
    filled = [v for v in values if not missing(v, rule)]
    if len(filled) < SHAPE_MIN_VALUES:
        return False
    ok = 0
    for v in filled:
        try:
            parse(v, rule)
        except ValueError:
            continue
        n = normalize(v, rule)
        if "pattern" in rule and not re.fullmatch(rule["pattern"], n):
            continue
        if "allowed" in rule and n not in {normalize(a, rule) for a in rule["allowed"]}:
            continue
        ok += 1
    return ok / len(filled) >= SHAPE_MIN_RATIO


def _distinctive(rule: dict) -> bool:
    """값 모양만으로 의미를 짐작할 수 있는 규칙(문자열 자유값은 제외)."""
    return rule.get("type", "string") != "string" or "pattern" in rule or "allowed" in rule


def map_columns(table: str, headers: list[str], rows: list[dict[str, str]], cat: Catalog) -> dict:
    cols = cat.columns[table]
    rules = cat.rules.get(table, {})
    result = []
    for h in headers:
        n = norm(h)
        entry = {"original": h, "logical": None, "grade": "none", "candidates": [], "reasons": []}
        exact = next((c for c in cols if norm(c) == n), None)
        alias = cat.column_aliases[table].get(n)
        if exact:
            entry.update(logical=exact, grade="exact")
        elif alias:
            entry.update(logical=alias, grade="alias")
        else:
            names = {c: [norm(c)] + [a for a, lc in cat.column_aliases[table].items() if lc == c] for c in cols}
            sims = {c: max(difflib.SequenceMatcher(None, n, x).ratio() for x in xs) for c, xs in names.items()}
            close = sorted(((c, s) for c, s in sims.items() if s >= SIMILAR_MIN), key=lambda kv: (-kv[1], kv[0]))
            if close:
                entry["candidates"] = [{"logical": c, "grade": "similar", "score": round(s, 3)} for c, s in close]
                if len(close) == 1:
                    entry.update(logical=close[0][0], grade="similar")
                else:
                    entry.update(grade="similar")
                    entry["reasons"].append("similar_tie")
            else:
                values = [r.get(h, "") for r in rows]
                shaped = [c for c, rule in rules.items() if _distinctive(rule) and _shape_ok(values, rule)]
                if shaped:
                    entry["candidates"] = [{"logical": c, "grade": "shape", "score": None} for c in shaped]
                    entry.update(grade="shape", logical=shaped[0] if len(shaped) == 1 else None)
                    if len(shaped) > 1:
                        entry["reasons"].append("shape_tie")
        result.append(entry)
    # 정규화 충돌: 서로 다른 원래 헤더가 정규화하면 같다
    norm_count = Counter(norm(h) for h in headers)
    for e in result:
        if norm_count[norm(e["original"])] > 1:
            e["reasons"].append("normalization_collision")
    # 일대일 위반: 두 원래 컬럼이 같은 표준 컬럼으로
    claimed = defaultdict(list)
    for e in result:
        if e["logical"]:
            claimed[e["logical"]].append(e)
    for es in claimed.values():
        if len(es) > 1:
            for e in es:
                e["reasons"].append("one_to_one")
    for e in result:
        e["needs_confirmation"] = e["grade"] in ("similar", "shape") or bool(e["reasons"])
    mapped = {e["logical"] for e in result if e["logical"]}
    return {
        "columns": result,
        "missing_required": [c for c in cat.required.get(table, []) if c not in mapped],
        "extra": [e["original"] for e in result if e["logical"] is None and not e["needs_confirmation"]],
    }


def propose(name: str, headers: list[str], rows: list[dict[str, str]], cat: Catalog,
            table: str | None = None) -> dict:
    """한 표의 자동 제안. table을 주면 그 테이블로 컬럼만 매핑한다(사용자가 분류를 바꾼 경우)."""
    cls = classify_table(name, headers, cat) if table is None else {
        "table": table, "auto": False, "basis": "user", "scores": {}}
    if cls["table"] is None:
        return {"table": cls, "columns": [{"original": h, "logical": None, "grade": "none", "candidates": [],
                                           "reasons": [], "needs_confirmation": False} for h in headers],
                "missing_required": [], "extra": list(headers), "needs_confirmation": True}
    cols = map_columns(cls["table"], headers, rows, cat)
    needs = not cls["auto"] or any(c["needs_confirmation"] for c in cols["columns"])
    return {"table": cls, **cols, "needs_confirmation": needs}


def validate_final(table: str, columns: dict[str, str | None], headers: list[str], cat: Catalog) -> list[str]:
    """사용자가 확정한 매핑 검사: 표준에 있는 이름·일대일·원래 헤더 일치."""
    errors = []
    if table not in cat.columns:
        errors.append(f"표준에 없는 테이블: {table}")
        return errors
    if set(columns) != set(headers):
        errors.append("매핑의 원래 컬럼 목록이 파일 헤더와 다르다")
    used = [v for v in columns.values() if v is not None]
    for v in used:
        if v not in cat.columns[table]:
            errors.append(f"{table}에 없는 표준 컬럼: {v}")
    dup = sorted(v for v, n in Counter(used).items() if n > 1)
    if dup:
        errors.append(f"두 컬럼이 같은 표준 컬럼으로 간다: {dup}")
    return errors
