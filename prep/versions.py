"""결과 버전 이력·복원 (FS F08, docs/technical-design.md 10-1절, M5-1).

- 버전은 출처 단위 시간순이다. 출처의 "현재 결과"는 그 출처 실행들의 가장 최근 버전 행이다.
- 복원은 지우거나 덮어쓰지 않고 새 행 `result_version(kind=restore, restored_from=X)`을 만든다.
  데이터 해시·manifest는 X와 같은 것을 가리키고(새 파일·새 변경 내역 없음), 근거(매핑·설정·계획·뷰·승인)는
  원래 정제 버전을 참조로 조회한다. 현재 규칙으로 다시 계산한 값을 붙이지 않는다.
- 복원 이벤트는 `event(subject_type=result_version)`에 원래 정제 변경과 따로 남긴다.
- 되돌리기는 결과에만 적용된다. 승인·매핑·재사용 규칙 기록은 건드리지 않는다(AC-08-3).
- 스키마 변경 비교 기준(F10)은 업로드 실행의 정제 결과만 쓴다. 복원 버전은 기준이 되지 않는다(AC-08-4).
"""

from __future__ import annotations

import json

from prep.store import Store


class VersionError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


_SELECT = """
    SELECT v.*, r.batch_id, b.source_id, r.status AS run_status
    FROM result_version v JOIN run r ON r.id = v.run_id JOIN batch b ON b.id = r.batch_id
"""


def get(store: Store, version_id: int) -> dict:
    row = store.conn.execute(_SELECT + " WHERE v.id = ?", (version_id,)).fetchone()
    if row is None:
        raise VersionError("version_not_found", "결과 버전이 없다", 404)
    return dict(row)


def origin(store: Store, version: dict) -> dict:
    """복원 버전이면 데이터·근거의 출처인 정제 버전까지 따라간다."""
    seen = set()
    while version["kind"] == "restore":
        if version["id"] in seen:
            raise VersionError("version_cycle", "복원 참조가 순환한다", 500)
        seen.add(version["id"])
        version = get(store, version["restored_from"])
    return version


def for_source(store: Store, source_id: int) -> list[dict]:
    rows = [dict(r) for r in store.conn.execute(_SELECT + " WHERE b.source_id = ? ORDER BY v.id", (source_id,))]
    current = rows[-1]["id"] if rows else None
    for r in rows:
        r["current"] = r["id"] == current
        r["data_hashes"] = json.loads(r["data_hashes"])
    return rows


def current(store: Store, source_id: int) -> dict | None:
    rows = for_source(store, source_id)
    return rows[-1] if rows else None


def restore(store: Store, version_id: int) -> dict:
    """선택한 버전으로 되돌린다 = 같은 데이터를 가리키는 새 복원 버전. 이미 현재면 거부."""
    target = get(store, version_id)
    now = current(store, target["source_id"])
    if now is not None and now["id"] == target["id"]:
        raise VersionError("already_current", "이미 현재 결과 버전이다", 409)
    base = origin(store, target)
    with store.tx() as c:
        vid = c.execute(
            "INSERT INTO result_version (run_id, kind, restored_from, data_hashes, manifest_path) "
            "VALUES (?, 'restore', ?, ?, ?)",
            (target["run_id"], target["id"], target["data_hashes"], target["manifest_path"])).lastrowid
        c.execute("INSERT INTO event (subject_type, subject_id, kind, payload) VALUES ('result_version', ?, "
                  "'restored', ?)",
                  (vid, json.dumps({"restored_from": target["id"], "origin": base["id"],
                                    "previous_current": None if now is None else now["id"],
                                    "source_id": target["source_id"]})))
    return get(store, vid)


def refs(store: Store, version: dict) -> dict:
    """당시 근거(AC-08-2): 원래 정제 버전의 실행에서 매핑·설정·계획·뷰·적용 계획·실행 승인 해시."""
    base = origin(store, version)
    run_id = base["run_id"]
    approval = store.conn.execute(
        "SELECT a.* FROM attempt t JOIN approval a ON a.id = t.approval_id WHERE t.id = ?",
        (base["attempt_id"],)).fetchone()
    mapping = store.conn.execute("SELECT version, hash FROM mapping_version WHERE run_id = ? ORDER BY version DESC "
                                 "LIMIT 1", (run_id,)).fetchone()
    snapshot = store.conn.execute("SELECT s.hash FROM run r JOIN config_snapshot s ON s.id = r.config_snapshot_id "
                                  "WHERE r.id = ?", (run_id,)).fetchone()
    hashes = {}
    if approval is not None:
        hashes = {k: approval[k] for k in approval.keys() if k.endswith("_hash")}
    return {"origin": base["id"], "run_id": run_id, "attempt_id": base["attempt_id"],
            "config_snapshot": None if snapshot is None else snapshot["hash"],
            "mapping_version": None if mapping is None else dict(mapping),
            "approval_id": None if approval is None else approval["id"], "approval_hashes": hashes}


def schema_baseline_run(store: Store, source_id: int) -> int | None:
    """스키마 변경 비교 기준(F10, AC-08-4): 이 출처에서 결과를 발행한 마지막 업로드 실행. 복원 버전은 무시한다."""
    row = store.conn.execute(
        "SELECT v.run_id FROM result_version v JOIN run r ON r.id = v.run_id JOIN batch b ON b.id = r.batch_id "
        "WHERE b.source_id = ? AND v.kind = 'cleaned' ORDER BY v.id DESC LIMIT 1", (source_id,)).fetchone()
    return None if row is None else row["run_id"]
