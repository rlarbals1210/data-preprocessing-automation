"""M1-5 규칙 기반 테이블 분류·컬럼 매핑 (FS F03 AC-03-1, QE 3-1·3-2). 개발 입력과 직접 만든 fixture만 쓴다."""

import csv
import json
from pathlib import Path

import pytest

from prep.mapping import Catalog, classify_table, map_columns, norm, propose, validate_final
from tools import build_aliases

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"
SET = ROOT / "config/sets/shop"
CAT = Catalog(json.loads((ROOT / "schema.json").read_text(encoding="utf-8")),
              json.loads((SET / "logical-diagnostics.json").read_text(encoding="utf-8")),
              json.loads((SET / "aliases.json").read_text(encoding="utf-8")))


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], [dict(zip(rows[0], r, strict=True)) for r in rows[1:]]


def test_aliases_file_current():
    assert build_aliases.main(["--check"]) == 0


@pytest.mark.parametrize("raw, expected", [("Order ID", "orderid"), (" order_id ", "orderid"), ("ORDER-ID", "orderid"),
                                           ("order.id/x", "orderidx"), ("ＯｒｄｅｒＩＤ", "orderid"), ("주문 번호", "주문번호")])
def test_norm(raw, expected):
    assert norm(raw) == expected


def test_development_files_map_automatically():
    """개발용 한글 헤더 13개 파일은 이름으로 테이블이, 별칭으로 모든 컬럼이 자동 제안된다."""
    for path in sorted(DEV.glob("*.csv")):
        headers, rows = read(path)
        p = propose(path.name, headers, rows, CAT)
        assert p["table"] == {"table": path.stem, "auto": True, "basis": "name", "scores": {}}
        assert all(c["grade"] == "alias" for c in p["columns"]), path.name
        assert not p["needs_confirmation"] and not p["missing_required"] and not p["extra"]


def english_fixture():
    """AC-03-1용 개발 fixture: 개발 orders의 헤더를 영문 표기 변형으로 바꾸고 컬럼 순서를 섞는다."""
    headers, rows = read(DEV / "orders.csv")
    hmap = json.loads((SET / "legacy-header-map.json").read_text(encoding="utf-8"))["tables"]["orders"]["columns"]
    styles = [lambda s: s.replace("_", " ").title(), lambda s: s.upper(), lambda s: s.replace("_", "-"),
              lambda s: "".join(w.capitalize() if i else w for i, w in enumerate(s.split("_")))]
    english = {h: styles[i % 4](hmap[h]) for i, h in enumerate(headers)}
    order = list(reversed(headers))
    return [english[h] for h in order], [{english[h]: r[h] for h in order} for r in rows], hmap, english


def test_english_alias_fixture_maps_to_same_columns():
    """AC-03-1: 영문 표기·순서 변경 fixture와 한글 헤더 파일이 같은 표준 컬럼으로 연결된다."""
    headers, rows, hmap, english = english_fixture()
    p = propose("주문내역_영문.csv", headers, rows, CAT)
    assert p["table"]["table"] == "orders" and p["table"]["auto"] and p["table"]["basis"] == "headers"
    got = {c["original"]: c["logical"] for c in p["columns"]}
    assert got == {english[k]: v for k, v in hmap.items()}
    assert all(c["grade"] == "exact" for c in p["columns"]) and not p["needs_confirmation"]
    ko_headers, ko_rows = read(DEV / "orders.csv")
    ko = propose("orders.csv", ko_headers, ko_rows, CAT)
    assert {c["logical"] for c in ko["columns"]} == set(got.values())


def test_normalization_collision_needs_confirmation():
    p = map_columns("orders", ["Order ID", "order_id", "고객번호"], [], CAT)["columns"]
    assert [c["needs_confirmation"] for c in p] == [True, True, False]
    assert "normalization_collision" in p[0]["reasons"] and "one_to_one" in p[0]["reasons"]


def test_one_to_one_violation_even_for_alias_and_exact():
    p = map_columns("orders", ["주문번호", "order_id"], [], CAT)["columns"]
    assert [c["grade"] for c in p] == ["alias", "exact"]
    assert all("one_to_one" in c["reasons"] and c["needs_confirmation"] for c in p)


def test_similar_unique_and_tie():
    p = map_columns("orders", ["order_chanel"], [], CAT)["columns"][0]
    assert p["grade"] == "similar" and p["logical"] == "order_channel" and p["needs_confirmation"]
    cat = Catalog({"tables": {"t": {"fields": [{"name": "start_at"}, {"name": "stat_at"}]}}},
                  {"tables": {"t": {"columns": {}}}}, {"tables": {}})
    tie = map_columns("t", ["star_at"], [], cat)["columns"][0]
    assert tie["grade"] == "similar" and tie["logical"] is None and "similar_tie" in tie["reasons"]
    assert {c["logical"] for c in tie["candidates"]} == {"start_at", "stat_at"}


def test_shape_candidate_needs_confirmation():
    rows = [{"연락처X": f"010-1234-{i:04d}"} for i in range(6)]
    p = map_columns("orders", ["연락처X"], rows, CAT)["columns"][0]
    assert p["grade"] == "shape" and p["logical"] == "recipient_phone" and p["needs_confirmation"]
    few = map_columns("orders", ["연락처X"], rows[:4], CAT)["columns"][0]  # 표본 미달
    assert few["grade"] == "none" and not few["needs_confirmation"]


def test_extra_and_missing_required():
    headers, rows = read(DEV / "orders.csv")
    keep = [h for h in headers if h != "주문일시"] + ["비고"]
    out = map_columns("orders", keep, [dict(r, 비고="") for r in rows], CAT)
    assert out["extra"] == ["비고"] and out["missing_required"] == ["ordered_at"]


def test_table_classification_ambiguous_and_empty():
    tie = classify_table("x.csv", ["created_at", "updated_at"], CAT)  # 여러 테이블이 100%로 동률
    assert tie["auto"] is False and len(tie["scores"]) >= 2
    assert classify_table("x.csv", ["order_id", "customer_id"], CAT)["table"] == "orders"
    assert classify_table("x.csv", [], CAT)["table"] is None
    assert classify_table("x.csv", ["전혀", "모름"], CAT)["table"] is None
    p = propose("x.csv", ["전혀"], [], CAT)
    assert p["needs_confirmation"] and p["table"]["table"] is None


def test_user_table_override():
    headers, rows = read(DEV / "returns.csv")
    p = propose("returns.csv", headers, rows, CAT, table="refunds")
    assert p["table"] == {"table": "refunds", "auto": False, "basis": "user", "scores": {}}
    assert p["needs_confirmation"]


def test_validate_final():
    headers = ["주문번호", "비고"]
    assert validate_final("orders", {"주문번호": "order_id", "비고": None}, headers, CAT) == []
    assert validate_final("nope", {}, headers, CAT)
    assert any("없는 표준 컬럼" in e for e in validate_final("orders", {"주문번호": "x", "비고": None}, headers, CAT))
    assert any("같은 표준" in e for e in validate_final("orders", {"주문번호": "order_id", "비고": "order_id"},
                                                     headers, CAT))
    assert any("헤더와 다르다" in e for e in validate_final("orders", {"주문번호": "order_id"}, headers, CAT))
