"""T-06·T-07 정책 변환 (docs/technical-design.md 6절, 엔진 확장 ②, M1-8)."""

import json
from pathlib import Path

import pytest

from diagnostics.classification import proposal
from diagnostics.engine import ConfigError, diagnose, validate_config
from diagnostics.transforms import TRANSFORMS
from prep.view import Locator

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    "krw_integer": {"type": "integer", "min": 0},
    "krw_integer_symbol": {"type": "integer", "min": 0},
    "kr_mobile": {"type": "string", "pattern": "010-[0-9]{4}-[0-9]{4}"},
    "kr_mobile_intl": {"type": "string", "pattern": "010-[0-9]{4}-[0-9]{4}"},
    "iso_datetime": {"type": "datetime"},
    "iso_datetime_ymd": {"type": "datetime"},
}
# (변환 ID, 입력, 기대 후보 — None이면 "변환하면 안 되는 값")
PAIRS = [
    ("krw_integer", "62,000 원", "62000"), ("krw_integer", "71000원", "71000"),
    ("krw_integer", "₩62,000", None), ("krw_integer", "62,00", None), ("krw_integer", "약 6만원", None),
    ("krw_integer_symbol", "₩62,000", "62000"), ("krw_integer_symbol", "₩ 62000", "62000"),
    ("krw_integer_symbol", "₩62,000원", "62000"), ("krw_integer_symbol", "62,000 원", "62000"),
    ("krw_integer_symbol", "₩", None), ("krw_integer_symbol", "₩6,20", None), ("krw_integer_symbol", "$62,000", None),
    ("krw_integer_symbol", "-₩500", None),  # 음수는 범위 검사(min 0)에서 탈락
    ("kr_mobile", "010 1234 5678", "010-1234-5678"), ("kr_mobile", "(010)1234-5678", "010-1234-5678"),
    ("kr_mobile", "+82 10-1234-5678", None), ("kr_mobile", "010-1234-567", None),
    ("kr_mobile_intl", "+82 10-1234-5678", "010-1234-5678"), ("kr_mobile_intl", "+82-10-1234-5678", "010-1234-5678"),
    ("kr_mobile_intl", "+821012345678", "010-1234-5678"), ("kr_mobile_intl", "010 1234 5678", "010-1234-5678"),
    ("kr_mobile_intl", "+82 10-1234-567", None), ("kr_mobile_intl", "+82 2-1234-5678", None),
    ("kr_mobile_intl", "+1 10-1234-5678", None), ("kr_mobile_intl", "82 10-1234-5678", None),
    ("iso_datetime", "2026-07-15 16:31:00+09:00", "2026-07-15T16:31:00+09:00"),
    ("iso_datetime", "2026/07/15 16:31:00+09:00", "2026-07-15T16:31:00+09:00"),
    ("iso_datetime", "2026.07.15 16:31:00+09:00", None), ("iso_datetime", "2026-07-15 16:31:00", None),
    ("iso_datetime_ymd", "2026.07.15 16:31:00+09:00", "2026-07-15T16:31:00+09:00"),
    ("iso_datetime_ymd", "2026.07.15T16:31:00+0900", "2026-07-15T16:31:00+09:00"),
    ("iso_datetime_ymd", "2026/07/15T16:31:00+09:00", "2026-07-15T16:31:00+09:00"),
    ("iso_datetime_ymd", "2026-07-15 16:31:00+09:00", "2026-07-15T16:31:00+09:00"),
    ("iso_datetime_ymd", "2026.07.15 16:31:00", None), ("iso_datetime_ymd", "07/08/26 09:30", None),
    ("iso_datetime_ymd", "15.07.2026 16:31:00+09:00", None), ("iso_datetime_ymd", "2026-07-15", None),
]


@pytest.mark.parametrize("tid, value, expected", PAIRS, ids=[f"{t}-{v}" for t, v, _ in PAIRS])
def test_transform_pairs(tid, value, expected):
    """T-06(AC-04-5): 변환마다 정상 변환과 변환하면 안 되는 값."""
    assert proposal(value, {**RULES[tid], "standardization": tid}) == expected


def test_every_transform_has_both_kinds_of_test():
    for tid in TRANSFORMS:
        kinds = {exp is None for t, _, exp in PAIRS if t == tid}
        assert kinds == {True, False}, tid


def test_registry_drives_validation():
    validate_config({"tables": {"t": {"columns": {"p": {"type": "string", "standardization": "kr_mobile_intl"}}}}})
    with pytest.raises(ConfigError, match="unsupported"):
        validate_config({"tables": {"t": {"columns": {"p": {"type": "string", "standardization": "nope"}}}}})
    with pytest.raises(ConfigError, match="mismatch"):
        validate_config({"tables": {"t": {"columns": {"p": {"type": "string", "standardization": "krw_integer_symbol"}}}}})


def test_record_matches_registry_and_logical_config_uses_new_ids():
    record = json.loads((ROOT / "config/sets/shop/transforms.json").read_text(encoding="utf-8"))
    assert {t["id"] for t in record["transforms"]} == set(TRANSFORMS)
    policy = json.loads((ROOT / "config/normalization-policy.json").read_text(encoding="utf-8"))
    assert all(TRANSFORMS[t["id"]].policy_key in policy for t in record["transforms"])
    logical = json.loads((ROOT / "config/sets/shop/logical-diagnostics.json").read_text(encoding="utf-8"))
    used = {r["standardization"] for s in logical["tables"].values() for r in s["columns"].values()
            if "standardization" in r}
    assert used == {"kr_mobile_intl", "krw_integer_symbol", "iso_datetime_ymd"}
    original = json.loads((ROOT / "config/shop-diagnostics.json").read_text(encoding="utf-8"))
    used = {r["standardization"] for s in original["tables"].values() for r in s["columns"].values()
            if "standardization" in r}
    assert used == {"kr_mobile", "krw_integer", "iso_datetime"}  # 원 설정은 기존 ID 그대로(T-01)


def test_t07_timezone_less_datetime_goes_to_human_review(tmp_path):
    """T-07(CSV 부분): 시간대 없는 값은 후보 없이 사람 검토, 표시 이유는 '시간대 없음'. 엑셀 부분은 M4에서 완결."""
    (tmp_path / "t.csv").write_text("id,at\n1,2026-07-15 16:31:00\n2,2026.07.15 16:31:00+09:00\n3,07/08/26 09:30\n",
                                    encoding="utf-8")
    config = {"tables": {"t": {"columns": {"id": {}, "at": {"type": "datetime", "standardization": "iso_datetime_ymd"}}}}}
    report = diagnose(tmp_path, config)
    at = {i["record"]: i for i in report["issues"] if i["columns"] == ["at"] and i["code"] == "type"}
    assert at[1]["category"] == "human_review" and at[1].get("suggested_value") is None
    assert at[2]["category"] == "standardization_candidate" and at[2]["suggested_value"] == "2026-07-15T16:31:00+09:00"
    assert at[3]["category"] == "human_review"
    lineage = {"tables": {"t": {"rows": [{"record": r, "item_id": 1, "source_record": r, "line_start": r + 1,
                                          "line_end": r + 1, "extras": {}} for r in (1, 2, 3)],
                                "columns": {"id": {"1": "id"}, "at": {"1": "at"}}, "absent": []}}}
    translated = {i["record"]: i for i in Locator(lineage).translate_report(report)["issues"]
                  if i["columns"] == ["at"] and i["code"] == "type"}
    assert translated[1].get("detail") == "timezone_missing"
    assert "detail" not in translated[3]  # 연도가 앞에 오지 않는 값은 다른 이유(모호한 날짜)
    assert report["summary"]["category_counts"]["human_review"] == 2  # 이중 집계 없음
