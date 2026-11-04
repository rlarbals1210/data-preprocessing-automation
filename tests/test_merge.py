"""M4-6 여러 파일·시트 통합 (FS F02 AC-02-1·02-4, TD 5-3). 개발 입력을 나눈 사본과 합성 xlsx만 쓴다."""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"
LINES = (DEV / "customers.csv").read_text(encoding="utf-8-sig").splitlines()
HEAD = LINES[0]


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "workspace"))


def csv_bytes(lines):
    return ("\n".join(lines) + "\n").encode("utf-8")


def xlsx_bytes(sheets: dict[str, list[str]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, lines in sheets.items():
        ws = wb.create_sheet(name)
        for line in lines:
            ws.append(line.split(","))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run_flow(client, files, edit=None, execute=True):
    r = client.post("/api/runs", files=[("files", (n, d, "application/octet-stream")) for n, d in files],
                    data={"set": "shop", "source": "s"})
    run = r.json()
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"] if not f["read_error"]]
    if edit:
        edit(maps, run)
    c = client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps})
    if c.status_code != 200:
        return run, c
    d = client.post(f"/api/runs/{run['id']}/diagnose").json()
    if not execute:
        return run, d
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    return run, client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]}).json()


def test_two_files_and_two_sheets_into_one_table(client):
    """AC-02-1: 파일 2개(CSV·xlsx)와 시트 2개를 같은 표준 테이블로 합치면 행 수 = 합계, 모든 행이 원래 위치로 추적된다."""
    rows = LINES[1:]
    a, b, c, d = rows[:50], rows[50:100], rows[100:150], rows[150:]
    # 두 번째 CSV는 컬럼 순서를 바꾼다(매핑으로 해결)
    cols = HEAD.split(",")
    order = list(reversed(range(len(cols))))
    reorder = [",".join(cols[i] for i in order)] + [",".join(r.split(",")[i] for i in order) for r in b]
    if any(r.count('"') for r in rows):
        pytest.skip("따옴표 필드가 있으면 단순 분할을 쓰지 않는다")
    files = [("a.csv", csv_bytes([HEAD] + a)), ("b.csv", csv_bytes(reorder)),
             ("고객.xlsx", xlsx_bytes({"1월": [HEAD] + c, "2월": [HEAD] + d}))]
    run, res = run_flow(client, files)
    assert res["status"] in ("completed", "completed_with_issues") and res["rows"] == len(rows)
    rid = run["id"]
    out = client.get(f"/api/runs/{rid}/files/customers.csv").text.splitlines()
    assert len(out) - 1 == len(rows)
    # 결과 첫 행 = a.csv 1행, a 다음은 b.csv(순서를 바꾼 파일)의 1행
    assert out[1].split(",")[0] == a[0].split(",")[0]
    assert out[51].split(",")[0] == b[0].split(",")[0]
    issues = client.get(f"/api/runs/{rid}/remaining", params={"limit": 500}).json()["items"]
    by_file = {(i["source"]["file"], i["source"].get("sheet")) for i in issues}
    assert by_file <= {("a.csv", None), ("b.csv", None), ("고객.xlsx", "1월"), ("고객.xlsx", "2월")}
    changes = client.get(f"/api/runs/{rid}/changes", params={"limit": 500}).json()["items"]
    sheets = {c["source"].get("sheet") for c in changes if c["source"]["file"] == "고객.xlsx"}
    assert sheets == {"1월", "2월"}
    x = next(c for c in changes if c["source"].get("sheet") == "2월")
    assert x["source"]["cells"][x["column"]][1:] == str(x["source"]["record"] + 1)  # 엑셀 셀 좌표


def test_same_pk_across_files_kept_and_reported(client):
    """AC-02-4: 다른 파일의 같은 PK 행은 모두 남고, 병합·삭제 없이 중복 그룹으로 진단된다."""
    first = LINES[1].split(",")
    other = list(first)
    other[1] = "가상고객999"  # 같은 고객번호, 다른 이름
    files = [("a.csv", csv_bytes([HEAD, LINES[1], LINES[2]])), ("b.csv", csv_bytes([HEAD, ",".join(other)]))]
    run, d = run_flow(client, files, execute=False)
    assert d["rows_read"] == 3
    groups = client.get(f"/api/runs/{run['id']}/duplicates").json()["items"]
    key = next(g for g in groups if g["code"] == "unique")
    assert key["kind"] == "conflicting" and {s["file"] for s in key["sources"]} == {"a.csv", "b.csv"}


def test_file_without_required_column_not_merged(client):
    cols = HEAD.split(",")
    keep = [i for i, c in enumerate(cols) if c != "고객명"]
    short = [",".join(cols[i] for i in keep), ",".join(LINES[1].split(",")[i] for i in keep)]
    files = [("a.csv", csv_bytes([HEAD, LINES[2]])), ("b.csv", csv_bytes(short))]
    run, r = run_flow(client, files, execute=False)
    assert r.status_code == 400 and "필수 컬럼" in r.json()["message"] and "b.csv" in r.json()["message"]

    def exclude_b(maps, run):
        next(m for m in maps if m["item_id"] == run["files"][1]["item_id"])["table"] = None
    run, d = run_flow(client, files, edit=exclude_b, execute=False)
    assert d["rows_read"] == 1


def test_unclassified_sheet_not_merged(client):
    data = xlsx_bytes({"customers": [HEAD, LINES[1]], "메모": ["내용,작성자", "참고,누군가"]})

    def unclassify(maps, run):
        memo = next(f for f in run["files"] if f["sheet"] == "메모")
        next(m for m in maps if m["item_id"] == memo["item_id"])["table"] = None
    run, d = run_flow(client, [("파일.xlsx", data)], edit=unclassify, execute=False)
    assert d["rows_read"] == 1 and [t["table"] for t in d["tables"]] == ["customers"]
