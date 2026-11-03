"""M1-10a 승인 해시 7종·무효화 (docs/technical-design.md 4-2·8-1절, AC-06-4·AC-07-6)."""

import sqlite3

import pytest

from prep.guard import Guard
from prep.runs import HASHES, ApprovalError, RunLedger
from prep.store import Store


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    store = Store(ws, Guard([ws], project_root=tmp_path / "project"))
    led = RunLedger(store)
    snap = store.add_config_snapshot("c" * 64, "shop", "p", ["schema.json"])
    batch = store.create_batch(store.source_id("s"))
    items = [store.add_upload_item(batch, f"{n}.csv", h * 64, "csv", {"encoding": "utf-8-sig", "delimiter": ","},
                                   "ok", []) for n, h in (("a", "1"), ("b", "2"))]
    run = led.start(batch, snap)
    yield store, led, run, items
    store.close()


PLAN = {"executed": [], "skipped": [], "tables": {}, "hash": "p" * 64}


def apply_plan(view_hash, h="a", cand="k"):
    return {"view_hash": view_hash, "candidates_hash": cand * 64, "approved_groups": [], "excluded_cells": [],
            "allow": [], "hash": h * 64}


def prepare(led, run, items, mapping=None):
    led.set_targets(run, [{"upload_item_id": i, "included": True} for i in items])
    led.record_mapping(run, {"auto": 1}, mapping or {items[0]: {"table": "t", "columns": {"x": "x"}}})
    led.record_plan(run, PLAN)
    led.record_view(run, "runs/1/view", "v" * 64)
    led.record_apply_plan(run, apply_plan("v" * 64))
    return led.current_hashes(run)


def valid_execution(led, run):
    return [a for a in led.approvals(run) if a["kind"] == "execution" and a["invalidated_at"] is None]


def test_approve_and_check(env):
    _, led, run, items = env
    h = prepare(led, run, items)
    assert set(h) == set(HASHES) and all(h.values())
    aid = led.approve_execution(run, h)
    assert led.check_execution(run, aid)["apply_hash"] == "a" * 64


@pytest.mark.parametrize("key", HASHES)
def test_any_presented_mismatch_rejected(env, key):
    """승인 때와 해시가 하나라도 다르면 실행 승인을 만들지 않는다."""
    _, led, run, items = env
    h = prepare(led, run, items)
    with pytest.raises(ApprovalError) as e:
        led.approve_execution(run, h | {key: "0" * 64})
    assert e.value.mismatched == [key] and valid_execution(led, run) == []


@pytest.mark.parametrize(
    "change, reason, changed_key",
    [
        ("targets", "targets_changed", "input_hash"),
        ("mapping", "mapping_changed", "mapping_hash"),
        ("plan", "plan_regenerated", "plan_hash"),
        ("view", "view_regenerated", "view_hash"),
        ("selection", "selection_changed", "apply_hash"),
    ],
)
def test_later_change_invalidates_approval(env, change, reason, changed_key):
    """AC-06-4: 미리보기·승인 뒤 실행 대상·매핑·계획·뷰·선택이 바뀌면 예전 승인으로 실행되지 않는다."""
    _, led, run, items = env
    h = prepare(led, run, items)
    aid = led.approve_execution(run, h)
    if change == "targets":
        led.set_targets(run, [{"upload_item_id": items[1], "included": True},
                              {"upload_item_id": items[0], "included": True}])
    elif change == "mapping":
        led.record_mapping(run, {"auto": 1}, {items[0]: {"table": "t", "columns": {"x": None}}})
    elif change == "plan":
        led.record_plan(run, PLAN | {"hash": "q" * 64})
    elif change == "view":
        led.record_view(run, "runs/1/view2", "w" * 64)
    else:
        led.record_apply_plan(run, apply_plan("v" * 64, h="b"))
    a = next(x for x in led.approvals(run) if x["id"] == aid)
    assert a["invalidated_reason"] == reason
    with pytest.raises(ApprovalError):
        led.check_execution(run, aid)
    if changed_key in ("input_hash", "mapping_hash", "plan_hash", "view_hash", "apply_hash"):
        with pytest.raises(ApprovalError):
            led.approve_execution(run, h)  # 예전 해시로 다시 승인할 수도 없다


def test_targets_change_requires_new_structure_approval(env):
    _, led, run, items = env
    prepare(led, run, items)
    led.set_targets(run, [{"upload_item_id": items[0], "included": True}])
    with pytest.raises(ApprovalError, match="구조 승인"):
        led.approve_execution(run, led.current_hashes(run))
    led.record_mapping(run, {"auto": 1}, {items[0]: {"table": "t", "columns": {"x": "x"}}})
    led.approve_execution(run, led.current_hashes(run))


def test_reconfirming_same_mapping_keeps_execution_approval(env):
    _, led, run, items = env
    h = prepare(led, run, items)
    aid = led.approve_execution(run, h)
    led.record_mapping(run, {"auto": 1}, {items[0]: {"table": "t", "columns": {"x": "x"}}})
    assert led.check_execution(run, aid)


def test_missing_stage_rejected(env):
    _, led, run, items = env
    led.set_targets(run, [{"upload_item_id": items[0], "included": True}])
    led.record_mapping(run, {}, {items[0]: {"table": "t", "columns": {}}})
    with pytest.raises(ApprovalError, match="준비되지"):
        led.approve_execution(run, led.current_hashes(run))
    with pytest.raises(ApprovalError):
        led.record_view(run, "x", "v" * 64)  # 계획 없이 뷰 없음


def test_apply_plan_must_match_current_view(env):
    _, led, run, items = env
    prepare(led, run, items)
    with pytest.raises(ApprovalError):
        led.record_apply_plan(run, apply_plan("z" * 64))


def test_new_approval_supersedes_previous(env):
    _, led, run, items = env
    h = prepare(led, run, items)
    first = led.approve_execution(run, h)
    second = led.approve_execution(run, h)
    assert [a["id"] for a in valid_execution(led, run)] == [second]
    assert next(a for a in led.approvals(run) if a["id"] == first)["invalidated_reason"] == "superseded"


def test_approval_rows_append_only(env):
    store, led, run, items = env
    aid = led.approve_execution(run, prepare(led, run, items))
    c = store.conn
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("UPDATE approval SET apply_hash = ? WHERE id = ?", ("x" * 64, aid))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("DELETE FROM approval WHERE id = ?", (aid,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("UPDATE approval SET invalidated_at = NULL, invalidated_reason = NULL WHERE id = ?", (aid,))
    c.execute("UPDATE approval SET invalidated_at = 'x', invalidated_reason = 'test' WHERE id = ?", (aid,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("UPDATE approval SET invalidated_at = 'y', invalidated_reason = 'again' WHERE id = ?", (aid,))
    for table in ("mapping_version", "run_plan", "view_snapshot", "apply_plan"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute(f"DELETE FROM {table}")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute(f"UPDATE {table} SET hash = hash")
    kinds = [e["kind"] for e in store.events("run", run)]
    assert "approval_invalidated" in kinds


def test_same_targets_do_not_report_targets_changed(env):
    """M3-4: 같은 실행 대상을 다시 확정하면 무효화 이유는 매핑 단계가 정한다(화면에 정확한 이유를 보이기 위해)."""
    _, led, run, items = env
    prepare(led, run, items)
    led.set_targets(run, [{"upload_item_id": i, "included": True} for i in items])
    led.record_mapping(run, {"auto": 1}, {items[0]: {"table": "t", "columns": {"x": None}}})
    reasons = [a["invalidated_reason"] for a in led.approvals(run) if a["invalidated_at"]]
    assert "targets_changed" not in reasons and "mapping_changed" in reasons
