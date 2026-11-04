"""결과 발행 (docs/technical-design.md 8-5절, FS F07·7절, M1-10c).

순서: 엔진 정제(시도 폴더) → 감사 대조(M1-10b) → [정합성 재검사: M4] → 결과 파일 작성 → DB 트랜잭션 하나로
result_version 행 + 시도 마감 + 실행 상태(완료/완료(문제 남음)).
- 완료 판정은 result_version 행 하나다. 행이 없는 시도 폴더는 미완료 흔적이며 다운로드 대상이 아니다.
- 결과 파일: 발행 가능한 테이블만, 보조 컬럼(__extras_digest__)을 빼고 추가 컬럼을 원래 이름으로 다시 붙인다.
  변경 내역은 원본 위치(파일·레코드·물리 줄)로 쓴다. 원래 없던 셀은 absent-cells.json에 남긴다.
- 다운로드할 때 파일 해시를 다시 대조하고 다르면 오류.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
from pathlib import Path

from diagnostics.engine import read_table
from prep.attempt import Attempt
from prep.guard import Guard
from prep.store import Store
from prep.view import EXTRAS, Locator

RESULT_DIR = "result"


class PublishError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("utf-8")


def write_files(attempt: Attempt, lineage: dict, tables: dict, names: dict[int, str], guard: Guard) -> dict:
    """attempt-n/result/ 에 결과를 쓴다(임시 폴더 → rename). tables: 실행 계획의 테이블 판정."""
    target = guard.safe_join(attempt.folder, RESULT_DIR)
    if target.exists():
        raise PublishError("결과 폴더가 이미 있다")
    stage = guard.safe_join(attempt.folder, ".result-staging")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    loc = Locator(lineage)
    files, excluded, absent = {}, {}, []
    try:
        for table, spec in sorted(lineage["tables"].items()):
            judged = tables.get(table)
            if judged is not None and not judged["publishable"]:
                excluded[table] = judged["reason"]
                continue
            data = read_table(attempt.engine / "data" / f"{table}.csv", "utf-8", ",")
            logical = [h for h in data["headers"] if h != EXTRAS]
            extras = spec["extra_columns"]
            rows = [logical + extras]
            by_record = {r["record"]: r for r in spec["rows"]}
            for row in data["rows"]:
                lin = by_record[row["record"]]
                rows.append([row["values"][h] for h in logical] + [lin["extras"].get(e, "") for e in extras])
            body = _csv(rows)
            (stage / f"{table}.csv").write_bytes(body)
            files[f"{table}.csv"] = {"sha256": _sha(body), "rows": len(rows) - 1, "columns": len(rows[0])}
            for record, col in spec["absent"]:
                absent.append({"table": table, **loc.source(table, record, [col]), "column": col})
            for e in extras:
                missing_in = [it["item_id"] for it in spec["items"] if e not in it["extra_columns"]]
                for r in spec["rows"]:
                    if r["item_id"] in missing_in:
                        absent.append({"table": table, "item_id": r["item_id"], "record": r["source_record"],
                                       "column": e, "extra": True})
        changes = json.loads((attempt.engine / "changes.json").read_text(encoding="utf-8"))
        rows = [["table", "file", "source_record", "line_start", "line_end", "column", "original_column", "before",
                 "after", "logical_after", "codes", "sheet", "cell"]]  # 엑셀은 줄 번호 대신 시트·셀 좌표
        for c in loc.translate_changes(changes):
            if c["table"] in excluded:
                continue
            s = c["source"]
            rows.append([c["table"], names.get(s["item_id"], ""), s["record"], s["line_start"], s["line_end"],
                         c["column"], s["columns"].get(c["column"]) or "", c["before"], c["after"],
                         json.dumps(c["logical_after"], ensure_ascii=False), ",".join(c["codes"]),
                         s.get("sheet") or "", (s.get("cells") or {}).get(c["column"]) or ""])
        body = _csv(rows)
        (stage / "changes.csv").write_bytes(body)
        files["changes.csv"] = {"sha256": _sha(body), "rows": len(rows) - 1}
        body = json.dumps(absent, ensure_ascii=False, indent=2).encode("utf-8")
        (stage / "absent-cells.json").write_bytes(body)
        files["absent-cells.json"] = {"sha256": _sha(body), "rows": len(absent)}
        # 내부 검토용 근거(FS F07 출력): 변경 내역 JSON(논리 null 구분)·전후 진단·보류 목록, 모두 원본 위치 포함
        translated = [c for c in loc.translate_changes(changes) if c["table"] not in excluded]
        extras = {"changes.json": translated}
        for name, out in (("before.json", "diagnosis-before.json"), ("after.json", "diagnosis-after.json")):
            extras[out] = loc.translate_report(json.loads((attempt.engine / name).read_text(encoding="utf-8")))
        integrity = attempt.engine / "integrity.json"
        if integrity.is_file():  # 발행 전 정합성 재검사(M4-3) — 관련 행 원본 위치 포함
            after = json.loads(integrity.read_text(encoding="utf-8"))
            after["violations"] = [v | {"sources": [loc.source(t, r) | {"table": t} for t, r in v["rows"]]}
                                   for v in after["violations"]]
            extras["integrity.json"] = after
        held = json.loads((attempt.engine / "held.json").read_text(encoding="utf-8"))
        extras["held.json"] = [h | {"source": loc.source(h["table"], h["record"], h["columns"])}
                               if h["table"] in loc.tables else h for h in held]
        for name, obj in extras.items():
            body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
            (stage / name).write_bytes(body)
            rows = len(obj.get("issues", obj.get("violations", []))) if isinstance(obj, dict) else len(obj)
            files[name] = {"sha256": _sha(body), "rows": rows}
        manifest = {"files": files, "excluded_tables": excluded, "attempt": attempt.number}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for p in stage.iterdir():
            os.chmod(p, 0o444)
        os.rename(stage, target)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return manifest


def record(store: Store, run_id: int, attempt: Attempt, manifest: dict, status: str, audit: dict,
           guard: Guard) -> int:
    """DB 트랜잭션 하나: result_version + 시도 마감 + 실행 상태."""
    hashes = {name: meta["sha256"] for name, meta in manifest["files"].items()}
    path = str(guard.safe_join(attempt.folder, RESULT_DIR, "manifest.json").relative_to(guard.roots[0]))
    with store.tx() as c:
        rid = c.execute("INSERT INTO result_version (run_id, kind, attempt_id, data_hashes, manifest_path) "
                        "VALUES (?, 'cleaned', ?, ?, ?)",
                        (run_id, attempt.id, json.dumps(hashes), path)).lastrowid
        c.execute("UPDATE attempt SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), outcome = 'engine_ok', "
                  "audit = ? WHERE id = ?", (json.dumps(audit, ensure_ascii=False), attempt.id))
        c.execute("UPDATE run SET status = ?, status_reason = 'published', "
                  "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?", (status, run_id))
    return rid


def latest(store: Store, run_id: int) -> dict | None:
    r = store.conn.execute("SELECT * FROM result_version WHERE run_id = ? ORDER BY id DESC LIMIT 1",
                           (run_id,)).fetchone()
    return None if r is None else dict(r)


def read_verified(guard: Guard, root: Path, version: dict, name: str) -> bytes:
    """발행 파일을 해시 대조 후 돌려준다. manifest.json은 적힌 파일 해시가 DB 기록과 같은지로 대조한다."""
    hashes = json.loads(version["data_hashes"])
    if name == "manifest.json":
        data = guard.safe_join(root, version["manifest_path"]).read_bytes()
        listed = {n: m["sha256"] for n, m in json.loads(data)["files"].items()}
        if listed != hashes:
            raise PublishError("발행된 manifest가 결과 버전 기록과 다르다")
        return data
    if name not in hashes:
        raise FileNotFoundError(name)
    folder = guard.safe_join(root, version["manifest_path"]).parent
    data = guard.safe_join(folder, name).read_bytes()
    if _sha(data) != hashes[name]:
        raise PublishError(f"발행된 결과 파일이 바뀌었다: {name}")
    return data
