"""스키마 변경 감지 엔진 — 매핑 전 (FS F10, TD 10-3, QE 3-3, M6-3).

순수 함수다. 업로드 원문·기준 프로파일을 읽기만 하고 어떤 값도 바꾸지 않는다(AC-10-3). 변화는 알림이며 오류가 아니다.

1. `candidates`: 정규화 헤더 구성이 정확히 같은 양식이 하나면 그 양식(양식 정의상 같은 단위). 아니면 같은 출처의
   기존 양식 중 원래 헤더(정규화)가 가장 많이 겹치는 양식을 비교 **후보**로 고른다.
   겹침이 없거나 1위가 동률이면 자동으로 정하지 않는다(사용자 선택 또는 "기준 생성", TD 10-3).
2. `compare`: 새 표를 선택한 양식의 기준 프로파일과 비교해 QE 3-3의 변화 6종을 보고한다.
   - 원래 헤더 변경: 원래 헤더 문자열 목록(정규화 전·순서 포함)이 다르면
   - 컬럼 추가·삭제: 정규화 헤더 집합 차이
   - 이름 변경 의심: 사라진 컬럼과 새 컬럼의 대표 서명이 같고 서명 분포 겹침 ≥ 80%. 한쪽이라도 비어 있지 않은 값 < 5면
     판단하지 않고 표본 미달로 표시
   - 값 모양 변화: 대표 서명이 바뀌었거나 이전에 없던 서명의 비율 ≥ 10%. < 5면 표본 미달
   - 숫자·날짜 범위: 논리 타입으로 파싱된 값의 최소·최대가 기준 범위 밖으로 나가면(파싱 불가·시간대 없는 값은 건수만)
   - 범주 새 값: 허용값 규칙 컬럼에서 비교 정규화 후 이전에 없던 값
   비율 경계는 분수로 계산해 부동소수점 오차 없이 "같음"을 포함한다.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from fractions import Fraction

from prep.mapping import norm
from prep.profile import dominant, profile_table

RENAME_MIN_OVERLAP = Fraction(8, 10)
NEW_SHAPE_MIN_RATIO = Fraction(1, 10)
MIN_SAMPLE = 5
NEW_VALUES_SHOWN = 20


def candidates(headers: list[str], templates: list[dict]) -> dict:
    """비교 후보 양식. templates: {id, std_table, headers(정규화 목록)}. 반환: {choice, ranked, reason}.

    정규화 헤더 구성이 정확히 같은 양식이 하나뿐이면 그 양식이다(양식 정의 그대로, reason=same_headers).
    아니면 겹침 수 1위를 후보로 하되, 겹침이 없거나 1위가 동률이면 자동으로 정하지 않는다.
    """
    exact = [t for t in templates if sorted(t["headers"]) == sorted(norm(h) for h in headers if h.strip())]
    mine = {norm(h) for h in headers if h.strip()}
    ranked = sorted(({"template_id": t["id"], "std_table": t["std_table"], "overlap": len(mine & set(t["headers"])),
                      "size": len(set(t["headers"]))} for t in templates),
                    key=lambda r: (-r["overlap"], r["template_id"]))
    ranked = [r for r in ranked if r["overlap"] > 0]
    if not ranked:
        return {"choice": None, "ranked": [], "reason": "no_overlap"}
    if len(exact) == 1:
        return {"choice": exact[0]["id"], "ranked": ranked, "reason": "same_headers"}
    if len(ranked) > 1 and ranked[0]["overlap"] == ranked[1]["overlap"]:
        return {"choice": None, "ranked": ranked, "reason": "tie"}
    return {"choice": ranked[0]["template_id"], "ranked": ranked, "reason": "most_overlap"}


def pick_baseline(headers: list[str], profiles: list[dict]) -> dict:
    """기준 실행에 같은 양식 항목이 여럿이면 원래 헤더가 가장 많이 같은 프로파일, 동률이면 실행 대상 순서가 앞선 것."""
    if not profiles:
        raise ValueError("기준 프로파일이 없다")
    mine = set(headers)
    return min(profiles, key=lambda p: (-len(mine & set(p["profile"]["headers"])), p["order_no"]))["profile"]


def _share(sigs: dict[str, int]) -> dict[str, Fraction]:
    total = sum(sigs.values())
    return {s: Fraction(n, total) for s, n in sigs.items()} if total else {}


def _overlap(a: dict[str, int], b: dict[str, int]) -> Fraction:
    pa, pb = _share(a), _share(b)
    return sum((min(pa[s], pb[s]) for s in pa.keys() & pb.keys()), Fraction(0))


def _ratio_text(x: Fraction) -> str:
    return f"{float(x):.4f}"


def _value(text: str | None, kind: str):
    if text is None:
        return None
    if kind in ("integer", "decimal"):
        return Decimal(text)
    return datetime.fromisoformat(text)


def _by_norm(profile: dict) -> dict[str, tuple[str, dict]]:
    out = {}
    for raw, col in profile["columns"].items():
        if raw.strip():
            out.setdefault(col["norm"], (raw, col))
    return out


def compare(headers: list[str], rows: list[dict[str, str]], baseline: dict, table_rules: dict[str, dict]) -> dict:
    """새 표(원문)를 기준 프로파일과 비교한다.

    table_rules: 기준 양식 표준 테이블의 논리 규칙(논리 컬럼 → 규칙). 새 표의 같은 정규화 헤더에는 기준 매핑의
    논리 규칙을 빌려 써서 결측 표현·타입·허용값을 같은 기준으로 잰다.
    """
    old = _by_norm(baseline)
    rules = {}
    for h in headers:
        hit = old.get(norm(h)) if h.strip() else None
        logical = hit[1]["logical"] if hit else None
        if logical and logical in table_rules:
            rules[h] = (logical, table_rules[logical])
    current = profile_table(headers, rows, rules)
    new = _by_norm(current)
    changes: list[dict] = []

    if list(headers) != list(baseline["headers"]):
        changes.append({"kind": "raw_headers", "before": list(baseline["headers"]), "after": list(headers),
                        "added": [h for h in headers if h not in baseline["headers"]],
                        "removed": [h for h in baseline["headers"] if h not in headers],
                        "order_only": sorted(headers) == sorted(baseline["headers"])})
    removed = sorted(old.keys() - new.keys())
    added = sorted(new.keys() - old.keys())
    for n in removed:
        changes.append({"kind": "column_removed", "column": old[n][0], "norm": n, "logical": old[n][1]["logical"]})
    for n in added:
        changes.append({"kind": "column_added", "column": new[n][0], "norm": n})
    for o in removed:
        for a in added:
            changes.extend(_rename(old[o], new[a]))
    for n in sorted(old.keys() & new.keys()):
        changes.extend(_column_changes(old[n], new[n]))
    return {"status": "changed" if changes else "no_change", "changes": changes, "profile": current}


def _rename(old: tuple[str, dict], new: tuple[str, dict]) -> list[dict]:
    (o_raw, o), (n_raw, n) = old, new
    base = {"kind": "rename_suspected", "before": o_raw, "after": n_raw, "logical": o["logical"]}
    if o["filled"] < MIN_SAMPLE or n["filled"] < MIN_SAMPLE:
        return [base | {"kind": "rename_undetermined", "reason": "sample_insufficient",
                        "filled": {"before": o["filled"], "after": n["filled"]}}]
    if dominant(o["signatures"]) != dominant(n["signatures"]):
        return []
    overlap = _overlap(o["signatures"], n["signatures"])
    if overlap < RENAME_MIN_OVERLAP:
        return []
    return [base | {"dominant": dominant(n["signatures"]), "overlap": _ratio_text(overlap)}]


def _column_changes(old: tuple[str, dict], new: tuple[str, dict]) -> list[dict]:
    (o_raw, o), (n_raw, n) = old, new
    out: list[dict] = []
    at = {"column": n_raw, "baseline_column": o_raw, "logical": o["logical"]}
    if o["filled"] < MIN_SAMPLE or n["filled"] < MIN_SAMPLE:
        out.append(at | {"kind": "shape_undetermined", "reason": "sample_insufficient",
                         "filled": {"before": o["filled"], "after": n["filled"]}})
    else:
        d_old, d_new = dominant(o["signatures"]), dominant(n["signatures"])
        unseen = {s: c for s, c in n["signatures"].items() if s not in o["signatures"]}
        ratio = Fraction(sum(unseen.values()), n["filled"])
        if d_old != d_new or ratio >= NEW_SHAPE_MIN_RATIO:
            out.append(at | {"kind": "value_shape", "before": d_old, "after": d_new,
                             "new_signatures": dict(sorted(unseen.items())), "new_ratio": _ratio_text(ratio)})
    ro, rn = o.get("range"), n.get("range")
    if ro and rn and ro["type"] == rn["type"] and rn["parsed"]:
        kind = ro["type"]
        lo_o, hi_o = _value(ro["min"], kind), _value(ro["max"], kind)
        lo_n, hi_n = _value(rn["min"], kind), _value(rn["max"], kind)
        below = lo_o is not None and lo_n < lo_o
        above = hi_o is not None and hi_n > hi_o
        if below or above or lo_o is None:
            out.append(at | {"kind": "value_range", "type": kind,
                             "before": {"min": ro["min"], "max": ro["max"]},
                             "after": {"min": rn["min"], "max": rn["max"]}, "below": below, "above": above,
                             "unparsed": rn["unparsed"], "tz_naive": rn["tz_naive"]})
    vo, vn = o.get("values"), n.get("values")
    if vo is not None and vn is not None:
        fresh = sorted(set(vn) - set(vo))
        if fresh:
            out.append(at | {"kind": "category_new", "values": fresh[:NEW_VALUES_SHOWN], "count": len(fresh)})
    return out
