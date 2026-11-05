"""양식(비교·재사용 단위)과 기준 프로파일 (FS 1절·F10, TD 10-3, M6-2).

- 양식 = (출처, 표준 테이블, header_signature). header_signature는 원래 헤더를 정규화(QE 3-1)·정렬한 목록의
  해시다. 정규화하면 같아지는 헤더 차이는 같은 양식이며, 그 차이는 프로파일의 원래 헤더 목록으로 따로 본다(QE 3-3).
- 실행↔양식 연결은 매핑 버전마다 남긴다(구조 승인 때). 매핑을 다시 확정하면 새 연결이 생기고 예전 것은 그대로다.
- 기준 프로파일은 업로드 실행이 정제 결과를 발행할 때만 저장한다(결정 M6-D3). 실패 실행·복원 버전은 기준이 아니며
  현재 실행은 자기 비교 기준이 될 수 없다(발행 전에는 프로파일이 없다).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from prep.mapping import norm
from prep.store import Store


def header_signature(headers: list[str]) -> tuple[str, list[str]]:
    normalized = sorted(norm(h) for h in headers if h.strip())
    digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode("utf-8")).hexdigest()
    return digest, normalized


def ensure(c: sqlite3.Connection, source_id: int, std_table: str, headers: list[str], run_id: int) -> int:
    """같은 단위의 양식이 있으면 그 ID, 없으면 새로 만든다. 바깥 트랜잭션 안에서 부른다."""
    digest, normalized = header_signature(headers)
    row = c.execute("SELECT id FROM template WHERE source_id = ? AND std_table = ? AND header_signature = ?",
                    (source_id, std_table, digest)).fetchone()
    if row:
        return row["id"]
    tid = c.execute("INSERT INTO template (source_id, std_table, header_signature, headers, created_in_run) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (source_id, std_table, digest, json.dumps(normalized, ensure_ascii=False), run_id)).lastrowid
    c.execute("INSERT INTO event (subject_type, subject_id, kind, payload) VALUES ('template', ?, 'template_created', ?)",
              (tid, json.dumps({"run": run_id, "source": source_id, "std_table": std_table})))
    return tid


def link_mapping(store: Store, run_id: int, mapping_version_id: int, items: list[dict]) -> dict[int, int]:
    """매핑 버전의 실행 대상 항목을 양식에 연결한다. items: {upload_item_id, table, headers}. 반환: 항목 → 양식."""
    with store.tx() as c:
        source_id = c.execute("SELECT b.source_id FROM run r JOIN batch b ON b.id = r.batch_id WHERE r.id = ?",
                              (run_id,)).fetchone()["source_id"]
        out = {}
        for it in items:
            tid = ensure(c, source_id, it["table"], it["headers"], run_id)
            c.execute("INSERT INTO run_template (run_id, mapping_version_id, upload_item_id, template_id) "
                      "VALUES (?, ?, ?, ?)", (run_id, mapping_version_id, it["upload_item_id"], tid))
            out[it["upload_item_id"]] = tid
        return out


def for_run(store: Store, run_id: int) -> dict[int, int]:
    """실행의 최신 매핑 버전 기준 항목 → 양식."""
    rows = store.conn.execute(
        "SELECT upload_item_id, template_id FROM run_template WHERE mapping_version_id = "
        "(SELECT MAX(id) FROM mapping_version WHERE run_id = ?)", (run_id,))
    return {r["upload_item_id"]: r["template_id"] for r in rows}


def get(store: Store, template_id: int) -> dict:
    row = store.conn.execute("SELECT * FROM template WHERE id = ?", (template_id,)).fetchone()
    if row is None:
        raise KeyError(template_id)
    return dict(row) | {"headers": json.loads(row["headers"])}


def for_source(store: Store, source_id: int, std_table: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM template WHERE source_id = ?", [source_id]
    if std_table is not None:
        sql, args = sql + " AND std_table = ?", [*args, std_table]
    return [dict(r) | {"headers": json.loads(r["headers"])} for r in store.conn.execute(sql + " ORDER BY id", args)]


def save_profiles(store: Store, result_version_id: int, run_id: int, profiles: list[dict]) -> None:
    """발행한 정제 결과 버전에 기준 프로파일을 붙인다. profiles: {template_id, upload_item_id, order_no, profile}."""
    with store.tx() as c:
        for p in profiles:
            c.execute("INSERT INTO template_profile (template_id, result_version_id, run_id, upload_item_id, order_no,"
                      " profile) VALUES (?, ?, ?, ?, ?, ?)",
                      (p["template_id"], result_version_id, run_id, p["upload_item_id"], p["order_no"],
                       json.dumps(p["profile"], ensure_ascii=False, sort_keys=True)))


def baseline(store: Store, template_id: int) -> dict | None:
    """양식의 비교 기준: 그 양식 프로파일을 남긴 마지막 정제 결과 버전(복원 제외, AC-08-4·M6-D3)."""
    last = store.conn.execute("SELECT MAX(result_version_id) AS v FROM template_profile WHERE template_id = ?",
                              (template_id,)).fetchone()["v"]
    if last is None:
        return None
    rows = [dict(r) for r in store.conn.execute(
        "SELECT * FROM template_profile WHERE template_id = ? AND result_version_id = ? ORDER BY order_no",
        (template_id, last))]
    return {"template_id": template_id, "result_version_id": last, "run_id": rows[0]["run_id"],
            "profiles": [{"upload_item_id": r["upload_item_id"], "order_no": r["order_no"],
                          "profile": json.loads(r["profile"])} for r in rows]}
