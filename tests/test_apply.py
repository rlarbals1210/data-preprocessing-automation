"""T-08 적용 계획과 엔진 확장 ③ (docs/technical-design.md 8-2~8-4절, M1-9)."""

import json
from pathlib import Path

import pytest

from diagnostics.cleaning import clean
from diagnostics.engine import diagnose
from prep.apply import ApplyError, build_apply_plan, candidates, group_id, groups, verify_plan

CONFIG = {"tables": {"t": {"columns": {
    "id": {"required": True},
    "price": {"type": "integer", "min": 0, "standardization": "krw_integer_symbol"},
    "status": {"allowed": ["paid", "cancelled"], "casefold": True},
    "phone": {"pattern": "010-[0-9]{4}-[0-9]{4}", "standardization": "kr_mobile_intl"},
}, "unique": [["id"]]}}}
CSV = ("id,price,status,phone\n"
       "1,\"1,000\",PAID,010 1234 5678\n"
       "2,₩2000,paid,+82 10-1111-2222\n"
       "2,3000원,CANCELLED,010-3333-4444\n"  # id 중복 → id 셀은 위반이지만 다른 셀은 보류 아님
       "4,\"5,000\",unknown,010 5555 6666\n")  # status 허용값 밖(위반) — 같은 셀이 아니면 보류 아님


@pytest.fixture
def data(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "t.csv").write_text(CSV, encoding="utf-8")
    return src, diagnose(src, CONFIG)


def test_candidates_and_held(data):
    _, report = data
    cells, held = candidates(report)
    assert ("t", 1, "price") in cells and cells[("t", 1, "price")]["after"] == "1000"
    assert ("t", 2, "phone") in cells and cells[("t", 2, "phone")]["after"] == "010-1111-2222"
    assert ("t", 1, "status") in cells  # PAID → paid
    assert held == {}  # 같은 셀에 위반이 겹치지 않는다
    cells2, held2 = candidates(report, extra_blocked=[("t", 1, "price")])
    assert ("t", 1, "price") in held2 and ("t", 1, "price") not in cells2


def test_default_plan_allows_all_unheld(data):
    _, report = data
    plan = build_apply_plan(report, "v1")
    cells, _ = candidates(report)
    assert plan["counts"]["allowed"] == len(cells) and plan["counts"]["not_approved"] == 0
    verify_plan(plan, report, "v1")


def test_partial_group_and_excluded_cells(data):
    _, report = data
    price = group_id("t", "price", "type")
    plan = build_apply_plan(report, "v1", {"view_hash": "v1", "approved_groups": [price],
                                           "excluded_cells": [["t", 4, "price"]]})
    assert {tuple(k) for k in plan["allow"]} == {("t", 1, "price"), ("t", 2, "price"), ("t", 3, "price")}


@pytest.mark.parametrize(
    "selection, message",
    [
        ({"view_hash": "old"}, "이전 진단"),
        ({"approved_groups": ["t.nope.type"]}, "규칙 묶음"),
        ({"excluded_cells": [["t", 99, "price"]]}, "후보가 아닌"),
        ({"excluded_cells": [["t", 1, "id"]]}, "후보가 아닌"),
        ({"excluded_cells": ["t.1.price"]}, "형식"),
    ],
)
def test_bad_selections_rejected(data, selection, message):
    """T-08: 이전 뷰·없는 묶음·후보가 아닌 셀은 거부."""
    _, report = data
    with pytest.raises(ApplyError, match=message):
        build_apply_plan(report, "v1", selection)


def test_held_cell_selection_rejected(data):
    _, report = data
    with pytest.raises(ApplyError, match="보류"):
        build_apply_plan(report, "v1", {"excluded_cells": [["t", 1, "price"]]}, extra_blocked=[("t", 1, "price")])


def test_verify_detects_stale_or_tampered(data):
    _, report = data
    plan = build_apply_plan(report, "v1")
    with pytest.raises(ApplyError, match="뷰"):
        verify_plan(plan, report, "v2")
    with pytest.raises(ApplyError, match="후보"):
        verify_plan(plan, report, "v1", extra_blocked=[("t", 1, "price")])
    bad = json.loads(json.dumps(plan))
    bad["allow"].append(["t", 1, "id"])
    with pytest.raises(ApplyError):
        verify_plan(bad, report, "v1")


def test_cell_in_two_groups_needs_both():
    def issue(code):
        return {"table": "t", "record": 1, "columns": ["status"], "values": {"status": " PAID"}, "code": code,
                "category": "standardization_candidate", "suggested_value": "paid"}
    report = {"issues": [issue("outer_whitespace"), issue("comparison_normalization")]}
    cells, _ = candidates(report)
    codes = cells[("t", 1, "status")]["codes"]
    assert codes == ["outer_whitespace", "comparison_normalization"]
    one = build_apply_plan(report, "v", {"approved_groups": [group_id("t", "status", codes[0])]})
    assert one["allow"] == []
    both = build_apply_plan(report, "v", {"approved_groups": [group_id("t", "status", c) for c in codes]})
    assert both["allow"] == [["t", 1, "status"]]
    assert {g["id"] for g in groups(cells, {})} == {group_id("t", "status", c) for c in codes}
    conflict = {"issues": [issue("outer_whitespace"), issue("comparison_normalization") | {"suggested_value": "x"}]}
    with pytest.raises(ApplyError, match="서로 다른 제안"):
        candidates(conflict)


# ---- 엔진 확장 ③ ----
def test_clean_allow_none_is_default(data, tmp_path):
    src, report = data
    out = clean(src, CONFIG, tmp_path / "o1")
    assert out["changed_cells"] == len(candidates(report)[0])
    assert "not_approved_cells" not in out and not (tmp_path / "o1/not-approved.json").exists()


def test_clean_empty_allow_changes_nothing(data, tmp_path):
    src, report = data
    out = clean(src, CONFIG, tmp_path / "o2", allow=set())
    assert out["changed_cells"] == 0 and out["not_approved_cells"] == len(candidates(report)[0])
    assert (tmp_path / "o2/data/t.csv").read_text(encoding="utf-8").splitlines()[1] == '1,"1,000",PAID,010 1234 5678'


def test_clean_partial_allow_matches_plan(data, tmp_path):
    """AC-06-5: 미리보기(적용 계획)의 허용 건수 = 실행 변경 건수."""
    src, report = data
    plan = build_apply_plan(report, "v", {"approved_groups": [group_id("t", "price", "type")]})
    out = clean(src, CONFIG, tmp_path / "o3", allow={tuple(k) for k in plan["allow"]})
    changes = json.loads((tmp_path / "o3/changes.json").read_text(encoding="utf-8"))
    assert out["changed_cells"] == plan["counts"]["allowed"] == len(changes)
    assert {(c["table"], c["record"], c["column"]) for c in changes} == {tuple(k) for k in plan["allow"]}
    assert len(json.loads((tmp_path / "o3/not-approved.json").read_text())) == plan["counts"]["not_approved"]


@pytest.mark.parametrize("cell", [("t", 1, "id"), ("t", 99, "price")])
def test_clean_rejects_non_candidates(data, tmp_path, cell):
    src, _ = data
    with pytest.raises(ValueError, match="not current unheld candidates"):
        clean(src, CONFIG, tmp_path / "o4", allow={cell})
    assert not Path(tmp_path / "o4").exists()


def test_clean_rejects_held_cell_and_honours_extra_blocked(data, tmp_path):
    src, _ = data
    with pytest.raises(ValueError):
        clean(src, CONFIG, tmp_path / "o5", allow={("t", 1, "price")}, extra_blocked=[("t", 1, "price")])
    out = clean(src, CONFIG, tmp_path / "o6", extra_blocked=[("t", 1, "price")])
    assert out["extra_blocked_cells"] == 1
    assert (tmp_path / "o6/data/t.csv").read_text(encoding="utf-8").splitlines()[1].startswith('1,"1,000"')
