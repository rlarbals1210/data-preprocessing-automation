"""형태 서명·컬럼 프로파일 (TD 10-2·10-3, QE 3-3, M6-1).

재사용 규칙(F09)과 스키마 변경 감지(F10)가 함께 쓰는 순수 함수다. 파일·DB를 읽거나 쓰지 않고, 값을 바꾸지 않는다.

- 형태 서명: 숫자 → `9`, 한글 → `가`, 영문 → `a`, 기호·공백은 그대로, 같은 문자 연속은 하나로.
  서로 다른 값이 같은 서명을 가질 수 있으므로 서명만으로 값을 판단하지 않는다(TD 10-2).
- 컬럼 프로파일: 원래 헤더 목록(정규화 전), 정규화 헤더 집합, 컬럼별 서명 분포·비어 있지 않은 값 수,
  논리 타입으로 파싱되는 값의 최소·최대와 파싱 불가 건수, 허용값 규칙 컬럼의 값 집합(QE 3-3).
- 규칙은 원래 헤더별로 받는다(논리 컬럼 규칙). 규칙이 없으면 빈 값만 결측으로 보고 범위·허용값은 재지 않는다.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

from diagnostics.engine import missing, normalize, parse
from prep.mapping import norm

RANGE_TYPES = ("integer", "decimal", "date", "datetime")


class ProfileError(ValueError):
    pass


def _char_class(ch: str) -> str:
    if ch.isdecimal():
        return "9"
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:
        return "가"
    if ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
        return "a"
    return ch


def signature(value: str) -> str:
    """값의 형태 서명. 빈 문자열은 빈 서명이다."""
    out: list[str] = []
    for ch in value:
        c = _char_class(ch)
        if not out or out[-1] != c:
            out.append(c)
    return "".join(out)


def dominant(signatures: dict[str, int]) -> str | None:
    """대표 형태 서명(최빈). 동률이면 서명 문자열 순서로 정해 결정적으로 고른다."""
    if not signatures:
        return None
    return min(signatures.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _range(values: list[str], rule: dict) -> dict:
    kind = rule["type"]
    parsed: list[Decimal | datetime] = []
    unparsed = naive = 0
    for v in values:
        try:
            p = parse(v, rule)
        except ValueError:
            unparsed += 1
            continue
        if kind == "datetime" and isinstance(p, datetime):
            if p.tzinfo is None:
                naive += 1  # 시간대 없는 시각은 비교하지 않는다(FS Q8: 사람 검토)
                continue
            p = p.astimezone(UTC)
        parsed.append(p)
    lo = min(parsed) if parsed else None
    hi = max(parsed) if parsed else None
    return {"type": kind, "parsed": len(parsed), "unparsed": unparsed, "tz_naive": naive,
            "min": _text(lo, kind), "max": _text(hi, kind)}


def _text(value: Decimal | datetime | None, kind: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if kind == "date":
        return value.date().isoformat()
    return value.isoformat()


def profile_column(header: str, values: list[str], rule: dict | None = None, logical: str | None = None) -> dict:
    rule = rule or {}
    filled = [v for v in values if not missing(v, rule)]
    sigs = Counter(signature(v) for v in filled)
    out = {
        "norm": norm(header),
        "logical": logical,
        "rows": len(values),
        "filled": len(filled),
        "signatures": dict(sorted(sigs.items())),
        "range": _range(filled, rule) if rule.get("type") in RANGE_TYPES else None,
        "values": sorted({normalize(v, rule) for v in filled}) if "allowed" in rule else None,
    }
    return out


def profile_table(headers: list[str], rows: list[dict[str, str]],
                  rules: dict[str, tuple[str, dict]] | None = None) -> dict:
    """한 표(업로드 항목)의 프로파일.

    rules: 원래 헤더 → (논리 컬럼, 논리 규칙). 매핑을 아는 경우(기준 저장)나 비교 기준의 매핑을 빌려 올 때 쓴다.
    """
    if len(set(headers)) != len(headers):
        raise ProfileError("원래 헤더가 중복된 표는 프로파일을 만들 수 없다")
    rules = rules or {}
    unknown = sorted(set(rules) - set(headers))
    if unknown:
        raise ProfileError(f"표에 없는 헤더의 규칙: {unknown}")
    columns = {}
    for h in headers:
        logical, rule = rules.get(h, (None, None))
        columns[h] = profile_column(h, [r.get(h, "") for r in rows], rule, logical)
    return {
        "headers": list(headers),
        "normalized": sorted({norm(h) for h in headers if h.strip()}),
        "rows": len(rows),
        "columns": columns,
    }
