"""M6-1 형태 서명·컬럼 프로파일 (TD 10-2·10-3, QE 3-3). 직접 만든 값만 쓴다."""

import json
import random

import pytest

from prep.profile import ProfileError, dominant, profile_column, profile_table, signature


@pytest.mark.parametrize("value, expected", [
    ("010-1234-5678", "9-9-9"),
    ("+82 10 1234 5678", "+9 9 9 9"),
    ("홍길동", "가"),
    ("Kim Gildong", "a a"),
    ("A-001", "a-9"),
    ("₩12,000", "₩9,9"),
    ("2026-11-05T09:00:00+09:00", "9-9-9a9:9:9+9:9"),
    ("서울시 강남구 123", "가 가 9"),
    ("--  ", "- "),           # 기호·공백도 같은 문자 연속은 하나
    ("ab12가나!!", "a9가!"),
    ("", ""),
])
def test_signature(value, expected):
    assert signature(value) == expected


def test_signature_distinct_values_can_share():
    """서명은 값을 구별하지 않는다(TD 10-2) — 다른 값이 같은 서명을 가진다."""
    assert signature("010-1111-2222") == signature("02-9-1") == "9-9-9"


def test_dominant_tie_is_deterministic():
    assert dominant({"9": 3, "a": 3, "9-9": 1}) == "9"
    assert dominant({}) is None


def test_missing_values_excluded_from_signatures_and_filled():
    rule = {"type": "string", "missing_values": ["NULL", "없음"]}
    col = profile_column("phone", ["010-1234-5678", "NULL", "", "없음", "01012345678"], rule, "phone")
    assert col["rows"] == 5 and col["filled"] == 2
    assert col["signatures"] == {"9": 1, "9-9-9": 1}
    assert col["logical"] == "phone" and col["norm"] == "phone"


def test_without_rule_only_blank_is_missing_and_no_range():
    col = profile_column("Qty", ["1", " ", "NULL", "3"])
    assert col["filled"] == 3  # 규칙이 없으면 'NULL'도 값으로 본다
    assert col["range"] is None and col["values"] is None and col["logical"] is None


def test_integer_range_counts_unparsed_separately():
    col = profile_column("qty", ["3", "10", "-2", "1,000", "abc", ""], {"type": "integer"})
    assert col["range"] == {"type": "integer", "parsed": 3, "unparsed": 2, "tz_naive": 0, "min": "-2", "max": "10"}


def test_decimal_range_uses_numeric_order():
    col = profile_column("amount", ["9.5", "10.25", "100", "0.50"], {"type": "decimal"})
    assert (col["range"]["min"], col["range"]["max"]) == ("0.5", "100")


def test_date_range():
    col = profile_column("d", ["2026-03-01", "2025-12-31", "2026/01/01"], {"type": "date"})
    assert col["range"]["min"] == "2025-12-31" and col["range"]["max"] == "2026-03-01"
    assert col["range"]["unparsed"] == 1


def test_datetime_range_compares_in_utc_and_skips_naive():
    rule = {"type": "datetime", "formats": ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"]}
    col = profile_column("t", ["2026-01-01T09:00:00+09:00", "2026-01-01T01:00:00+00:00", "2026-01-01 05:00:00"], rule)
    r = col["range"]
    assert r["parsed"] == 2 and r["tz_naive"] == 1 and r["unparsed"] == 0
    assert r["min"] == "2026-01-01T00:00:00+00:00" and r["max"] == "2026-01-01T01:00:00+00:00"


def test_allowed_values_use_compare_normalization():
    rule = {"type": "string", "allowed": ["active", "inactive"], "casefold": True, "strip": True}
    col = profile_column("status", ["Active", " active ", "INACTIVE", "paused"], rule)
    assert col["values"] == ["active", "inactive", "paused"]


def test_table_profile_headers_and_normalized_set():
    headers = ["Order ID", "order_id ", "주문일", ""]
    rows = [{"Order ID": "1", "order_id ": "1", "주문일": "2026-01-01", "": ""}]
    p = profile_table(headers, rows)
    assert p["headers"] == headers  # 원래 헤더는 정규화 전 그대로(QE 3-1)
    assert p["normalized"] == ["orderid", "주문일"]
    assert p["rows"] == 1 and set(p["columns"]) == set(headers)


def test_table_profile_rules_by_raw_header():
    rows = [{"수량": "2", "메모": "x"}, {"수량": "5", "메모": ""}]
    p = profile_table(["수량", "메모"], rows, {"수량": ("quantity", {"type": "integer"})})
    assert p["columns"]["수량"]["logical"] == "quantity" and p["columns"]["수량"]["range"]["max"] == "5"
    assert p["columns"]["메모"]["logical"] is None


def test_table_profile_rejects_duplicate_headers_and_unknown_rules():
    with pytest.raises(ProfileError):
        profile_table(["a", "a"], [])
    with pytest.raises(ProfileError):
        profile_table(["a"], [], {"b": ("x", {})})


def test_profile_is_deterministic_and_row_order_free():
    headers = ["id", "phone"]
    rows = [{"id": str(i), "phone": random.Random(i).choice(["010-1234-5678", "01012345678", "NULL"])}
            for i in range(40)]
    rules = {"phone": ("phone", {"type": "string", "missing_values": ["NULL"]}), "id": ("id", {"type": "integer"})}
    a = json.dumps(profile_table(headers, rows, rules), ensure_ascii=False, sort_keys=True)
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    b = json.dumps(profile_table(headers, shuffled, rules), ensure_ascii=False, sort_keys=True)
    assert a == b


def test_profile_does_not_change_values():
    rows = [{"x": " Active "}]
    before = json.dumps(rows)
    profile_table(["x"], rows, {"x": ("x", {"type": "string", "allowed": ["active"], "strip": True, "casefold": True})})
    assert json.dumps(rows) == before
