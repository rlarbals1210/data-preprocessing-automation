"""M1-10b 정제 시도·감사 대조 (TD 8-4·8-5, AC-07-1·07-5)."""

import json
import sqlite3

import pytest

from diagnostics.engine import diagnose
from prep import attempt as att
from prep.apply import build_apply_plan, group_id
from prep.guard import Guard
from prep.runs import RunLedger
from prep.store import Store
from prep.view import ViewInput, build_view

CONFIG = {"tables": {"t": {"columns": {"id": {"required": True},
                                       "price": {"type": "integer", "standardization": "krw_integer_symbol"},
                                       "status": {"allowed": ["paid"], "casefold": True}}}}}


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "in").mkdir(parents=True)
    guard = Guard([ws], project_root=tmp_path / "project")
    store = Store(ws, guard)
    src = ws / "in" / "t.csv"
    src.write_text("id,price,status\n1,\"1,000\",PAID\n2,₩2000,paid\n3,3000,PAID\n", encoding="utf-8")
    view = build_view([ViewInput(1, "t", src, {"id": "id", "price": "price", "status": "status"}, 1)],
                      ws / "run" / "view", guard)
    report = diagnose(view.folder, CONFIG)
    # 승인 기록(시도는 실행 승인을 가리킨다)
    led = RunLedger(store)
    snap = store.add_config_snapshot("c" * 64, "shop", "p", [])
    batch = store.create_batch(store.source_id("s"))
    item = store.add_upload_item(batch, "t.csv", "1" * 64, "csv", {}, "ok", [])
    run = led.start(batch, snap)
    led.set_targets(run, [{"upload_item_id": item, "included": True}])
    led.record_mapping(run, {}, {item: {"table": "t"}})
    led.record_plan(run, {"executed": [], "skipped": [], "tables": {}, "hash": "p" * 64})
    led.record_view(run, "run/view", view.hash)
    plan = build_apply_plan(report, view.hash, {"approved_groups": [group_id("t", "price", "type")]})
    led.record_apply_plan(run, plan)
    approval = led.approve_execution(run, led.current_hashes(run))
    yield store, guard, ws, view, plan, run, approval
    store.close()


def go(env):
    store, guard, ws, view, plan, run, approval = env
    a = att.start(store, guard, run, ws / "run", approval)
    att.run_engine(a, view.folder, CONFIG, plan["allow"])
    return a


def test_attempt_folders_never_overwritten(env):
    store, guard, ws, view, plan, run, approval = env
    a1, a2 = go(env), go(env)
    assert (a1.number, a2.number) == (1, 2) and a1.folder != a2.folder
    assert (a1.engine / "data/t.csv").is_file() and (a2.engine / "data/t.csv").is_file()
    (ws / "run" / "attempt-3").mkdir()
    with pytest.raises(FileExistsError):  # AC-07-5
        att.start(store, guard, run, ws / "run", approval)


def test_audit_passes_and_is_recorded(env):
    store, guard, ws, view, plan, run, approval = env
    a = go(env)
    report = att.audit(a, view.folder, guard, plan["allow"], view.hash)
    assert report["passed"] and report["changed_cells"] == report["allowed_cells"] == 2
    assert report["checked_cells"] == 9
    att.finish(store, a, "engine_ok", None, report)
    row = store.conn.execute("SELECT outcome, audit FROM attempt WHERE id = ?", (a.id,)).fetchone()
    assert row["outcome"] == "engine_ok" and json.loads(row["audit"])["passed"]
    assert json.loads((a.folder / "audit.json").read_text())["passed"]
    assert any(e["kind"] == "attempt_ended" for e in store.events("run", run))


def test_audit_detects_unapproved_change(env):
    """AC-07-1: 승인 목록 밖 셀이 결과에서 바뀌면 실패."""
    _, guard, _, view, plan, _, _ = env
    a = go(env)
    data = a.engine / "data/t.csv"
    data.write_text(data.read_text(encoding="utf-8").replace("PAID", "paid", 1), encoding="utf-8")
    with pytest.raises(att.AuditError) as e:
        att.audit(a, view.folder, guard, plan["allow"], view.hash)
    assert "status" in e.value.audit["problems"][0] and not e.value.audit["passed"]


def test_audit_detects_ledger_mismatch(env):
    _, guard, _, view, plan, _, _ = env
    a = go(env)
    with pytest.raises(att.AuditError, match="승인한 셀이 바뀌지 않았다"):
        att.audit(a, view.folder, guard, plan["allow"] + [["t", 3, "status"]], view.hash)
    with pytest.raises(att.AuditError, match="승인 안 한 셀"):
        att.audit(a, view.folder, guard, plan["allow"][:1], view.hash)


def test_audit_detects_view_change_and_missing_output(env):
    _, guard, _, view, plan, _, _ = env
    a = go(env)
    with pytest.raises(att.AuditError, match="표준화 뷰"):
        att.audit(a, view.folder, guard, plan["allow"], "0" * 64)
    (a.engine / "data/t.csv").unlink()
    with pytest.raises(att.AuditError, match="결과 파일이 없다"):
        att.audit(a, view.folder, guard, plan["allow"], view.hash)


def test_attempt_finish_only_once(env):
    store, *_ = env
    a = go(env)
    att.finish(store, a, "engine_failed", "x")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        att.finish(store, a, "engine_ok")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.conn.execute("DELETE FROM attempt")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store.conn.execute("UPDATE attempt SET path = 'x'")
