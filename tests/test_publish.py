"""T-09 결과 발행 (docs/technical-design.md 8-5절, FS F07·7절, M1-10c). 개발 입력만 쓴다."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from prep import publish

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "workspace"))


def flow(client, files, edit=None):
    r = client.post("/api/runs", files=[("files", (n, d, "text/csv")) for n, d in files],
                    data={"set": "shop", "source": "s"})
    run = r.json()
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"] if not f["read_error"]]
    if edit:
        edit(maps)
    assert client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps}).status_code == 200
    client.post(f"/api/runs/{run['id']}/diagnose")
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    return run, client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]}).json()


def db(tmp_path):
    c = sqlite3.connect(tmp_path / "workspace/app.db")
    c.row_factory = sqlite3.Row
    return c


def dev(name):
    return (name, (DEV / name).read_bytes())


def test_publish_records_result_version_and_files(client, tmp_path):
    run, res = flow(client, [dev("orders.csv"), dev("customers.csv")])
    assert res["result_version"] >= 1 and res["status"] in ("completed", "completed_with_issues")
    rv = db(tmp_path).execute("SELECT * FROM result_version").fetchall()
    assert len(rv) == 1 and rv[0]["kind"] == "cleaned"
    full = client.get(f"/api/runs/{run['id']}/result").json()
    assert full["published"] and {f["name"] for f in full["files"]} == {"orders.csv", "customers.csv"}
    body = client.get(f"/api/runs/{run['id']}/files/changes.csv").text
    assert body.splitlines()[0].startswith("table,file,source_record,line_start")
    assert "orders.csv" in body.splitlines()[1] or "customers.csv" in body.splitlines()[1]
    events = [r["kind"] for r in db(tmp_path).execute("SELECT kind FROM event WHERE subject_type = 'run'")]
    assert events.count("result_published") == 1 and "attempt_ended" in events


def test_extra_columns_reattached_without_digest(client):
    text = (DEV / "categories.csv").read_text(encoding="utf-8-sig").splitlines()
    rows = [text[0] + ",비고"] + [r + ",메모" + str(i) for i, r in enumerate(text[1:])]
    run, res = flow(client, [("categories.csv", ("\n".join(rows) + "\n").encode())])
    body = client.get(f"/api/runs/{run['id']}/files/categories.csv").text.splitlines()
    assert body[0].endswith(",비고") and "__extras_digest__" not in body[0]
    assert body[1].endswith(",메모0")


def test_non_publishable_table_excluded(client):
    """필수 컬럼이 없는 테이블은 진단은 하되 결과로 발행하지 않는다(AC-03-4, FS 7절)."""
    def drop(maps):
        orders = next(m for m in maps if m["table"] == "orders")
        orders["columns"]["주문일시"] = None
    run, res = flow(client, [dev("orders.csv"), dev("customers.csv")], drop)
    assert res["excluded_tables"] == {"orders": "missing_required"}
    full = client.get(f"/api/runs/{run['id']}/result").json()
    assert {f["name"] for f in full["files"]} == {"customers.csv"}
    assert client.get(f"/api/runs/{run['id']}/files/orders.csv").status_code == 404
    assert "orders" not in client.get(f"/api/runs/{run['id']}/files/changes.csv").text


def test_file_failure_during_publish(client, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("디스크 가득 참 흉내")
    monkeypatch.setattr(publish, "write_files", boom)
    run, res = flow(client, [dev("categories.csv")])
    assert res["status"] == "failed" and res["stage"] == "publish_failed"
    c = db(tmp_path)
    assert c.execute("SELECT COUNT(*) FROM result_version").fetchone()[0] == 0
    assert c.execute("SELECT outcome FROM attempt").fetchone()[0] == "publish_failed"
    assert not (tmp_path / "workspace/runs" / run["id"] / "attempt-1/result").exists()


def test_db_failure_leaves_no_result(client, tmp_path, monkeypatch):
    """DB 기록 실패: 결과 파일이 있어도 result_version 행이 없으면 완료가 아니고 다운로드할 수 없다."""
    def broken(*a, **k):
        raise sqlite3.OperationalError("DB 기록 실패 흉내")
    monkeypatch.setattr(publish, "record", broken)
    run, res = flow(client, [dev("categories.csv")])
    assert res["status"] == "failed" and res["stage"] == "publish_failed"
    assert (tmp_path / "workspace/runs" / run["id"] / "attempt-1/result/categories.csv").exists()  # 흔적만
    full = client.get(f"/api/runs/{run['id']}/result").json()
    assert full["published"] is False
    assert client.get(f"/api/runs/{run['id']}/files/categories.csv").status_code == 404
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "failed"


def test_result_version_append_only(tmp_path, client):
    """결과 버전은 바꾸거나 지울 수 없다."""
    run, res = flow(client, [dev("categories.csv")])
    c = db(tmp_path)
    assert c.execute("SELECT COUNT(*) FROM result_version").fetchone()[0] == 1
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("UPDATE result_version SET data_hashes = '{}'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("DELETE FROM result_version")


def test_download_detects_tampering(client, tmp_path):
    run, res = flow(client, [dev("categories.csv")])
    f = tmp_path / "workspace/runs" / run["id"] / "attempt-1/result/categories.csv"
    f.chmod(0o644)
    f.write_text(f.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    r = client.get(f"/api/runs/{run['id']}/files/categories.csv")
    assert r.status_code == 409 and r.json()["code"] == "result_tampered"


def test_empty_selection_still_publishes(client):
    run = client.post("/api/runs", files=[("files", dev("categories.csv"))], data={"set": "shop", "source": "s"}).json()
    client.put(f"/api/runs/{run['id']}/mapping",
               json={"files": [{"item_id": run["files"][0]["item_id"], **run["files"][0]["mapping"]}]})
    client.post(f"/api/runs/{run['id']}/diagnose")
    p = client.put(f"/api/runs/{run['id']}/selection", json={"approved_groups": []}).json()
    res = client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]}).json()
    assert res["changed_cells"] == 0 and res["result_version"]
