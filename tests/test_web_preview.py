"""M3-1 미리보기 보강 (FS F06, SC07). 개발 입력만 쓴다."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "workspace"))


def ready(client, names=("customers.csv",), edit=None):
    files = [("files", (n, (DEV / n).read_bytes(), "text/csv")) for n in names]
    run = client.post("/api/runs", files=files, data={"set": "shop", "source": "s"}).json()
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    if edit:
        edit(maps)
    assert client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps}).status_code == 200
    client.post(f"/api/runs/{run['id']}/diagnose")
    return run, client.get(f"/api/runs/{run['id']}/preview").json()


def test_group_cells_listed_with_source(client):
    run, p = ready(client)
    g = next(g for g in p["groups"] if g["id"] == "customers.phone.pattern")
    body = client.get(f"/api/runs/{run['id']}/preview/groups/{g['id']}", params={"limit": 500}).json()
    assert body["total"] == g["cells"] + g["held"] and len(body["items"]) == body["total"]
    first = body["items"][0]
    assert first["cell"][0] == "customers" and first["cell"][2] == "phone"
    assert first["source"]["file"] == "customers.csv" and first["source"]["columns"]["phone"] == "휴대폰"
    assert first["before"] != first["after"] and not first["held"] and not first["excluded"]
    page = client.get(f"/api/runs/{run['id']}/preview/groups/{g['id']}", params={"offset": 1, "limit": 2}).json()
    assert page["items"] == body["items"][1:3]
    assert client.get(f"/api/runs/{run['id']}/preview/groups/customers.nope.x").status_code == 404


def test_excluded_cells_not_applied(client):
    """AC-06-1·AC-06-5: 묶음 안에서 뺀 셀은 바뀌지 않고, 최종 확인 건수 = 변경 내역 고유 셀 수."""
    run, p = ready(client)
    gid = "customers.phone.pattern"
    cells = client.get(f"/api/runs/{run['id']}/preview/groups/{gid}", params={"limit": 2}).json()["items"]
    drop = [c["cell"] for c in cells]
    approved = [g["id"] for g in p["groups"] if g["approved"]]
    q = client.put(f"/api/runs/{run['id']}/selection",
                   json={"approved_groups": approved, "excluded_cells": drop}).json()
    assert q["total_cells"] == p["total_cells"] - 2 and q["not_approved"] == 2
    assert sorted(q["excluded_cells"]) == sorted(drop)
    assert next(g for g in q["groups"] if g["id"] == gid)["excluded"] == 2
    listed = client.get(f"/api/runs/{run['id']}/preview/groups/{gid}", params={"limit": 2}).json()["items"]
    assert all(c["excluded"] for c in listed)
    res = client.post(f"/api/runs/{run['id']}/execute", json={"approval": q["approval"]}).json()
    assert res["changed_cells"] == q["total_cells"]
    changes = client.get(f"/api/runs/{run['id']}/changes", params={"limit": 500}).json()
    assert changes["total"] == q["total_cells"]
    changed = {(c["table"], c["record"], c["column"]) for c in changes["items"]}
    assert not changed & {tuple(c) for c in drop}


def test_not_publishable_tables_announced_before_execution(client):
    """F07-3: 발행 대상이 아닌 테이블을 실행 전에 알린다."""
    def drop(maps):
        maps[0]["columns"]["고객명"] = None
    _, p = ready(client, edit=drop)
    assert p["not_publishable"] == {"customers": "missing_required"}


def test_held_cells_show_blocking_reasons(client, tmp_path):
    """AC-06-2: 보류 셀은 선택할 수 없고, 막는 위반·검토가 이유로 나온다(같은 셀 위반을 진단 결과에 넣어 확인)."""
    run, p = ready(client)
    base = tmp_path / "workspace" / "runs" / run["id"]
    report = json.loads((base / "report.json").read_text(encoding="utf-8"))
    cand = next(i for i in report["issues"] if i["category"] == "standardization_candidate"
                and i["columns"] == ["phone"] and i["code"] == "pattern")
    report["issues"].append(cand | {"category": "human_review", "code": "pattern", "suggested_value": None})
    (base / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (base / "apply-plan.json").unlink()
    body = client.get(f"/api/runs/{run['id']}/preview/groups/customers.phone.pattern",
                      params={"limit": 500}).json()
    held = [c for c in body["items"] if c["held"]]
    assert body["held"] == 1 and held[0]["cell"] == [cand["table"], cand["record"], "phone"]
    assert held[0]["reasons"] == [{"category": "human_review", "code": "pattern"}]
    assert body["items"][-1]["held"]  # 보류 셀은 뒤에
    bad = client.put(f"/api/runs/{run['id']}/selection", json={"excluded_cells": [held[0]["cell"]]})
    assert bad.status_code == 400 and "보류" in bad.json()["message"]
