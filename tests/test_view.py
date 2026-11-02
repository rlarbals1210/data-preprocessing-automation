"""T-04·T-05 표준화 뷰·역추적 (docs/technical-design.md 5-3절, M1-7). 개발 입력과 직접 만든 fixture만 쓴다."""

import csv
import io
import json
import shutil
from pathlib import Path

import pytest

from diagnostics.engine import diagnose
from prep.guard import Guard
from prep.mapping import Catalog, propose
from prep.plan import build_plan
from prep.view import EXTRAS, Locator, ViewError, ViewInput, build_view, strip_extras

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"
SET = ROOT / "config/sets/shop"
LOGICAL = json.loads((SET / "logical-diagnostics.json").read_text(encoding="utf-8"))
CAT = Catalog(json.loads((ROOT / "schema.json").read_text(encoding="utf-8")), LOGICAL,
              json.loads((SET / "aliases.json").read_text(encoding="utf-8")))
CONFIG = {"version": 1, "tables": {"t": {"columns": {"id": {"required": True}, "name": {}, "memo": {}}}}}


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "workspace"
    (w / "in").mkdir(parents=True)
    return w, Guard([w], project_root=tmp_path / "project")


def put(ws, name, text):
    p = ws[0] / "in" / name
    p.write_text(text, encoding="utf-8")
    return p


def view_rows(view, table):
    with (view.folder / f"{table}.csv").open(encoding="utf-8", newline="") as f:
        return list(csv.reader(f))


def test_rows_differing_only_in_extra_columns_are_not_duplicates(ws):
    """T-04: 표준 컬럼은 같고 추가 컬럼만 다른 두 행은 동일 행 중복이 아니다."""
    p = put(ws, "a.csv", "id,name,비고\n1,가,첫째\n1,가,둘째\n2,나,x\n2,나,x\n")
    inp = ViewInput(1, "t", p, {"id": "id", "name": "name", "비고": None}, 1)
    view = build_view([inp], ws[0] / "view", ws[1])
    report = diagnose(view.folder, CONFIG)
    dup = [i for i in report["issues"] if i["code"] == "duplicate_row"]
    assert {i["record"] for i in dup} == {3, 4}  # 2·나·x만 진짜 중복
    # 대조: 보조 컬럼이 없으면 1·가 두 행도 중복으로 묶인다
    plain = ws[0] / "plain"
    plain.mkdir()
    (plain / "t.csv").write_text("id,name\n1,가\n1,가\n2,나\n2,나\n", encoding="utf-8")
    assert len([i for i in diagnose(plain, CONFIG)["issues"] if i["code"] == "duplicate_row"]) == 4
    loc = Locator(view.lineage).translate_report(report)
    assert all(EXTRAS not in i["columns"] for i in loc["issues"])
    assert all(EXTRAS not in t["profiles"] for t in loc["tables"].values())
    assert view.lineage["tables"]["t"]["rows"][0]["extras"] == {"비고": "첫째"}


def test_same_name_files_integrated_without_loss(ws):
    """T-04: 같은 이름 파일 두 개(다른 업로드 항목)를 합쳐도 값 손실·오연결이 없다."""
    a = put(ws, "a1.csv", "id,name\n1,가\n2,나\n")
    b = put(ws, "a2.csv", "name,id\n다,3\n")
    view = build_view([ViewInput(7, "t", b, {"name": "name", "id": "id"}, 2, name="orders.csv"),
                       ViewInput(5, "t", a, {"id": "id", "name": "name"}, 1, name="orders.csv")],
                      ws[0] / "view", ws[1])
    assert view_rows(view, "t") == [["id", "name"], ["1", "가"], ["2", "나"], ["3", "다"]]
    loc = Locator(view.lineage)
    assert [(loc.source("t", r)["item_id"], loc.source("t", r)["record"]) for r in (1, 2, 3)] == [(5, 1), (5, 2),
                                                                                                    (7, 1)]
    assert loc.source("t", 3, ["name"])["columns"] == {"name": "name"}


def test_multiline_fields_keep_physical_lines(ws):
    p = put(ws, "m.csv", 'id,name\n1,"여러\n줄"\n2,나\n')
    view = build_view([ViewInput(1, "t", p, {"id": "id", "name": "name"}, 1)], ws[0] / "view", ws[1])
    loc = Locator(view.lineage)
    assert (loc.source("t", 1)["line_start"], loc.source("t", 1)["line_end"]) == (2, 3)
    assert (loc.source("t", 2)["line_start"], loc.source("t", 2)["line_end"]) == (4, 4)
    assert view_rows(view, "t")[1] == ["1", "여러\n줄"]


def test_different_extra_columns_per_file(ws):
    """T-04: 파일마다 다른 추가 컬럼 — 원문은 역추적 표에, '없음'과 빈 값은 구분된다."""
    a = put(ws, "a.csv", "id,name,x\n1,가,\n")
    b = put(ws, "b.csv", "id,name,y\n1,가,\n")
    view = build_view([ViewInput(1, "t", a, {"id": "id", "name": "name", "x": None}, 1),
                       ViewInput(2, "t", b, {"id": "id", "name": "name", "y": None}, 2)], ws[0] / "view", ws[1])
    rows = view_rows(view, "t")
    assert rows[0] == ["id", "name", EXTRAS] and rows[1][2] != rows[2][2]  # x 빈 값 ≠ y 빈 값
    assert view.lineage["tables"]["t"]["extra_columns"] == ["x", "y"]
    assert [r["extras"] for r in view.lineage["tables"]["t"]["rows"]] == [{"x": ""}, {"y": ""}]


def test_file_without_required_column_not_integrated(ws):
    """T-04: 한 파일에만 필수 컬럼이 없으면 합치지 않는다(합집합 헤더로 숨기지 않음)."""
    a = put(ws, "a.csv", "id,name\n1,가\n")
    b = put(ws, "b.csv", "name\n나\n")
    with pytest.raises(ViewError, match="필수 컬럼"):
        build_view([ViewInput(1, "t", a, {"id": "id", "name": "name"}, 1),
                    ViewInput(2, "t", b, {"name": "name"}, 2)], ws[0] / "view", ws[1], required={"t": ["id"]})


def test_optional_column_missing_marked_absent(ws):
    a = put(ws, "a.csv", "id,name,memo\n1,가,메모\n")
    b = put(ws, "b.csv", "id,name\n2,나\n")
    view = build_view([ViewInput(1, "t", a, {"id": "id", "name": "name", "memo": "memo"}, 1),
                       ViewInput(2, "t", b, {"id": "id", "name": "name"}, 2)], ws[0] / "view", ws[1],
                      required={"t": ["id"]})
    assert view_rows(view, "t")[2] == ["2", "나", ""]
    loc = Locator(view.lineage)
    assert loc.source("t", 2, ["memo"]) == {"item_id": 2, "record": 1, "line_start": 2, "line_end": 2,
                                            "columns": {"memo": None}, "absent": ["memo"]}
    assert loc.source("t", 1, ["memo"])["absent"] == []


def test_view_hash_and_guards(ws):
    p = put(ws, "a.csv", "id,name\n1,가\n")
    inp = ViewInput(1, "t", p, {"id": "id", "name": "name"}, 1)
    a = build_view([inp], ws[0] / "v1", ws[1])
    b = build_view([inp], ws[0] / "v2", ws[1])
    assert a.hash == b.hash
    c = build_view([ViewInput(1, "t", p, {"id": "id", "name": None}, 1)], ws[0] / "v3", ws[1])
    assert c.hash != a.hash
    with pytest.raises(ViewError):
        build_view([inp], ws[0] / "v1", ws[1])  # 비어 있지 않은 폴더
    with pytest.raises(ViewError):
        build_view([ViewInput(1, "t", p, {"id": "id"}, 1)], ws[0] / "v4", ws[1])  # 헤더 불일치
    with pytest.raises(ViewError):
        build_view([ViewInput(1, "t", p, {"id": "id", "name": "id"}, 1)], ws[0] / "v5", ws[1])
    outside = ws[0].parent / "outside.csv"
    outside.write_text("id\n1\n")
    with pytest.raises(PermissionError):
        build_view([ViewInput(1, "t", outside, {"id": "id"}, 1)], ws[0] / "v6", ws[1])


def test_strip_extras():
    assert strip_extras(f"id,{EXTRAS},name\n1,abc,가\n") == "id,name\n1,가\n"
    assert strip_extras("id\n1\n") == "id\n1\n"


def _dev_view(ws, folder, files):
    """개발 입력(또는 fixture)을 매핑 제안 → 뷰 → 계획 → 진단으로 돌려 원본 위치 기준 결과를 낸다."""
    inputs, present = [], {}
    for order, (item_id, path) in enumerate(files, start=1):
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        p = propose(path.name, rows[0], [dict(zip(rows[0], r, strict=True)) for r in rows[1:]], CAT,
                    table=path.stem.split("__")[0])
        cols = {c["original"]: c["logical"] for c in p["columns"]}
        inputs.append(ViewInput(item_id, path.stem.split("__")[0], path, cols, order, name=path.name))
        present[path.stem.split("__")[0]] = {v for v in cols.values() if v}
    view = build_view(inputs, ws[0] / folder, ws[1])
    plan = build_plan(LOGICAL, present)
    report = Locator(view.lineage).translate_report(diagnose(view.folder, plan["config"]))
    return report, plan


def english_copy(ws, table):
    """개발 입력의 헤더를 논리 이름의 영문 표기 변형으로 바꾸고 컬럼 순서를 뒤집은 fixture."""
    hmap = json.loads((SET / "legacy-header-map.json").read_text(encoding="utf-8"))["tables"][table]["columns"]
    with (DEV / f"{table}.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    order = list(reversed(range(len(rows[0]))))
    styles = [str.upper, lambda s: s.replace("_", " ").title(), lambda s: s.replace("_", "-")]
    head = [styles[i % 3](hmap[rows[0][i]]) for i in order]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([head] + [[r[i] for i in order] for r in rows[1:]])
    p = ws[0] / "in" / f"{table}__en.csv"
    p.write_text(buf.getvalue(), encoding="utf-8")
    return p


def test_t05_korean_and_english_fixture_same_results(ws):
    """T-05(AC-04-1·04-2): 한글 헤더 입력과 영문 표기·다른 순서 fixture의
    (분류, 규칙 코드, 원본 위치, 논리 컬럼) 집합과 rule_execution이 같다."""
    tables = ["customers", "orders", "order_items", "product_variants"]
    ko_files, en_files = [], []
    for i, t in enumerate(tables, start=1):
        src = ws[0] / "in" / f"{t}.csv"
        shutil.copy(DEV / f"{t}.csv", src)
        ko_files.append((i, src))
        en_files.append((i, english_copy(ws, t)))
    ko, ko_plan = _dev_view(ws, "ko", ko_files)
    en, en_plan = _dev_view(ws, "en", en_files)

    def key(report):
        return sorted(json.dumps([i["category"], i["code"], i["table"], i["source"]["item_id"], i["source"]["record"],
                                  sorted(i["columns"])], ensure_ascii=False) for i in report["issues"])

    assert key(ko) == key(en)
    assert ko["summary"]["category_counts"] == en["summary"]["category_counts"]
    strip = lambda r: sorted(json.dumps(x, sort_keys=True) for x in r["rule_execution"])  # noqa: E731
    assert strip(ko) == strip(en)
    assert ko_plan["hash"] == en_plan["hash"]
