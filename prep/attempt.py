"""정제 시도와 감사 대조 (docs/technical-design.md 8-4·8-5절, FS F07 AC-07-1·07-5, M1-10b).

- 엔진 정제는 서비스가 만든 시도 폴더 runs/<실행>/attempt-<n>/engine/ 에 쓴다. 기존 시도는 덮어쓰지 않는다.
  시도 폴더는 완료 판정이 아니다(완료는 M1-10c의 result_version 하나).
- 감사 대조는 엔진의 자체 검증과 별개로 서비스가 한다:
  ① 변경 내역의 셀 = 적용 계획의 허용 셀(빠짐·초과 없음)
  ② 결과 CSV 전체 셀: 허용 셀은 변경 내역의 값, 나머지는 표준화 뷰 값과 같음(헤더·행 수·행 순서 포함)
  ③ 표준화 뷰와 보관 원본이 승인 때 해시 그대로
  하나라도 어긋나면 시도는 audit_failed이고 결과로 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from diagnostics.cleaning import clean
from diagnostics.engine import read_table
from prep.guard import Guard
from prep.store import Store
from prep.view import view_hash


class AuditError(ValueError):
    def __init__(self, message: str, audit: dict):
        super().__init__(message)
        self.audit = audit


@dataclass
class Attempt:
    id: int
    number: int
    folder: Path

    @property
    def engine(self) -> Path:
        return self.folder / "engine"


def start(store: Store, guard: Guard, run_id: int, run_folder: Path, approval_id: int) -> Attempt:
    with store.tx() as c:
        number = c.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM attempt WHERE run_id = ?",
                           (run_id,)).fetchone()[0]
        folder = guard.safe_join(run_folder, f"attempt-{number}")
        folder.mkdir(parents=False, exist_ok=False)  # AC-07-5: 기존 결과 폴더는 덮어쓰지 않는다
        aid = c.execute("INSERT INTO attempt (run_id, number, approval_id, path) VALUES (?, ?, ?, ?)",
                        (run_id, number, approval_id, str(folder.relative_to(guard.roots[0])))).lastrowid
    return Attempt(aid, number, folder)


def finish(store: Store, attempt: Attempt, outcome: str, error: str | None = None, audit: dict | None = None) -> None:
    with store.tx() as c:
        c.execute("UPDATE attempt SET ended_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), outcome = ?, error = ?, "
                  "audit = ? WHERE id = ?",
                  (outcome, error, None if audit is None else json.dumps(audit, ensure_ascii=False), attempt.id))


def run_engine(attempt: Attempt, view_dir: Path, config: dict, allow: list) -> dict:
    """엔진 확장 ③으로 허용 셀만 정제한다. 실패하면 엔진 예외를 그대로 올린다."""
    return clean(view_dir, config, attempt.engine, allow={tuple(k) for k in allow})


def audit(attempt: Attempt, view_dir: Path, guard: Guard, allow: list, approved_view_hash: str,
          sources: dict[str, bytes] | None = None, source_hashes: dict[str, str] | None = None) -> dict:
    """감사 대조. 통과하면 요약을 돌려주고, 어긋나면 AuditError."""
    problems: list[str] = []
    allowed = {tuple(k) for k in allow}
    changes = json.loads((attempt.engine / "changes.json").read_text(encoding="utf-8"))
    changed = {(c["table"], c["record"], c["column"]): c for c in changes}
    extra, missing = sorted(set(changed) - allowed), sorted(allowed - set(changed))
    if extra:
        problems.append(f"승인 안 한 셀이 바뀌었다: {extra[:5]}")
    if missing:
        problems.append(f"승인한 셀이 바뀌지 않았다: {missing[:5]}")
    if view_hash(view_dir, guard) != approved_view_hash:
        problems.append("표준화 뷰가 승인 이후 바뀌었다")
    cells = 0
    for path in sorted(p for p in view_dir.glob("*.csv")):
        table = path.stem
        before = read_table(path, "utf-8", ",")
        out_path = attempt.engine / "data" / path.name
        if not out_path.is_file():
            problems.append(f"결과 파일이 없다: {path.name}")
            continue
        after = read_table(out_path, "utf-8", ",")
        if before["headers"] != after["headers"] or len(before["rows"]) != len(after["rows"]):
            problems.append(f"{table}: 헤더 또는 행 수가 다르다")
            continue
        for b, a in zip(before["rows"], after["rows"], strict=True):
            for col, value in b["values"].items():
                cells += 1
                key = (table, b["record"], col)
                expected = changed[key]["after"] if key in changed and key in allowed else value
                if a["values"][col] != expected:
                    problems.append(f"{table} {b['record']}행 {col}: 결과 {a['values'][col]!r} ≠ 기대 {expected!r}")
                    if len(problems) > 20:
                        break
    for name, data in (sources or {}).items():
        if hashlib.sha256(data).hexdigest() != (source_hashes or {}).get(name):
            problems.append(f"보관 원본이 바뀌었다: {name}")
    report = {"changed_cells": len(changed), "allowed_cells": len(allowed), "checked_cells": cells,
              "problems": problems[:20], "passed": not problems}
    (attempt.folder / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if problems:
        raise AuditError(problems[0], report)
    return report
