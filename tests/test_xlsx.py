"""M4-5 엑셀 읽기 (FS F02 AC-02-2·02-3·02-5, TD 7절, T-07 엑셀 부분).

xlsx fixture는 테스트에서 openpyxl로 만든다(바이너리를 저장소에 두지 않음). 개발 입력·합성 값만 쓴다.
"""

import datetime as dt
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from api.app import create_app
from prep.sheets import cell_text, read_workbook

HEAD = ["고객번호", "고객명", "이메일", "휴대폰", "회원상태", "마케팅동의", "가입일시", "수정일시"]
TZ = "2026-06-01T09:00:00+09:00"


def book(**sheets) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, fill in sheets.items():
        fill(wb.create_sheet(name))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def customers(ws):
    ws.append(HEAD)
    ws.append(["C1", "가상고객001", "c1@example.com", "01012345678", "ACTIVE", "false", TZ, TZ])
    ws.append(["C2", "가상고객002", "c2@example.com", "010-2222-3333", "active", "true",
               dt.datetime(2026, 6, 1, 9, 0), TZ])  # 시간대 없는 날짜 셀
    ws.append(["00123", "가상고객003", None, None, "active", "false", TZ, TZ])


def test_cell_values_and_types():
    """AC-02-3: 텍스트 '00123'은 그대로, 숫자 123에 앞자리 0을 붙이지 않는다. 저장값과 자료형을 함께 둔다."""
    assert cell_text("00123") == ("00123", "text")
    assert cell_text(123) == ("123", "number")
    assert cell_text(123.0) == ("123", "number") and cell_text(1.5) == ("1.5", "number")
    assert cell_text(True) == ("TRUE", "bool") and cell_text(None) == ("", "empty")
    assert cell_text(dt.datetime(2026, 7, 1, 9, 30)) == ("2026-07-01T09:30:00", "datetime")

    def fill(ws):
        ws.append(["id", "qty", "at"])
        ws.append(["00123", 123, dt.datetime(2026, 7, 1, 9, 30)])
        ws.append([None, None, None])  # 중간 빈 행은 레코드로 남는다
        ws.append(["x", 1.5, None])
        ws.append([None, None, None])  # 뒤쪽 빈 행은 버린다
    [s] = read_workbook(book(data=fill))
    assert s["status"] == "ok" and s["headers"] == ["id", "qty", "at"] and len(s["rows"]) == 3
    r = s["rows"][0]
    assert r["values"] == {"id": "00123", "qty": "123", "at": "2026-07-01T09:30:00"}
    assert r["types"] == {"qty": "number", "at": "datetime"} and r["sheet_row"] == 2 and r["line_start"] is None


@pytest.mark.parametrize("name, fill, cell, reason", [
    ("merged", lambda ws: (ws.append(["a", "b"]), ws.append([1, 2]), ws.merge_cells("A2:B2")), "A2:B2", "병합"),
    ("formula", lambda ws: (ws.append(["a", "b"]), ws.append([1, "=A2*2"])), "B2", "수식"),
    ("blank_header", lambda ws: (ws.append(["a", None, "c"]), ws.append([1, 2, 3])), "B1", "빈 머리글"),
    ("dup_header", lambda ws: (ws.append(["a", "a"]), ws.append([1, 2])), "B1", "중복 머리글"),
    ("empty", lambda ws: None, None, "빈 시트"),
])
def test_unsupported_structures(name, fill, cell, reason):
    """AC-02-2: 지원 안 하는 구조는 시트 이름·셀 좌표·이유와 함께 오류, 그 시트는 표로 쓰지 않는다."""
    [s] = read_workbook(book(**{name: fill}))
    assert s["status"] == "error" and s["rows"] == [] and s["headers"] == []
    e = s["errors"][0]
    assert e["sheet"] == name and e["cell"] == cell and reason in e["reason"]


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "workspace"))


def upload(client, name, data):
    r = client.post("/api/runs", files=[("files", (name, data, "application/octet-stream"))],
                    data={"set": "shop", "source": "s"})
    assert r.status_code == 200, r.text
    return r.json()


def test_sheets_become_upload_items(client):
    data = book(customers=customers, categories=lambda ws: (ws.append(["분류번호", "상위분류번호", "분류명", "사용여부"]),
                                                             ws.append(["K1", None, "생활", "TRUE"])),
                memo=lambda ws: (ws.append(["a", "b"]), ws.append([1, "=A2"])))
    run = upload(client, "판매.xlsx", data)
    files = {f["sheet"]: f for f in run["files"]}
    assert set(files) == {"customers", "categories", "memo"}
    assert len({f["sha256"] for f in run["files"]}) == 1 and {f["name"] for f in run["files"]} == {"판매.xlsx"}
    assert files["customers"]["mapping"]["table"] == "customers"  # 시트 이름으로 분류
    assert files["categories"]["rows"] == 1 and files["categories"]["read_options"] == {"format": "xlsx",
                                                                                         "sheet": "categories"}
    memo = files["memo"]
    assert memo["read_error"].startswith("B2: 수식") and memo["read_errors"][0]["sheet"] == "memo"
    assert memo["mapping"]["table"] is None  # 실행 대상에서 빠진다


def test_excel_flow_cell_coordinates_and_timezone(client):
    run = upload(client, "고객.xlsx", book(customers=customers))
    rid = run["id"]
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    assert client.put(f"/api/runs/{rid}/mapping", json={"files": maps}).status_code == 200
    client.post(f"/api/runs/{rid}/diagnose")
    issues = client.get(f"/api/runs/{rid}/issues", params={"limit": 500}).json()["items"]
    joined = [i for i in issues if i["columns"] == ["joined_at"] and i["code"] == "type"]
    # AC-02-5·T-07: 시간대 없는 날짜 셀은 표준화 후보가 아니라 사람 검토, 이유는 시간대 없음
    assert len(joined) == 1 and joined[0]["category"] == "human_review" and joined[0]["detail"] == "timezone_missing"
    src = joined[0]["source"]
    assert src["sheet"] == "customers" and src["sheet_row"] == 3 and src["cells"] == {"joined_at": "G3"}
    assert src["line_start"] is None  # 엑셀에 CSV 줄 번호를 붙이지 않는다
    row = client.get(f"/api/runs/{rid}/source", params={"item_id": src["item_id"], "record": src["record"]}).json()
    g = next(c for c in row["cells"] if c["original"] == "가입일시")
    assert (g["cell"], g["type"], g["value"]) == ("G3", "datetime", "2026-06-01T09:00:00")
    assert row["sheet"] == "customers" and row["sheet_row"] == 3
    p = client.get(f"/api/runs/{rid}/preview").json()
    res = client.post(f"/api/runs/{rid}/execute", json={"approval": p["approval"]}).json()
    assert res["status"] in ("completed", "completed_with_issues")
    out = client.get(f"/api/runs/{rid}/files/customers.csv").text.splitlines()
    assert out[3].startswith("00123,")  # 텍스트 셀의 앞자리 0 유지
    changes = client.get(f"/api/runs/{rid}/files/changes.csv").text.splitlines()
    assert changes[0].endswith(",sheet,cell")
    phone = next(line for line in changes if ",phone," in line)
    assert phone.endswith(",customers,D2") and ",,," in phone  # 줄 번호 칸은 비고, 시트·셀 좌표


def test_broken_workbook_reported(client):
    run = upload(client, "깨짐.xlsx", b"not a zip")
    [f] = run["files"]
    assert f["read_error"].startswith("엑셀 파일을 열 수 없다") and f["mapping"]["table"] is None


def test_only_csv_and_xlsx(client):
    r = client.post("/api/runs", files=[("files", ("a.xls", b"x", "application/octet-stream"))],
                    data={"set": "shop", "source": "s"})
    assert r.status_code == 400 and "xlsx" in r.json()["message"]
