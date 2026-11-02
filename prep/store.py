"""SQLite 저장 계층 (docs/technical-design.md 4절, M1-3b).

원칙(TD 4-1):
- 메타데이터는 workspace/app.db 하나. 기록은 덧붙이기만 한다(모든 테이블 DELETE 금지, 내용 UPDATE 금지).
- 바꿀 수 있는 것은 현재 상태 필드뿐(run.status·status_reason·updated_at). 상태 전이는 허용 표에 있는 것만 되고,
  전이마다 event가 같은 트랜잭션에서 자동으로 남는다(트리거). 앱 코드가 우회해도 DB가 막는다.
- 실행 대상처럼 다시 확정할 수 있는 것은 버전을 올려 새 행으로 남긴다.
- 스키마는 마이그레이션 목록으로만 바꾼다(PRAGMA user_version). 뒤 단계 테이블은 해당 작업에서 추가한다.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from prep.guard import Guard

DB_NAME = "app.db"

RUN_STATUSES = (
    "uploaded",  # 업로드됨
    "needs_confirmation",  # 확인 필요
    "diagnosed",  # 진단 완료
    "awaiting_approval",  # 승인 대기
    "completed",  # 완료
    "completed_with_issues",  # 완료(문제 남음)
    "failed",  # 실패
)

# 허용 전이. 완료·실패는 끝 상태다(재시도는 새 실행, 되돌리기는 새 결과 버전 — FS 6절).
RUN_TRANSITIONS = {
    "uploaded": ("needs_confirmation", "diagnosed", "failed"),
    "needs_confirmation": ("diagnosed", "failed"),
    "diagnosed": ("needs_confirmation", "awaiting_approval", "failed"),
    "awaiting_approval": ("needs_confirmation", "diagnosed", "completed", "completed_with_issues", "failed"),
}

_NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
_IMMUTABLE = ("schema_meta", "run_transition", "config_snapshot", "source", "batch", "upload_item", "run_target",
              "event")
_ALL_TABLES = _IMMUTABLE + ("run",)


def _guards(tables: tuple[str, ...], all_tables: tuple[str, ...]) -> str:
    sql = []
    for t in all_tables:
        sql.append(f"CREATE TRIGGER {t}_no_delete BEFORE DELETE ON {t} "
                   f"BEGIN SELECT RAISE(ABORT, 'append-only: {t} 행은 지울 수 없다'); END;")
    for t in tables:
        sql.append(f"CREATE TRIGGER {t}_no_update BEFORE UPDATE ON {t} "
                   f"BEGIN SELECT RAISE(ABORT, 'append-only: {t} 행은 바꿀 수 없다'); END;")
    return "\n".join(sql)


_STATUS_CHECK = ", ".join(f"'{s}'" for s in RUN_STATUSES)
_TRANSITION_ROWS = ", ".join(f"('{a}', '{b}')" for a, bs in RUN_TRANSITIONS.items() for b in bs)

MIGRATIONS: list[str] = [
    # 1: M1-3b 기반 테이블
    f"""
    CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO schema_meta VALUES ('created_by', 'M1-3b');

    CREATE TABLE run_transition (from_status TEXT NOT NULL, to_status TEXT NOT NULL,
        PRIMARY KEY (from_status, to_status));
    INSERT INTO run_transition VALUES {_TRANSITION_ROWS};

    CREATE TABLE config_snapshot (
        id INTEGER PRIMARY KEY,
        hash TEXT NOT NULL UNIQUE CHECK (length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*'),
        set_name TEXT NOT NULL,
        path TEXT NOT NULL,
        files TEXT NOT NULL CHECK (json_valid(files) AND json_type(files) = 'array'),
        created_at TEXT NOT NULL DEFAULT {_NOW});

    CREATE TABLE source (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE CHECK (length(trim(name)) > 0),
        created_at TEXT NOT NULL DEFAULT {_NOW});

    CREATE TABLE batch (
        id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL REFERENCES source(id),
        created_at TEXT NOT NULL DEFAULT {_NOW});

    CREATE TABLE upload_item (
        id INTEGER PRIMARY KEY,
        batch_id INTEGER NOT NULL REFERENCES batch(id),
        filename TEXT NOT NULL CHECK (length(filename) > 0),
        blob_sha256 TEXT NOT NULL CHECK (length(blob_sha256) = 64),
        kind TEXT NOT NULL CHECK (kind IN ('csv', 'xlsx')),
        sheet TEXT,
        read_options TEXT NOT NULL CHECK (json_valid(read_options)),
        read_status TEXT NOT NULL CHECK (read_status IN ('ok', 'error')),
        read_errors TEXT NOT NULL CHECK (json_valid(read_errors) AND json_type(read_errors) = 'array'),
        created_at TEXT NOT NULL DEFAULT {_NOW},
        CHECK ((kind = 'xlsx') = (sheet IS NOT NULL)));

    CREATE TABLE run (
        id INTEGER PRIMARY KEY,
        batch_id INTEGER NOT NULL REFERENCES batch(id),
        retry_of INTEGER REFERENCES run(id),
        config_snapshot_id INTEGER NOT NULL REFERENCES config_snapshot(id),
        status TEXT NOT NULL DEFAULT 'uploaded' CHECK (status IN ({_STATUS_CHECK})),
        status_reason TEXT,
        created_at TEXT NOT NULL DEFAULT {_NOW},
        updated_at TEXT NOT NULL DEFAULT {_NOW});

    CREATE TABLE run_target (
        run_id INTEGER NOT NULL REFERENCES run(id),
        version INTEGER NOT NULL CHECK (version >= 1),
        upload_item_id INTEGER NOT NULL REFERENCES upload_item(id),
        order_no INTEGER NOT NULL CHECK (order_no >= 1),
        included INTEGER NOT NULL CHECK (included IN (0, 1)),
        exclusion_reason TEXT,
        PRIMARY KEY (run_id, version, upload_item_id),
        UNIQUE (run_id, version, order_no),
        CHECK ((included = 1) = (exclusion_reason IS NULL)));

    CREATE TABLE event (
        id INTEGER PRIMARY KEY,
        subject_type TEXT NOT NULL,
        subject_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{{}}' CHECK (json_valid(payload) AND json_type(payload) = 'object'),
        created_at TEXT NOT NULL DEFAULT {_NOW});
    CREATE INDEX event_subject ON event(subject_type, subject_id, id);

    {_guards(_IMMUTABLE, _ALL_TABLES)}

    -- 허용 전이 표와 스키마 정보는 마이그레이션 밖에서 늘릴 수 없다.
    CREATE TRIGGER run_transition_no_insert BEFORE INSERT ON run_transition
    BEGIN SELECT RAISE(ABORT, 'append-only: run_transition은 마이그레이션으로만 바꾼다'); END;
    CREATE TRIGGER schema_meta_no_insert BEFORE INSERT ON schema_meta
    BEGIN SELECT RAISE(ABORT, 'append-only: schema_meta는 마이그레이션으로만 바꾼다'); END;

    -- run: 고정 필드는 바꿀 수 없다.
    CREATE TRIGGER run_fixed_fields BEFORE UPDATE ON run
    WHEN NEW.id IS NOT OLD.id OR NEW.batch_id IS NOT OLD.batch_id OR NEW.retry_of IS NOT OLD.retry_of
      OR NEW.config_snapshot_id IS NOT OLD.config_snapshot_id OR NEW.created_at IS NOT OLD.created_at
    BEGIN SELECT RAISE(ABORT, 'run의 고정 필드는 바꿀 수 없다'); END;

    -- run: 상태는 허용 표에 있는 전이만.
    CREATE TRIGGER run_status_transition BEFORE UPDATE OF status ON run
    WHEN NEW.status IS NOT OLD.status AND NOT EXISTS (
        SELECT 1 FROM run_transition WHERE from_status = OLD.status AND to_status = NEW.status)
    BEGIN SELECT RAISE(ABORT, '허용하지 않는 실행 상태 전이'); END;

    -- run: 상태 전이는 같은 트랜잭션에서 자동으로 event에 남는다.
    CREATE TRIGGER run_status_event AFTER UPDATE OF status ON run
    WHEN NEW.status IS NOT OLD.status
    BEGIN
        INSERT INTO event (subject_type, subject_id, kind, payload)
        VALUES ('run', NEW.id, 'status_changed',
                json_object('from', OLD.status, 'to', NEW.status, 'reason', NEW.status_reason));
    END;

    CREATE TRIGGER run_created_event AFTER INSERT ON run
    BEGIN
        INSERT INTO event (subject_type, subject_id, kind, payload)
        VALUES ('run', NEW.id, 'created', json_object('status', NEW.status, 'retry_of', NEW.retry_of));
    END;

    -- 실행 대상 행은 그 실행의 묶음에 속한 업로드 항목만.
    CREATE TRIGGER run_target_same_batch BEFORE INSERT ON run_target
    WHEN (SELECT batch_id FROM upload_item WHERE id = NEW.upload_item_id)
         IS NOT (SELECT batch_id FROM run WHERE id = NEW.run_id)
    BEGIN SELECT RAISE(ABORT, '실행 대상은 같은 묶음의 업로드 항목이어야 한다'); END;
    """,
]


class StoreError(RuntimeError):
    pass


class Store:
    def __init__(self, workspace: Path, guard: Guard):
        self.path = guard.safe_join(workspace, DB_NAME)
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # INSERT OR REPLACE의 내부 삭제도 삭제 금지 트리거를 거치게 한다.
        self.conn.execute("PRAGMA recursive_triggers = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """쓰기 트랜잭션. 안에서 예외가 나면 전부 되돌린다."""
        if self.conn.in_transaction:
            yield self.conn  # 바깥 트랜잭션에 합류
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    @property
    def version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> None:
        current = self.version
        if current > len(MIGRATIONS):
            raise StoreError(f"DB 스키마 버전 {current}이 코드({len(MIGRATIONS)})보다 새롭다")
        for number, sql in enumerate(MIGRATIONS[current:], start=current + 1):
            with self.tx() as c:
                for statement in _split(sql):
                    c.execute(statement)
                c.execute(f"PRAGMA user_version = {number}")

    # ---- 기록 ----
    def add_config_snapshot(self, hash_: str, set_name: str, path: str, files: list[str]) -> int:
        with self.tx() as c:
            row = c.execute("SELECT id, set_name, files FROM config_snapshot WHERE hash = ?", (hash_,)).fetchone()
            if row:
                if row["set_name"] != set_name or json.loads(row["files"]) != list(files):
                    raise StoreError("같은 해시의 스냅샷이 다른 내용으로 기록돼 있다")
                return row["id"]
            return c.execute("INSERT INTO config_snapshot (hash, set_name, path, files) VALUES (?, ?, ?, ?)",
                             (hash_, set_name, path, json.dumps(list(files)))).lastrowid

    def source_id(self, name: str) -> int:
        with self.tx() as c:
            row = c.execute("SELECT id FROM source WHERE name = ?", (name,)).fetchone()
            return row["id"] if row else c.execute("INSERT INTO source (name) VALUES (?)", (name,)).lastrowid

    def create_batch(self, source_id: int) -> int:
        with self.tx() as c:
            return c.execute("INSERT INTO batch (source_id) VALUES (?)", (source_id,)).lastrowid

    def add_upload_item(self, batch_id: int, filename: str, blob_sha256: str, kind: str, read_options: dict,
                        read_status: str, read_errors: list, sheet: str | None = None) -> int:
        with self.tx() as c:
            return c.execute(
                "INSERT INTO upload_item (batch_id, filename, blob_sha256, kind, sheet, read_options, read_status,"
                " read_errors) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, filename, blob_sha256, kind, sheet, json.dumps(read_options, ensure_ascii=False),
                 read_status, json.dumps(read_errors, ensure_ascii=False))).lastrowid

    def create_run(self, batch_id: int, config_snapshot_id: int, retry_of: int | None = None) -> int:
        with self.tx() as c:
            if retry_of is not None:
                prev = c.execute("SELECT batch_id, status FROM run WHERE id = ?", (retry_of,)).fetchone()
                if prev is None or prev["batch_id"] != batch_id:
                    raise StoreError("재시도는 같은 묶음의 이전 실행이어야 한다")
            return c.execute("INSERT INTO run (batch_id, config_snapshot_id, retry_of) VALUES (?, ?, ?)",
                             (batch_id, config_snapshot_id, retry_of)).lastrowid

    def transition(self, run_id: int, to: str, reason: str | None = None) -> None:
        with self.tx() as c:
            row = c.execute("SELECT status FROM run WHERE id = ?", (run_id,)).fetchone()
            if row is not None and row["status"] == to:
                raise StoreError(f"이미 {to} 상태다")
            cur = c.execute("UPDATE run SET status = ?, status_reason = ?, updated_at = "
                            "strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?", (to, reason, run_id))
            if cur.rowcount != 1:
                raise StoreError(f"실행이 없다: {run_id}")

    def set_targets(self, run_id: int, targets: list[dict]) -> int:
        """실행 대상과 순서를 새 버전으로 확정한다. targets: {upload_item_id, included, exclusion_reason}."""
        if not targets:
            raise StoreError("실행 대상이 비어 있다")
        with self.tx() as c:
            version = c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM run_target WHERE run_id = ?",
                                (run_id,)).fetchone()[0]
            for order_no, t in enumerate(targets, start=1):
                c.execute("INSERT INTO run_target (run_id, version, upload_item_id, order_no, included,"
                          " exclusion_reason) VALUES (?, ?, ?, ?, ?, ?)",
                          (run_id, version, t["upload_item_id"], order_no, int(t["included"]),
                           t.get("exclusion_reason")))
            self.record_event("run", run_id, "targets_set", {"version": version, "count": len(targets)})
            return version

    def record_event(self, subject_type: str, subject_id: int, kind: str, payload: dict | None = None) -> int:
        with self.tx() as c:
            return c.execute("INSERT INTO event (subject_type, subject_id, kind, payload) VALUES (?, ?, ?, ?)",
                             (subject_type, subject_id, kind, json.dumps(payload or {}, ensure_ascii=False))
                             ).lastrowid

    # ---- 조회 ----
    def run(self, run_id: int) -> dict:
        row = self.conn.execute("SELECT * FROM run WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise StoreError(f"실행이 없다: {run_id}")
        return dict(row)

    def sources(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM source ORDER BY name")]

    def upload_items(self, batch_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM upload_item WHERE batch_id = ? ORDER BY id",
                                                   (batch_id,))]

    def targets(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM run_target WHERE run_id = ? AND version = "
            "(SELECT MAX(version) FROM run_target WHERE run_id = ?) ORDER BY order_no", (run_id, run_id))
        return [dict(r) for r in rows]

    def events(self, subject_type: str, subject_id: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM event WHERE subject_type = ? AND subject_id = ? ORDER BY id",
                                 (subject_type, subject_id))
        return [dict(r) | {"payload": json.loads(r["payload"])} for r in rows]


def _split(sql: str) -> list[str]:
    """트리거 본문(BEGIN…END) 안의 ;를 지키며 문장을 나눈다."""
    statements, buf = [], []
    for line in sql.splitlines():
        if not line.strip() or line.strip().startswith("--"):
            continue
        buf.append(line)
        joined = "\n".join(buf)
        if sqlite3.complete_statement(joined):
            statements.append(joined.strip())
            buf = []
    if buf and "\n".join(buf).strip():
        raise StoreError("마이그레이션 SQL이 끝나지 않았다")
    return statements
