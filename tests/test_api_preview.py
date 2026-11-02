"""웹 미리보기 API (prep/preview.py, api/app.py). 개발 입력만 쓴다."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from prep.preview import plan

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "workspace"))


def upload(client, names, set_name="shop", source="판매팀"):
    files = [("files", (n, (DEV / n).read_bytes(), "text/csv")) for n in names]
    r = client.post("/api/runs", files=files, data={"set": set_name, "source": source})
    assert r.status_code == 200, r.text
    return r.json()


def test_full_flow_all_tables(client):
    run = upload(client, sorted(p.name for p in DEV.glob("*.csv")))
    assert run["status"] == "uploaded" and run["rows"] == 6409
    assert all(h["logical"] for f in run["files"] for h in f["headers"])
    d = client.post(f"/api/runs/{run['id']}/diagnose").json()
    assert d["engine_status"] == "completed" and d["rules"]["not_executed"] == 0
    assert d["category_counts"]["standardization_candidate"] == 4424
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    assert p["total_cells"] == 4424 and p["groups"][0]["examples"]
    res = client.post(f"/api/runs/{run['id']}/execute").json()
    assert res["changed_cells"] == 4424 and res["rows"] == 6409
    full = client.get(f"/api/runs/{run['id']}/result").json()
    assert len(full["files"]) == 13
    ch = client.get(f"/api/runs/{run['id']}/changes", params={"limit": 5}).json()
    assert ch["total"] == 4424 and len(ch["items"]) == 5
    dl = client.get(f"/api/runs/{run['id']}/files/orders.csv")
    assert dl.status_code == 200 and dl.text.startswith("order_id,")
    assert client.get("/api/runs").json()[0]["id"] == run["id"]


def test_partial_upload_skips_dependent_rules(client):
    run = upload(client, ["orders.csv"])
    d = client.post(f"/api/runs/{run['id']}/diagnose").json()
    assert d["engine_status"] == "completed"
    reasons = {(s["rule"], s["reason"]) for s in d["skipped_rules"]}
    assert ("reference", "excluded_target") in reasons


def test_profile_mode(client):
    run = upload(client, ["categories.csv"], "none")
    d = client.post(f"/api/runs/{run['id']}/diagnose").json()
    assert d["mode"] == "profile"


@pytest.mark.parametrize("name", ["orders.txt", ".csv", "x" * 300 + ".csv", "x.csv.txt"])
def test_bad_file_names_rejected(client, name):
    r = client.post("/api/runs", files=[("files", (name, b"a\n1\n", "text/csv"))],
                    data={"set": "shop", "source": "s"})
    assert r.status_code == 400 and r.json()["code"] == "bad_upload"


def test_source_required_and_listed(client):
    r = client.post("/api/runs", files=[("files", ("a.csv", b"a\n1\n", "text/csv"))], data={"set": "shop"})
    assert r.status_code == 400 and "출처" in r.json()["message"]
    upload(client, ["categories.csv"], source="물류팀")
    upload(client, ["categories.csv"], source="물류팀")
    assert [s["name"] for s in client.get("/api/sources").json()] == ["물류팀"]


def test_same_name_files_kept_separately(client):
    data = (DEV / "categories.csv").read_bytes()
    r = client.post("/api/runs", files=[("files", ("categories.csv", data, "text/csv"))] * 2,
                    data={"set": "shop", "source": "s"})
    run = r.json()
    assert len({f["item_id"] for f in run["files"]}) == 2
    assert client.post(f"/api/runs/{run['id']}/diagnose").json()["code"] == "duplicate_table"


@pytest.mark.parametrize("name", ["../orders.csv", "folder/orders.csv", "../../x/orders.csv"])
def test_upload_paths_reduced_to_file_name(client, tmp_path, name):
    """폴더 업로드·경로 조작 이름은 파일 이름만 남기고 실행 폴더 안에만 쓴다."""
    r = client.post("/api/runs", files=[("files", (name, b"a\n1\n", "text/csv"))],
                    data={"set": "shop", "source": "s"})
    run = r.json()
    assert r.status_code == 200 and run["files"][0]["name"] == "orders.csv"
    assert not list(tmp_path.rglob("orders.csv"))  # 원본은 이름이 아니라 해시로 blobs/에 보관
    assert (tmp_path / "workspace/blobs" / run["files"][0]["sha256"]).is_file()


def test_unknown_run_and_files(client):
    assert client.get("/api/runs/20260101T000000Z-abcdef").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fetc").status_code == 404
    assert client.get("/api/nothing").status_code == 404
    run = upload(client, ["categories.csv"])
    assert client.get(f"/api/runs/{run['id']}/files/../run.json").status_code == 404
    assert client.get(f"/api/runs/{run['id']}/result").status_code == 409


def test_broken_csv_reported(client):
    r = client.post("/api/runs", files=[("files", ("orders.csv", b"a,a\n1,2\n", "text/csv"))],
                    data={"set": "shop", "source": "s"})
    assert r.json()["files"][0]["read_error"]
    assert client.post(f"/api/runs/{r.json()['id']}/diagnose").status_code == 400


def test_plan_reasons():
    logical = {"version": 1, "tables": {
        "a": {"columns": {"id": {"required": True}, "s": {}, "m": {"required_if": {"column": "s", "in": ["x"]}}},
              "unique": [["id"]]},
        "b": {"columns": {"id": {}, "a_id": {}}, "references": [
            {"columns": ["a_id"], "table": "a", "target_columns": ["id"]}]}}}
    config, skipped = plan(logical, {"a": {"id", "m"}, "b": {"id", "a_id"}})
    assert {(s["table"], s["rule"], s["reason"]) for s in skipped} == {
        ("a", "column", "missing_column"), ("a", "required_if", "dependency_skipped")}
    assert config["tables"]["b"]["references"]
    config, skipped = plan(logical, {"b": {"id", "a_id"}})
    assert ("b", "reference", "excluded_target") in {(s["table"], s["rule"], s["reason"]) for s in skipped}
