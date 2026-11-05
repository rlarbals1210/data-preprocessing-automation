"""M6-7 재사용 규칙 목록·승인 취소 (SC12, FS F09 AC-09-4·F06 승인 무효화, TD 10-2). 개발 입력만 쓴다."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
DATA = (ROOT / "data/dirty/development/customers.csv").read_bytes()
PHONE = "customers.phone.pattern"


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws))


def upload(client, source="거래처A"):
    run = client.post("/api/runs", files=[("files", ("customers.csv", DATA, "text/csv"))],
                      data={"set": "shop", "source": source}).json()
    d = client.get(f"/api/runs/{run['id']}/drift").json()
    if d["required"] and not d["confirmed"]:
        assert client.post(f"/api/runs/{run['id']}/drift/confirm").status_code == 200
        run = client.get(f"/api/runs/{run['id']}").json()
    return run


def prepare(client, source="거래처A"):
    run = upload(client, source)
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    assert client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps}).status_code == 200
    assert client.post(f"/api/runs/{run['id']}/diagnose").status_code == 200
    return run, client.get(f"/api/runs/{run['id']}/preview").json()


def execute(client, run, preview, reuse_choice=None):
    body = {"approval": preview["approval"]} | ({"reuse": reuse_choice} if reuse_choice else {})
    return client.post(f"/api/runs/{run['id']}/execute", json=body)


def seed(client, mapping=True):
    run, p = prepare(client)
    res = execute(client, run, p, {"groups": [PHONE], "mappings": [run["files"][0]["item_id"]] if mapping else []})
    assert res.json()["status"] in ("completed", "completed_with_issues")
    return run


def rules(client):
    (unit,) = client.get("/api/reuse-rules").json()
    return {(r["kind"], r["rule_key"], r["version"]): r for r in unit["rules"]}


def revoke(client, rule_id, reason="오적용 확인"):
    return client.post(f"/api/reuse-rules/{rule_id}/revoke", json={"reason": reason})


def group(preview, gid=PHONE):
    return next(g for g in preview["groups"] if g["id"] == gid)


def test_units_list_rules_with_versions(client):
    assert client.get("/api/reuse-rules").json() == []
    first = seed(client)
    (unit,) = client.get("/api/reuse-rules").json()
    assert unit["source"]["name"] == "거래처A" and unit["table"] == "customers"
    assert {(r["kind"], r["rule_key"], r["version"], r["state"], r["run"]) for r in unit["rules"]} == {
        ("mapping", "mapping", 1, "effective", first["id"]), ("standardize", PHONE, 1, "effective", first["id"])}


def test_revoke_validation(client):
    seed(client)
    rid = rules(client)[("standardize", PHONE, 1)]["id"]
    assert revoke(client, rid, "  ").status_code == 400
    assert client.post(f"/api/reuse-rules/{rid}/revoke", json={}).status_code == 400
    assert revoke(client, 9999).status_code == 404
    r = revoke(client, rid)
    assert r.status_code == 200 and r.json()["rule"]["state"] == "revoked"
    assert r.json()["rule"]["revoked_reason"] == "오적용 확인"
    assert revoke(client, rid).status_code == 409  # 한 번만


def test_revoke_applies_from_next_run_and_keeps_past_results(client, ws):
    """AC-09-4: 취소 뒤 다음 실행에서 그 규칙은 후보가 아니고, 과거 결과 버전은 그대로다."""
    first = seed(client)
    sid = client.get("/api/sources").json()[0]["id"]
    before = client.get(f"/api/sources/{sid}/versions").json()
    r = rules(client)
    assert revoke(client, r[("standardize", PHONE, 1)]["id"]).json()["runs"] == []
    assert revoke(client, r[("mapping", "mapping", 1)]["id"]).status_code == 200
    run, p = prepare(client)
    proposal = client.get(f"/api/runs/{run['id']}").json()["files"][0]["proposal"]
    assert "reuse" not in {c["grade"] for c in proposal["columns"]}
    assert not any(g["reuse"] for g in p["groups"])
    assert all(g["approved"] for g in p["groups"] if g["cells"])  # 효력 규칙이 없으면 기본 전체 선택
    after = client.get(f"/api/sources/{sid}/versions").json()
    assert [(v["id"], v["data_hash"]) for v in after] == [(v["id"], v["data_hash"]) for v in before]
    assert client.get(f"/api/runs/{first['id']}/result").json()["status"] in ("completed", "completed_with_issues")
    c = sqlite3.connect(ws / "app.db")
    kinds = [k for (k,) in c.execute("SELECT kind FROM event WHERE subject_type = 'template' ORDER BY id")]
    assert kinds.count("reuse_revoked") == 2


def test_revoke_deselects_prepared_run_and_invalidates_approval(client):
    """F06: 실행 준비 중 취소 → 그 규칙으로 미리 선택된 셀을 해제하고 실행 승인을 무효로 한다(재확인)."""
    seed(client)
    run, p = prepare(client)
    g = group(p)
    assert g["approved"] and g["reuse"] == g["cells"] > 0
    res = revoke(client, rules(client)[("standardize", PHONE, 1)]["id"]).json()
    assert res["runs"] == [run["id"]]
    p2 = client.get(f"/api/runs/{run['id']}/preview").json()
    g2 = group(p2)
    assert g2["reuse"] == 0 and g2["excluded"] == g2["cells"] and p2["total_cells"] == p["total_cells"] - g["cells"]
    assert p2["approval"] != p["approval"]
    stale = execute(client, run, p)
    assert stale.status_code == 409 and stale.json()["code"] == "approval_mismatch"
    ok = execute(client, run, p2).json()
    assert ok["changed_cells"] == p2["total_cells"]


def test_revoke_keeps_user_choices_outside_rule(client):
    """규칙과 맞지 않는 셀을 사용자가 고른 것은 그대로 둔다(취소는 그 규칙으로 미리 선택된 셀만 해제)."""
    seed(client)
    run, p = prepare(client)
    others = [x["id"] for x in p["groups"] if x["cells"] and x["id"] != PHONE]
    p = client.put(f"/api/runs/{run['id']}/selection",
                   json={"view_hash": p["view_hash"], "approved_groups": [PHONE, others[0]],
                         "excluded_cells": p["excluded_cells"]}).json()
    revoke(client, rules(client)[("standardize", PHONE, 1)]["id"])
    p2 = client.get(f"/api/runs/{run['id']}/preview").json()
    assert group(p2, others[0])["approved"] and group(p2, others[0])["excluded"] == 0
    assert p2["total_cells"] == group(p, others[0])["cells"]


def test_revoke_mapping_rule_resets_unconfirmed_proposal(client):
    seed(client)
    waiting = upload(client)
    assert waiting["files"][0]["proposal"]["table"]["basis"] == "reuse"
    done, _ = prepare(client)  # 매핑을 이미 확정한 실행은 그대로
    res = revoke(client, rules(client)[("mapping", "mapping", 1)]["id"]).json()
    assert res["runs"] == [waiting["id"]]
    now = client.get(f"/api/runs/{waiting['id']}").json()["files"][0]
    assert now["proposal"]["table"]["basis"] != "reuse"
    assert "reuse" not in {c["grade"] for c in now["proposal"]["columns"]}
    assert now["mapping"] == waiting["files"][0]["mapping"]  # 같은 매핑이 일반 제안으로 나온다
    assert client.get(f"/api/runs/{done['id']}").json()["files"][0]["confirmed"] is True


def test_revoked_latest_version_does_not_revive_older(client):
    seed(client)
    run, p = prepare(client)
    assert execute(client, run, p, {"groups": [PHONE], "mappings": []}).status_code == 200
    r = rules(client)
    assert r[("standardize", PHONE, 1)]["state"] == "superseded" and r[("standardize", PHONE, 2)]["state"] == "effective"
    assert revoke(client, r[("standardize", PHONE, 1)]["id"]).status_code == 409
    revoke(client, r[("standardize", PHONE, 2)]["id"])
    assert rules(client)[("standardize", PHONE, 1)]["state"] == "superseded"
    _, p2 = prepare(client)
    assert group(p2)["reuse"] == 0
