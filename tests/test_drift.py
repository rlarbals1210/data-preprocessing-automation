"""M6-3 스키마 변경 감지 엔진 (FS F10 AC-10-1·10-3, TD 10-3, QE 3-3 경계값). 직접 만든 표만 쓴다."""

import copy
import json

import pytest

from prep.drift import candidates, compare, pick_baseline
from prep.profile import profile_table

RULES = {
    "order_id": {"type": "string"},
    "qty": {"type": "integer", "missing_values": ["NULL"]},
    "ordered_on": {"type": "date"},
    "status": {"type": "string", "allowed": ["paid", "shipped"], "casefold": True, "strip": True},
    "phone": {"type": "string", "missing_values": ["NULL"]},
}


def table(headers, columns):
    """columns: 헤더 → 값 목록(같은 길이)."""
    n = len(next(iter(columns.values())))
    return list(headers), [{h: columns[h][i] for h in headers} for i in range(n)]


def baseline(headers, columns, mapping):
    h, rows = table(headers, columns)
    return profile_table(h, rows, {raw: (logical, RULES[logical]) for raw, logical in mapping.items()})


def kinds(result):
    return sorted(c["kind"] for c in result["changes"])


BASE_COLS = {"주문번호": [f"A-{i:03d}" for i in range(10)], "수량": [str(i) for i in range(1, 11)],
             "주문일": [f"2026-01-{i:02d}" for i in range(1, 11)], "상태": ["paid"] * 5 + ["shipped"] * 5}
BASE_MAP = {"주문번호": "order_id", "수량": "qty", "주문일": "ordered_on", "상태": "status"}


@pytest.fixture
def base():
    return baseline(list(BASE_COLS), BASE_COLS, BASE_MAP)


# ---- 후보 양식 ----

def test_candidates_pick_most_overlap():
    ts = [{"id": 1, "std_table": "orders", "headers": ["주문번호", "수량", "주문일"]},
          {"id": 2, "std_table": "orders", "headers": ["주문번호", "메모"]},
          {"id": 3, "std_table": "customers", "headers": ["고객번호"]}]
    c = candidates(["주문번호", "수량", "주문 일"], ts)
    assert c["choice"] == 1 and c["reason"] == "most_overlap"
    assert [r["template_id"] for r in c["ranked"]] == [1, 2]  # 겹침 0은 후보 아님


def test_candidates_tie_and_none_are_not_auto():
    ts = [{"id": 1, "std_table": "orders", "headers": ["a", "b"]}, {"id": 2, "std_table": "orders", "headers": ["a", "c"]}]
    assert candidates(["a", "x"], ts) | {"ranked": None} == {"choice": None, "ranked": None, "reason": "tie"}
    assert candidates(["z"], ts) == {"choice": None, "ranked": [], "reason": "no_overlap"}
    assert candidates(["a"], [])["reason"] == "no_overlap"


def test_pick_baseline_prefers_same_raw_headers_then_order():
    p1 = {"order_no": 1, "profile": {"headers": ["a", "b"]}}
    p2 = {"order_no": 2, "profile": {"headers": ["a", "b", "c"]}}
    assert pick_baseline(["a", "b", "c"], [p1, p2]) is p2["profile"]
    assert pick_baseline(["x"], [p2, p1]) is p1["profile"]
    with pytest.raises(ValueError):
        pick_baseline(["a"], [])


# ---- 변경 없음·값 불변 ----

def test_identical_table_has_no_change(base):
    h, rows = table(list(BASE_COLS), BASE_COLS)
    assert compare(h, rows, base, RULES)["status"] == "no_change"


def test_compare_does_not_change_values(base):
    """AC-10-3: 변경 감지는 어떤 값도 바꾸지 않는다."""
    h, rows = table(list(BASE_COLS), BASE_COLS)
    rows[0]["상태"] = " PAID "
    before_rows, before_base = copy.deepcopy(rows), json.dumps(base, sort_keys=True)
    compare(h, rows, base, RULES)
    assert rows == before_rows and json.dumps(base, sort_keys=True) == before_base


# ---- 헤더 ----

def test_ac_10_1_removed_and_added_both_reported(base):
    cols = {k: v for k, v in BASE_COLS.items() if k != "주문일"} | {"메모": ["x"] * 10}
    h, rows = table(["주문번호", "수량", "상태", "메모"], cols)
    r = compare(h, rows, base, RULES)
    assert {(c["kind"], c.get("column")) for c in r["changes"]} >= {("column_removed", "주문일"),
                                                                     ("column_added", "메모")}
    assert "raw_headers" in kinds(r)


def test_raw_header_change_without_set_change(base):
    """정규화하면 같은 헤더도 원래 헤더 변경으로 남긴다(QE 3-1·3-3)."""
    cols = dict(BASE_COLS)
    cols["주문 번호"] = cols.pop("주문번호")
    h, rows = table(["주문 번호", "수량", "주문일", "상태"], cols)
    r = compare(h, rows, base, RULES)
    assert kinds(r) == ["raw_headers"]
    assert r["changes"][0]["added"] == ["주문 번호"] and r["changes"][0]["removed"] == ["주문번호"]


def test_column_order_only(base):
    h, rows = table(["수량", "주문번호", "주문일", "상태"], BASE_COLS)
    (c,) = compare(h, rows, base, RULES)["changes"]
    assert c["kind"] == "raw_headers" and c["order_only"] is True


# ---- 이름 변경 의심 (QE 3-3 경계값) ----

def rename_case(old_values, new_values):
    b = baseline(["전화"], {"전화": old_values}, {"전화": "phone"})
    h, rows = table(["연락처"], {"연락처": new_values})
    return [c for c in compare(h, rows, b, RULES)["changes"] if c["kind"].startswith("rename")]


PHONES = ["010-1234-5678"] * 10  # 서명 9-9-9


@pytest.mark.parametrize("n_other, expect", [(1, True), (2, True), (3, False)])  # 겹침 90%·80%(같음)·70%
def test_rename_overlap_boundary(n_other, expect):
    new = ["010-1234-5678"] * (10 - n_other) + ["01012345678"] * n_other
    out = rename_case(PHONES, new)
    assert bool(out) is expect
    if expect:
        assert out[0]["kind"] == "rename_suspected" and out[0]["before"] == "전화" and out[0]["after"] == "연락처"


def test_rename_needs_same_dominant_signature():
    assert rename_case(PHONES, ["01012345678"] * 10) == []


@pytest.mark.parametrize("n, expect", [(4, "rename_undetermined"), (5, "rename_suspected")])  # 표본 5개 경계
def test_rename_sample_boundary(n, expect):
    (c,) = rename_case(PHONES[:n], PHONES[:n])
    assert c["kind"] == expect
    if expect == "rename_undetermined":
        assert c["reason"] == "sample_insufficient"


def test_rename_sample_counts_exclude_missing():
    (c,) = rename_case(PHONES[:4] + ["NULL"] * 6, PHONES)  # 결측 표현은 표본에 넣지 않는다
    assert c["kind"] == "rename_undetermined" and c["filled"] == {"before": 4, "after": 10}


# ---- 값 모양 변화 (QE 3-3 경계값) ----

def shape_case(old_values, new_values):
    b = baseline(["전화"], {"전화": old_values}, {"전화": "phone"})
    h, rows = table(["전화"], {"전화": new_values})
    return [c for c in compare(h, rows, b, RULES)["changes"] if c["kind"] in ("value_shape", "shape_undetermined")]


@pytest.mark.parametrize("new, expect", [
    (["010-1234-5678"] * 10 + ["01012345678"], False),       # 새 서명 1/11 ≈ 9.1% (아래)
    (["010-1234-5678"] * 9 + ["01012345678"], True),         # 1/10 = 10% (같음)
    (["010-1234-5678"] * 8 + ["01012345678"] * 2, True),     # 20% (위)
])
def test_new_signature_ratio_boundary(new, expect):
    out = shape_case(PHONES, new)
    assert bool(out) is expect
    if expect:
        assert out[0]["kind"] == "value_shape" and out[0]["new_signatures"] == {"9": new.count("01012345678")}


def test_dominant_signature_change():
    (c,) = shape_case(PHONES, ["01012345678"] * 10)
    assert c["before"] == "9-9-9" and c["after"] == "9"


@pytest.mark.parametrize("n, expect", [(4, ["shape_undetermined"]), (5, [])])
def test_shape_sample_boundary(n, expect):
    assert [c["kind"] for c in shape_case(PHONES, PHONES[:n])] == expect


# ---- 범위·범주 ----

def range_case(column, values):
    cols = dict(BASE_COLS) | {column: values}
    h, rows = table(list(BASE_COLS), cols)
    b = baseline(list(BASE_COLS), BASE_COLS, BASE_MAP)
    return [c for c in compare(h, rows, b, RULES)["changes"] if c["kind"] == "value_range"]


def test_integer_range_within_is_quiet():
    assert range_case("수량", [str(i) for i in range(2, 12)][:9] + ["1"]) == []


def test_integer_range_above_and_unparsed_counted():
    (c,) = range_case("수량", ["1", "2", "3", "4", "5", "6", "7", "8", "999", "열"])
    assert c["above"] is True and c["below"] is False and c["after"]["max"] == "999" and c["unparsed"] == 1


def test_date_range_below():
    vals = [f"2026-01-{i:02d}" for i in range(2, 11)] + ["2025-12-31"]
    (c,) = range_case("주문일", vals)
    assert c["below"] is True and c["before"]["min"] == "2026-01-01" and c["after"]["min"] == "2025-12-31"


def test_missing_values_use_baseline_rule():
    """새 표에도 기준 매핑의 논리 규칙(결측 표현)을 빌려 쓴다 — 'NULL'은 범위·표본에 넣지 않는다."""
    assert range_case("수량", ["NULL"] + [str(i) for i in range(1, 10)]) == []


def test_category_new_values_after_compare_normalization():
    cols = dict(BASE_COLS) | {"상태": [" PAID "] * 4 + ["Shipped", "returned", "returned", "lost"] + ["paid"] * 2}
    h, rows = table(list(BASE_COLS), cols)
    (c,) = [c for c in compare(h, rows, baseline(list(BASE_COLS), BASE_COLS, BASE_MAP), RULES)["changes"]
            if c["kind"] == "category_new"]
    assert c["values"] == ["lost", "returned"] and c["count"] == 2


def test_columns_without_logical_rule_have_no_range_or_category(base):
    cols = dict(BASE_COLS) | {"메모": ["1"] * 10}
    b = profile_table(list(cols), table(list(cols), cols)[1], {})  # 매핑 없는 기준
    h, rows = table(list(cols), dict(cols) | {"메모": ["999"] * 10})
    assert [c for c in compare(h, rows, b, RULES)["changes"] if c["kind"] in ("value_range", "category_new")] == []


def test_result_is_deterministic(base):
    cols = {k: v for k, v in BASE_COLS.items() if k != "주문일"} | {"메모": ["x"] * 10, "기타": ["y"] * 10}
    h, rows = table(["메모", "주문번호", "수량", "상태", "기타"], cols)
    a = json.dumps(compare(h, rows, base, RULES), ensure_ascii=False, sort_keys=True)
    b = json.dumps(compare(h, rows, base, RULES), ensure_ascii=False, sort_keys=True)
    assert a == b
