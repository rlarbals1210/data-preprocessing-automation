"""M6-5 재사용 규칙 저장·승인 (FS F06-5·F09, TD 10-2, 결정 M6-D1). 개발 입력만 쓴다."""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from prep import publish, reuse, templates
from prep.preview import Workspace
from prep.profile import signature

ROOT = Path(__file__).resolve().parents[1]
DATA = (ROOT / "data/dirty/development/categories.csv").read_bytes()


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws))


def prepare(client, source="거래처A"):
    run = client.post("/api/runs", files=[("files", ("categories.csv", DATA, "text/csv"))],
                      data={"set": "shop", "source": source}).json()
    d = client.get(f"/api/runs/{run['id']}/drift").json()
    if d["required"] and not d["confirmed"]:
        client.post(f"/api/runs/{run['id']}/drift/confirm")
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    assert client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps}).status_code == 200
    client.post(f"/api/runs/{run['id']}/diagnose")
    return run, client.get(f"/api/runs/{run['id']}/preview").json()


def execute(client, run, preview, reuse_choice=None):
    body = {"approval": preview["approval"]}
    if reuse_choice is not None:
        body["reuse"] = reuse_choice
    return client.post(f"/api/runs/{run['id']}/execute", json=body)


def db(ws):
    c = sqlite3.connect(ws / "app.db")
    c.row_factory = sqlite3.Row
    return c


def selectable(preview):
    return [g["id"] for g in preview["groups"] if g["cells"] and g["approved"]]


def test_transform_id():
    assert reuse.transform_id("type", {"standardization": "iso_datetime_ymd"}) == "type:iso_datetime_ymd"
    assert reuse.transform_id("pattern", {"standardization": "kr_mobile_intl"}) == "pattern:kr_mobile_intl"
    assert reuse.transform_id("comparison_normalization", {"standardization": "x"}) == "comparison_normalization"
    assert reuse.transform_id("type", {}) == "type"


def test_no_reuse_choice_records_nothing(client, ws):
    run, p = prepare(client)
    assert execute(client, run, p).json()["status"] in ("completed", "completed_with_issues")
    c = db(ws)
    assert c.execute("SELECT COUNT(*) FROM reuse_rule").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM approval WHERE kind = 'reuse'").fetchone()[0] == 0


def test_reuse_rules_recorded_with_signatures(client, ws):
    run, p = prepare(client)
    gid = selectable(p)[0]
    item = run["files"][0]["item_id"]
    res = execute(client, run, p, {"groups": [gid], "mappings": [item]}).json()
    assert res["reuse_rules"] == 2 and res["reuse_approval"]
    w = Workspace(ws)
    tid = templates.for_run(w.store, w.load(run["id"])["db_run"])[item]
    rules = {r["kind"]: r for r in reuse.for_template(w.store, tid)}
    m, s = rules["mapping"], rules["standardize"]
    assert m["spec"] == {"table": "categories", "columns": dict(sorted(run["files"][0]["mapping"]["columns"].items()))}
    assert m["transform_id"] is None and m["version"] == 1 and m["state"] == "effective"
    table, column, code = gid.split(".")
    assert s["rule_key"] == gid and s["spec"]["column"] == column and s["transform_id"].startswith(code)
    cells = client.get(f"/api/runs/{run['id']}/preview/groups/{gid}?offset=0&limit=1000").json()["items"]
    assert s["spec"]["signatures"] == sorted({signature(c["before"]) for c in cells if not c["held"]})
    a = db(ws).execute("SELECT * FROM approval WHERE kind = 'reuse'").fetchone()
    assert a["id"] == res["reuse_approval"] and a["config_hash"] == s["config_hash"]
    assert {e["kind"] for e in w.store.events("template", tid)} >= {"reuse_approved"}


def test_reuse_does_not_change_execution_approval(client, ws):
    """재사용 승인은 실행 승인과 별개다 — 실행 승인 해시·적용 셀은 그대로다(F06-5)."""
    run, p = prepare(client)
    res = execute(client, run, p, {"groups": selectable(p), "mappings": []}).json()
    assert res["changed_cells"] == p["total_cells"]
    e = db(ws).execute("SELECT * FROM approval WHERE kind = 'execution' AND invalidated_at IS NULL").fetchone()
    assert e["apply_hash"] == p["approval"]["apply_hash"]


def test_failed_run_rules_have_no_effect(client, ws, monkeypatch):
    """M6-D1: 최종 확인 때 기록하지만 결과를 발행하지 못한 실행의 체크는 효력이 없다."""
    run, p = prepare(client)
    with monkeypatch.context() as m:
        m.setattr(publish, "write_files", lambda *a, **k: (_ for _ in ()).throw(OSError("쓰기 실패 흉내")))
        res = execute(client, run, p, {"groups": selectable(p)[:1], "mappings": [run["files"][0]["item_id"]]}).json()
    assert res["status"] == "failed"
    w = Workspace(ws)
    tid = next(iter(templates.for_run(w.store, w.load(run["id"])["db_run"]).values()))
    rows = reuse.for_template(w.store, tid)
    assert len(rows) == 2 and {r["state"] for r in rows} == {"pending"}
    assert reuse.effective(w.store, tid) == []


def test_new_approval_supersedes_previous_version(client, ws):
    run1, p1 = prepare(client)
    execute(client, run1, p1, {"groups": [], "mappings": [run1["files"][0]["item_id"]]})
    run2, p2 = prepare(client)
    execute(client, run2, p2, {"groups": [], "mappings": [run2["files"][0]["item_id"]]})
    w = Workspace(ws)
    tid = next(iter(templates.for_run(w.store, w.load(run2["id"])["db_run"]).values()))
    rows = reuse.for_template(w.store, tid)
    assert [(r["version"], r["state"]) for r in rows] == [(1, "superseded"), (2, "effective")]
    assert [r["version"] for r in reuse.effective(w.store, tid)] == [2]


@pytest.mark.parametrize("choice, code", [
    ({"groups": ["없는.묶음.code"], "mappings": []}, "bad_reuse"),
    ({"groups": [], "mappings": [999]}, "bad_reuse"),
    ({"groups": "x", "mappings": []}, "bad_reuse"),
])
def test_invalid_reuse_rejected_before_execution(client, ws, choice, code):
    run, p = prepare(client)
    r = execute(client, run, p, choice)
    assert r.status_code == 400 and r.json()["code"] == code
    c = db(ws)
    assert c.execute("SELECT COUNT(*) FROM approval WHERE kind IN ('execution', 'reuse')").fetchone()[0] == 0
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "awaiting_approval"


def test_unapproved_group_cannot_be_reused(client):
    run, p = prepare(client)
    gid = selectable(p)[0]
    p = client.put(f"/api/runs/{run['id']}/selection",
                   json={"approved_groups": [g for g in selectable(p) if g != gid], "excluded_cells": []}).json()
    r = execute(client, run, p, {"groups": [gid], "mappings": []})
    assert r.status_code == 400 and "승인하지 않은 묶음" in r.json()["message"]


def test_reuse_rule_is_append_only_and_revoke_once(client, ws):
    run, p = prepare(client)
    execute(client, run, p, {"groups": [], "mappings": [run["files"][0]["item_id"]]})
    c = db(ws)
    c.execute("PRAGMA recursive_triggers = ON")
    for sql in ("DELETE FROM reuse_rule", "UPDATE reuse_rule SET spec = '{}'",
                "UPDATE reuse_rule SET revoked_at = 'x', revoked_reason = NULL"):
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(sql)
    c.execute("UPDATE reuse_rule SET revoked_at = '2026-11-05', revoked_reason = '사용자 취소'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        c.execute("UPDATE reuse_rule SET revoked_at = '2026-11-06', revoked_reason = '또'")
    c.commit()
    w = Workspace(ws)
    tid = next(iter(templates.for_run(w.store, w.load(run["id"])["db_run"]).values()))
    assert [r["state"] for r in reuse.for_template(w.store, tid)] == ["revoked"] and not reuse.effective(w.store, tid)
    assert "reuse_revoked" in {e["kind"] for e in w.store.events("template", tid)}


def test_reuse_rule_must_link_to_same_run_reuse_approval(client, ws):
    run, p = prepare(client)
    execute(client, run, p)
    c = db(ws)
    ex = c.execute("SELECT id, run_id FROM approval WHERE kind = 'execution'").fetchone()
    tid = c.execute("SELECT id FROM template").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="재사용 승인에만"):
        c.execute("INSERT INTO reuse_rule (template_id, kind, rule_key, version, spec, transform_id, config_hash,"
                  " approval_id, approved_in_run) VALUES (?, 'mapping', 'mapping', 1, ?, NULL, ?, ?, ?)",
                  (tid, json.dumps({}), "0" * 64, ex["id"], ex["run_id"]))
