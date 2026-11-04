"""M4-3 정합성 파이프라인 연결 (TD 8-3 보류 대응표·8-5 재검사·9-2 공급자, FS F05 AC-05-1·05-5). 개발 입력만 쓴다."""

import json
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import prep.preview as preview
from api.app import create_app
from evaluation import score as scorer
from prep.export import export_run
from prep.preview import Workspace

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws))


def diagnosed(client, names):
    files = [("files", (n, (DEV / n).read_bytes(), "text/csv")) for n in names]
    run = client.post("/api/runs", files=files, data={"set": "shop", "source": "s"}).json()
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps})
    client.post(f"/api/runs/{run['id']}/diagnose")
    return run


def test_dev_set_virtual_violations_and_holds(client):
    """개발 입력 전체: 가상 기준(모든 후보 적용) 위반은 소수의 행에만 남고(AC-05-1 대체 확인), 그 계산에 쓰인 후보는 보류."""
    run = diagnosed(client, sorted(p.name for p in DEV.glob("*.csv")))
    d = client.get(f"/api/runs/{run['id']}/diagnosis").json()
    assert d["integrity"]["virtual"]["not_run"] == 0 and d["integrity"]["virtual"]["disabled"] == 0
    virt = client.get(f"/api/runs/{run['id']}/integrity", params={"basis": "virtual", "limit": 500}).json()
    keys = sorted((v["rule"][:3], v["table"], v["record"]) for v in virt["items"])
    assert keys == [("R01", "order_items", 1), ("R01", "order_items", 1424), ("R02", "orders", 1),
                    ("R08", "order_items", 1)]
    first = next(v for v in virt["items"] if v["rule"].startswith("R02"))
    assert any(s["file"] == "orders.csv" and s["record"] == 1 for s in first["sources"])
    assert {s["table"] for s in first["sources"]} == {"orders", "order_items"}
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    assert p["held"] == 2
    held = [c for g in p["groups"] if g["held"]
            for c in client.get(f"/api/runs/{run['id']}/preview/groups/{g['id']}", params={"limit": 500}).json()["items"]
            if c["held"]]
    assert held and all(any(r["category"] == "integrity" for r in c["reasons"]) for c in held)
    violated_rows = {(v["table"], v["record"]) for v in virt["items"]} | {
        (t, r) for v in virt["items"] for t, r in v["rows"]}
    assert all((c["cell"][0], c["cell"][1]) in violated_rows for c in held)


def test_approved_basis_follows_selection(client):
    """AC-05-5: 금액 표기 후보를 빼면 승인 기준 계산이 바뀐다(원문·가상 기준과 보류는 그대로)."""
    run = diagnosed(client, ["orders.csv", "order_items.csv"])
    rid = run["id"]
    before = client.get(f"/api/runs/{rid}/integrity", params={"basis": "approved"}).json()
    r01 = next(r for r in before["rules"] if r["id"].startswith("R01"))
    base = r01["counts"]["not_computable"].get("parse_error", 0)  # 보류 셀은 승인 기준에서도 원문 그대로
    p = client.get(f"/api/runs/{rid}/preview").json()
    keep = [g["id"] for g in p["groups"] if g["id"] != "order_items.line_total.type"]
    q = client.put(f"/api/runs/{rid}/selection", json={"approved_groups": keep}).json()
    assert q["held"] == p["held"]
    after = client.get(f"/api/runs/{rid}/integrity", params={"basis": "approved"}).json()
    r01 = next(r for r in after["rules"] if r["id"].startswith("R01"))
    assert r01["counts"]["not_computable"].get("parse_error", 0) > base + 100
    raw = client.get(f"/api/runs/{rid}/integrity", params={"basis": "raw"}).json()
    assert raw["basis"] == "raw" and client.get(f"/api/runs/{rid}/integrity",
                                                params={"basis": "x"}).status_code == 400


def test_result_recheck_and_publish(client, ws):
    """TD 8-5 2단계: 실제 결과로 다시 평가하고, 진단 때 알던 위반만 남으면 발행(완료·문제 남음)."""
    run = diagnosed(client, ["orders.csv", "order_items.csv"])
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    res = client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]}).json()
    assert res["status"] == "completed_with_issues" and res["integrity_after"] >= 1
    after = client.get(f"/api/runs/{run['id']}/integrity", params={"basis": "result"}).json()
    assert after["basis"] == "result" and after["total"] == res["integrity_after"]
    body = client.get(f"/api/runs/{run['id']}/files/integrity.json").json()
    assert body["violations"][0]["sources"][0]["item_id"] >= 1


def test_new_violation_after_cleaning_fails_publish(client, monkeypatch):
    """정제 결과에서 진단 때 없던 위반이 생기면 실패하고 결과를 발행하지 않는다."""
    run = diagnosed(client, ["orders.csv", "order_items.csv"])
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    real = preview.evaluate_integrity

    def fake(doc, data, basis="raw"):
        out = real(doc, data, basis)
        if basis == "result":
            out["violations"].append({"rule": "R01_line_total", "table": "order_items", "record": 7,
                                      "rows": [["order_items", 7]], "cells": [], "basis": "result"})
        return out
    monkeypatch.setattr(preview, "evaluate_integrity", fake)
    res = client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]}).json()
    assert res["status"] == "failed" and "정합성 재검사에서 신규 위반" in res["error"]
    assert client.get(f"/api/runs/{run['id']}/files/orders.csv").status_code == 404


def test_scorer_detects_total_conflict(client, ws, tmp_path):
    """채점기: 그 주문을 기준 행으로 하는 R02 위반 묶음이 있으면 total_conflict 탐지(④ 5-3)."""
    run = diagnosed(client, ["orders.csv", "order_items.csv"])
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]})
    folder = export_run(Workspace(ws), run["id"])
    inputs = tmp_path / "inputs" / "development"
    inputs.mkdir(parents=True)
    for n in ("orders.csv", "order_items.csv"):
        (inputs / n).write_bytes((DEV / n).read_bytes())
    observed = (DEV / "orders.csv").read_text(encoding="utf-8-sig").splitlines()[1].split(",")[12]
    other = (DEV / "orders.csv").read_text(encoding="utf-8-sig").splitlines()[2].split(",")[12]
    cases = [{"case_id": "T1", "input_file": "orders.csv", "record_number": 1, "column": "주문총액",
              "observed_value": observed, "clean_value": observed, "expected_action": "review",
              "issue_type": "total_conflict", "pattern_group": "seen", "profile": "A", "reason": "",
              "canonical_column": "order_total"},
             {"case_id": "T2", "input_file": "orders.csv", "record_number": 2, "column": "주문총액",
              "observed_value": other, "clean_value": other, "expected_action": "review",
              "issue_type": "total_conflict", "pattern_group": "seen", "profile": "A", "reason": "",
              "canonical_column": "order_total"}]
    answers = tmp_path / "answers"
    (answers / "development").mkdir(parents=True)
    (answers / "development/cases.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n"
                                                            for c in cases), encoding="utf-8")
    (answers / "manifest.json").write_text(json.dumps({"splits": {"development": {
        "cases": 2, "actions": {"review": 2}, "issue_types": dict(Counter(c["issue_type"] for c in cases))}}}),
        encoding="utf-8")
    out = scorer.run(folder, "development", answers_root=answers, inputs_root=tmp_path / "inputs",
                     out=tmp_path / "score", condition="rules-only")
    details = {json.loads(x)["case_id"]: json.loads(x) for x in (out / "cases.jsonl").read_text().splitlines()}
    assert details["T1"]["detected"] is True and details["T2"]["detected"] is False
