"""M1-3b SQLite 저장 계층 (docs/technical-design.md 4절)."""

import sqlite3

import pytest

from prep import store as st
from prep.guard import Guard


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "workspace"
    w.mkdir()
    return w


@pytest.fixture
def db(ws):
    s = st.Store(ws, Guard([ws], project_root=ws.parent / "project"))
    yield s
    s.close()


H = "a" * 64


def seed(db):
    snap = db.add_config_snapshot(H, "shop", "config-snapshots/" + H, ["schema.json"])
    batch = db.create_batch(db.source_id("판매팀"))
    items = [db.add_upload_item(batch, f"t{i}.csv", "b" * 64, "csv", {"encoding": "utf-8-sig"}, "ok", [])
             for i in range(2)]
    run = db.create_run(batch, snap)
    return snap, batch, items, run


def test_migration_and_reopen(ws, db):
    assert db.version == len(st.MIGRATIONS) == 1
    db.close()
    again = st.Store(ws, Guard([ws], project_root=ws.parent / "project"))
    assert again.version == 1
    tables = {r[0] for r in again.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"config_snapshot", "source", "batch", "upload_item", "run", "run_target", "event"} <= tables
    again.close()


def test_newer_db_rejected(ws, db):
    db.conn.execute("PRAGMA user_version = 99")
    db.close()
    with pytest.raises(st.StoreError, match="새롭다"):
        st.Store(ws, Guard([ws], project_root=ws.parent / "project"))


def test_db_path_goes_through_guard(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(PermissionError):
        st.Store(tmp_path / "workspace", Guard([other], project_root=tmp_path / "project"))


def test_run_lifecycle_records_events(db):
    _, _, _, run = seed(db)
    for to, reason in [("diagnosed", None), ("awaiting_approval", None), ("needs_confirmation", "매핑 수정"),
                       ("diagnosed", None), ("awaiting_approval", None), ("completed", None)]:
        db.transition(run, to, reason)
    assert db.run(run)["status"] == "completed"
    events = db.events("run", run)
    assert events[0]["kind"] == "created"
    changes = [(e["payload"]["from"], e["payload"]["to"]) for e in events if e["kind"] == "status_changed"]
    assert changes[0] == ("uploaded", "diagnosed") and changes[-1] == ("awaiting_approval", "completed")
    assert events[3]["payload"]["reason"] == "매핑 수정"


@pytest.mark.parametrize(
    "path",
    [["completed"], ["diagnosed", "completed"], ["failed", "diagnosed"], ["diagnosed", "awaiting_approval",
                                                                            "completed", "diagnosed"]],
)
def test_invalid_transitions_rejected(db, path):
    """음성 사례: 허용 표 밖의 전이와 끝 상태 이후 전이는 DB가 막는다."""
    _, _, _, run = seed(db)
    with pytest.raises(sqlite3.DatabaseError, match="허용하지 않는"):
        for to in path:
            db.transition(run, to)


def test_direct_sql_bypass_still_blocked(db):
    _, _, _, run = seed(db)
    with pytest.raises(sqlite3.DatabaseError, match="허용하지 않는"):
        db.conn.execute("UPDATE run SET status = 'completed' WHERE id = ?", (run,))
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("UPDATE run SET status = 'done' WHERE id = ?", (run,))
    with pytest.raises(sqlite3.DatabaseError, match="고정 필드"):
        db.conn.execute("UPDATE run SET batch_id = batch_id + 1 WHERE id = ?", (run,))
    db.conn.execute("UPDATE run SET status = 'diagnosed' WHERE id = ?", (run,))
    assert db.events("run", run)[-1]["payload"] == {"from": "uploaded", "to": "diagnosed", "reason": None}


def test_same_status_rejected(db):
    _, _, _, run = seed(db)
    with pytest.raises(st.StoreError, match="이미"):
        db.transition(run, "uploaded")


@pytest.mark.parametrize("table", ["config_snapshot", "source", "batch", "upload_item", "run", "run_target", "event",
                                   "run_transition", "schema_meta"])
def test_no_delete(db, table):
    _, _, items, run = seed(db)
    db.set_targets(run, [{"upload_item_id": items[0], "included": True}])
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db.conn.execute(f"DELETE FROM {table}")


@pytest.mark.parametrize(
    "sql",
    ["UPDATE config_snapshot SET set_name = 'x'", "UPDATE source SET name = 'x'", "UPDATE batch SET source_id = 1",
     "UPDATE upload_item SET filename = 'x.csv'", "UPDATE run_target SET included = 0", "UPDATE event SET kind = 'x'",
     "INSERT INTO run_transition VALUES ('completed', 'uploaded')",
     "INSERT OR REPLACE INTO run_transition VALUES ('uploaded', 'diagnosed')",
     "INSERT OR REPLACE INTO source (id, name) VALUES (1, '바꿔치기')",
     "INSERT OR REPLACE INTO config_snapshot (id, hash, set_name, path, files) VALUES (1, 'b' || substr('"
     + "0" * 63 + "', 1, 63), 's', 'p', '[]')",
     "INSERT INTO schema_meta VALUES ('x', 'y')"],
)
def test_no_update(db, sql):
    _, _, items, run = seed(db)
    db.set_targets(run, [{"upload_item_id": items[0], "included": True}])
    with pytest.raises(sqlite3.DatabaseError):
        db.conn.execute(sql)


def test_transaction_rolls_back(db):
    _, batch, _, _ = seed(db)
    before = db.conn.execute("SELECT COUNT(*) FROM upload_item").fetchone()[0]
    with pytest.raises(RuntimeError):
        with db.tx():
            db.add_upload_item(batch, "x.csv", "c" * 64, "csv", {}, "ok", [])
            raise RuntimeError("중간 실패")
    assert db.conn.execute("SELECT COUNT(*) FROM upload_item").fetchone()[0] == before


def test_targets_versioned(db):
    _, _, items, run = seed(db)
    assert db.set_targets(run, [{"upload_item_id": items[0], "included": True},
                                {"upload_item_id": items[1], "included": False, "exclusion_reason": "사용자 제외"}]) == 1
    assert db.set_targets(run, [{"upload_item_id": items[1], "included": True},
                                {"upload_item_id": items[0], "included": True}]) == 2
    assert [t["upload_item_id"] for t in db.targets(run)] == [items[1], items[0]]
    assert db.conn.execute("SELECT COUNT(*) FROM run_target").fetchone()[0] == 4
    assert [e["kind"] for e in db.events("run", run)].count("targets_set") == 2


def test_target_constraints(db):
    _, _, items, run = seed(db)
    other_batch = db.create_batch(db.source_id("다른 팀"))
    foreign = db.add_upload_item(other_batch, "z.csv", "d" * 64, "csv", {}, "ok", [])
    with pytest.raises(sqlite3.DatabaseError, match="같은 묶음"):
        db.set_targets(run, [{"upload_item_id": foreign, "included": True}])
    with pytest.raises(sqlite3.IntegrityError):
        db.set_targets(run, [{"upload_item_id": items[0], "included": False}])  # 제외 사유 없음
    with pytest.raises(st.StoreError):
        db.set_targets(run, [])
    assert db.targets(run) == []


def test_snapshot_idempotent_and_conflict(db):
    a = db.add_config_snapshot(H, "shop", "p", ["a", "b"])
    assert db.add_config_snapshot(H, "shop", "p", ["a", "b"]) == a
    with pytest.raises(st.StoreError):
        db.add_config_snapshot(H, "shop", "p", ["a"])
    with pytest.raises(sqlite3.IntegrityError):
        db.add_config_snapshot("Z" * 64, "shop", "p", [])


def test_constraints(db):
    _, batch, _, _ = seed(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.add_upload_item(batch, "x.xlsx", "e" * 64, "xlsx", {}, "ok", [])  # 엑셀은 시트 필수
    with pytest.raises(sqlite3.IntegrityError):
        db.add_upload_item(9999, "x.csv", "e" * 64, "csv", {}, "ok", [])  # 외래 키
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO event (subject_type, subject_id, kind, payload) VALUES ('run', 1, 'x', '[1]')")
    with pytest.raises(st.StoreError):
        db.create_run(batch, 1, retry_of=12345)


def test_retry_chain(db):
    snap, batch, _, run = seed(db)
    db.transition(run, "failed", "재검증 신규 문제")
    retry = db.create_run(batch, snap, retry_of=run)
    assert db.run(retry)["retry_of"] == run and db.run(run)["status"] == "failed"
