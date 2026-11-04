"""업로드·원본 보관 (FS F01, docs/technical-design.md 4·7절, M1-4).

- 원본은 바이트 그대로 workspace/blobs/<sha256>에 내용 주소로 보관한다(읽기 전용, 읽을 때마다 해시 대조).
- 업로드 항목마다 식별자를 따로 준다. 이름이 같은 파일도 다른 항목이다(AC-01-4).
- CSV는 진단 엔진의 read_table()로 읽어 같은 계약(모든 셀 문자열, 빈 값·공백·NULL 구분)을 쓴다.
- 구조 오류(빈·중복 헤더, 열 개수 불일치, 인코딩·인용 오류)는 위치·이유와 함께 기록하고 그 항목은 실행 대상에서 빠진다.
- 출처 없이 올릴 수 없다(AC-01-5).
- xlsx는 시트마다 업로드 항목 하나(읽기 조건 {format: xlsx, sheet}, M4-5). 원본 바이트는 파일 하나로 보관한다.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from diagnostics.engine import read_table
from prep.guard import Guard
from prep.sheets import read_workbook
from prep.store import Store

ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")
DELIMITERS = (",", ";", "\t", "|")
MAX_BYTES = 50 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{64}")
_RECORD = re.compile(r"Record (\d+):")


class UploadError(ValueError):
    pass


class BlobIntegrityError(UploadError):
    pass


class BlobStore:
    def __init__(self, workspace: Path, guard: Guard):
        self.guard = guard
        self.root = guard.safe_join(workspace, "blobs")
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, sha256: str) -> Path:
        if not SHA.fullmatch(sha256 or ""):
            raise UploadError(f"원본 해시가 올바르지 않다: {sha256!r}")
        return self.guard.safe_join(self.root, sha256)

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        target = self.path(digest)
        if target.exists():
            self.get(digest)  # 이미 있는 원본도 내용을 확인한다
            return digest
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o444)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return digest

    def get(self, sha256: str) -> bytes:
        data = self.path(sha256).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise BlobIntegrityError(f"보관 원본이 바뀌었다: {sha256}")
        return data


@dataclass
class ReadResult:
    status: str  # ok | error
    errors: list[dict] = field(default_factory=list)
    rows: int = 0
    headers: list[str] = field(default_factory=list)


def check_options(encoding: str, delimiter: str) -> dict:
    if encoding not in ENCODINGS:
        raise UploadError(f"지원하지 않는 인코딩: {encoding}")
    if delimiter not in DELIMITERS:
        raise UploadError(f"지원하지 않는 구분자: {delimiter!r}")
    return {"encoding": encoding, "delimiter": delimiter}


def check_filename(name: str) -> str:
    if not name or name != os.path.basename(name) or "\\" in name or len(name) > 255:
        raise UploadError(f"허용하지 않는 파일 이름: {name!r}")
    if any(ord(ch) < 32 for ch in name) or name.startswith("."):
        raise UploadError(f"허용하지 않는 파일 이름: {name!r}")
    lower = name.lower()
    if not (lower.endswith(".csv") and len(name) > 4 or lower.endswith(".xlsx") and len(name) > 5):
        raise UploadError(f"CSV·xlsx 파일만 올릴 수 있다: {name!r}")
    return name


def read_xlsx(data: bytes) -> list[tuple[str, dict, ReadResult]]:
    """시트별 (시트 이름, 읽기 조건, 결과). 파일 자체를 열 수 없으면 시트 이름 ''인 오류 하나."""
    try:
        sheets = read_workbook(data)
    except Exception as exc:  # 손상·암호 걸린 파일 등
        return [("", {"format": "xlsx", "sheet": ""},
                 ReadResult("error", [{"record": None, "sheet": "", "cell": None,
                                      "reason": f"엑셀 파일을 열 수 없다: {type(exc).__name__}"}]))]
    out = []
    for s in sheets:
        errors = [{"record": None, **e} for e in s["errors"]]
        out.append((s["sheet"], {"format": "xlsx", "sheet": s["sheet"]},
                    ReadResult(s["status"], errors, len(s["rows"]), s["headers"])))
    return out


def read_csv(path: Path, options: dict) -> ReadResult:
    """엔진 read_table()로 읽는다. 구조 오류는 레코드 번호(알 수 있으면)·이유로 돌려준다."""
    try:
        table = read_table(path, options["encoding"], options["delimiter"])
    except UnicodeError as exc:
        return ReadResult("error", [{"record": None, "reason": f"인코딩 {options['encoding']}으로 읽을 수 없다: {exc}"}])
    except csv.Error as exc:
        return ReadResult("error", [{"record": None, "reason": f"CSV 형식 오류: {exc}"}])
    except ValueError as exc:
        m = _RECORD.match(str(exc))
        return ReadResult("error", [{"record": int(m.group(1)) if m else None, "reason": str(exc)}])
    return ReadResult("ok", [], len(table["rows"]), table["headers"])


def ingest(store: Store, blobs: BlobStore, source_name: str, files: list[tuple[str, bytes]],
           encoding: str = "utf-8-sig", delimiter: str = ",") -> dict:
    """출처·묶음·업로드 항목을 기록한다. 원본 보관이 끝난 뒤 DB 기록은 한 트랜잭션으로 한다."""
    source_name = (source_name or "").strip()
    if not source_name:
        raise UploadError("출처가 필요하다")
    if not files:
        raise UploadError("CSV 파일이 필요하다")
    options = check_options(encoding, delimiter)
    prepared = []
    for name, data in files:
        check_filename(name)
        if len(data) > MAX_BYTES:
            raise UploadError(f"파일이 너무 크다: {name}")
        digest = blobs.put(data)
        if name.lower().endswith(".xlsx"):
            for sheet, opts, read in read_xlsx(data):
                prepared.append((name, digest, len(data), read, "xlsx", sheet, opts))
        else:
            prepared.append((name, digest, len(data), read_csv(blobs.path(digest), options), "csv", None, options))
    with store.tx():
        source_id = store.source_id(source_name)
        batch_id = store.create_batch(source_id)
        items = []
        for name, digest, size, read, kind, sheet, opts in prepared:
            item_id = store.add_upload_item(batch_id, name, digest, kind, opts, read.status, read.errors, sheet)
            items.append({"id": item_id, "filename": name, "sheet": sheet, "sha256": digest, "bytes": size,
                          "rows": read.rows, "headers": read.headers, "read_options": opts,
                          "read_status": read.status, "read_errors": read.errors})
        store.record_event("batch", batch_id, "uploaded", {"items": len(items), "source": source_name})
    return {"source_id": source_id, "batch_id": batch_id, "items": items}


def item_rows(blobs: BlobStore, item: dict) -> ReadResult:
    """보관 원본을 다시 읽는다(해시 대조 포함)."""
    data = blobs.get(item["blob_sha256"])
    options = json.loads(item["read_options"])
    if options.get("format") == "xlsx":
        return next(r for sheet, _, r in read_xlsx(data) if sheet == options["sheet"])
    return read_csv(blobs.path(item["blob_sha256"]), options)
