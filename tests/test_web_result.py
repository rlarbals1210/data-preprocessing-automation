"""M3-3 결과 보강 (FS F07 AC-07-2, SC08). 개발 입력만 쓴다."""

import json
import os
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


def executed(client, names):
    files = [("files", (n, (DEV / n).read_bytes(), "text/csv")) for n in names]
    run = client.post("/api/runs", files=files, data={"set": "shop", "source": "s"}).json()
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps})
    client.post(f"/api/runs/{run['id']}/diagnose")
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    return run, client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]}).json()


def test_remaining_problems_listed_with_source(client):
    run, res = executed(client, ["customers.csv"])
    assert res["status"] == "completed_with_issues"
    body = client.get(f"/api/runs/{run['id']}/remaining", params={"limit": 500}).json()
    assert body["total"] == res["after"]["business_rule_violation"] + res["after"]["human_review"]
    assert {i["category"] for i in body["items"]} == {"business_rule_violation", "human_review"}
    assert all(i["source"]["file"] == "customers.csv" for i in body["items"])
    only = client.get(f"/api/runs/{run['id']}/remaining", params={"category": "human_review"}).json()
    assert only["total"] == res["after"]["human_review"]


def test_evidence_files_downloadable_and_verified(client, tmp_path):
    run, res = executed(client, ["customers.csv"])
    full = client.get(f"/api/runs/{run['id']}/result").json()
    assert full["evidence"] == ["changes.csv", "changes.json", "diagnosis-before.json", "diagnosis-after.json",
                                "held.json", "absent-cells.json", "manifest.json"]
    changes = client.get(f"/api/runs/{run['id']}/files/changes.json")
    assert changes.headers["content-type"].startswith("application/json")
    items = changes.json()
    assert len(items) == res["changed_cells"] and all(c["source"]["item_id"] == 1 for c in items)
    assert any(c["logical_after"] is None for c in items)  # 빈 값과 논리 null을 JSON에서 구분
    before = client.get(f"/api/runs/{run['id']}/files/diagnosis-before.json").json()
    assert all("source" in i for i in before["issues"])
    manifest = client.get(f"/api/runs/{run['id']}/files/manifest.json").json()
    assert "diagnosis-after.json" in manifest["files"]
    # manifest를 바꾸면 내려받지 않는다
    path = next((tmp_path / "workspace" / "runs" / run["id"]).glob("attempt-*/result/manifest.json"))
    os.chmod(path, 0o644)
    m = json.loads(path.read_text(encoding="utf-8"))
    m["files"]["customers.csv"]["sha256"] = "0" * 64
    path.write_text(json.dumps(m), encoding="utf-8")
    assert client.get(f"/api/runs/{run['id']}/files/manifest.json").status_code == 409
    assert client.get(f"/api/runs/{run['id']}/files/secret.json").status_code == 404


def test_failed_run_only_failure_report(client, monkeypatch):
    """AC-07-2: 실패하면 결과 데이터는 없고 실패 진단 리포트만 내려받는다."""
    def boom(*a, **k):
        raise OSError("쓰기 실패 흉내")
    with monkeypatch.context() as m:
        m.setattr(publish, "write_files", boom)
        run, res = executed(client, ["categories.csv"])
    assert res["status"] == "failed"
    report = client.get(f"/api/runs/{run['id']}/failure-report")
    assert report.status_code == 200 and "attachment" in report.headers["content-disposition"]
    body = report.json()
    assert body["status"] == "failed" and body["attempt"]["outcome"] == "publish_failed"
    assert "쓰기 실패" in body["attempt"]["error"] and body["diagnosis_after"] is not None
    assert "\"values\"" not in json.dumps(body)  # 셀 값(결과 데이터)은 넣지 않는다
    full = client.get(f"/api/runs/{run['id']}/result").json()
    assert not full["published"] and full["files"] == [] and full["evidence"] == []
    assert client.get(f"/api/runs/{run['id']}/files/categories.csv").status_code == 404
    assert client.get(f"/api/runs/{run['id']}/remaining").status_code == 409


def test_failure_report_only_for_failed(client):
    run, _ = executed(client, ["categories.csv"])
    assert client.get(f"/api/runs/{run['id']}/failure-report").status_code == 404
