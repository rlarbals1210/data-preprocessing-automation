"""M3-2 진단 보강 (FS F04 AC-04-4, SC05). 개발 입력만 쓴다."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "workspace"))


def diagnosed(client, names):
    files = [("files", (n, (DEV / n).read_bytes(), "text/csv")) for n in names]
    run = client.post("/api/runs", files=files, data={"set": "shop", "source": "s"}).json()
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps})
    client.post(f"/api/runs/{run['id']}/diagnose")
    return run


def test_issue_opens_original_row(client):
    """AC-04-4: 진단 항목의 원본 위치로 원문 행(원래 헤더·물리 줄)을 연다."""
    run = diagnosed(client, ["customers.csv"])
    issue = client.get(f"/api/runs/{run['id']}/issues",
                       params={"category": "business_rule_violation", "limit": 1}).json()["items"][0]
    src = issue["source"]
    row = client.get(f"/api/runs/{run['id']}/source",
                     params={"item_id": src["item_id"], "record": src["record"]}).json()
    lines = (DEV / "customers.csv").read_text(encoding="utf-8-sig").splitlines()
    assert row["file"] == "customers.csv" and row["line_start"] == src["line_start"]
    assert [c["original"] for c in row["cells"]] == lines[0].split(",")
    assert ",".join(c["value"] for c in row["cells"]) == lines[src["line_start"] - 1]
    original = src["columns"][issue["columns"][0]]
    cell = next(c for c in row["cells"] if c["original"] == original)
    assert cell["logical"] == issue["columns"][0] and cell["value"] == issue["values"][issue["columns"][0]]


def test_source_row_errors(client):
    run = diagnosed(client, ["categories.csv"])
    assert client.get(f"/api/runs/{run['id']}/source", params={"item_id": 1, "record": 999}).status_code == 404
    assert client.get(f"/api/runs/{run['id']}/source", params={"item_id": 9, "record": 1}).status_code == 404
    assert client.get(f"/api/runs/{run['id']}/source", params={"item_id": 1, "record": 0}).status_code == 404


def test_duplicate_groups_listed_with_sources(client):
    run = diagnosed(client, ["customers.csv"])
    body = client.get(f"/api/runs/{run['id']}/duplicates").json()
    assert body["total"] == client.get(f"/api/runs/{run['id']}/diagnosis").json()["duplicate_groups"] > 0
    kinds = {(g["code"], g["kind"]) for g in body["items"]}
    assert ("unique", "conflicting") in kinds and ("unique", "identical") in kinds
    g = body["items"][0]
    assert len(g["sources"]) == g["participant_count"] == len(g["records"])
    assert all(s["file"] == "customers.csv" for s in g["sources"])
