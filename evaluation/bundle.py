"""서비스 내보내기 폴더 읽기 (prep/export.py의 export-v1, M2-2).

채점기는 서비스 내부(DB·run.json)를 보지 않고 이 폴더만 읽는다. 적힌 해시와 실제 파일이 다르거나
목록에 없는 파일이 있으면 채점하지 않는다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

FORMAT = "export-v1"


class BundleError(ValueError):
    pass


@dataclass
class Bundle:
    folder: Path
    meta: dict
    inputs: list[dict]
    mapping: dict[int, dict]
    lineage: dict | None
    diagnosis: dict | None
    changes: list[dict]
    held: list[dict]
    not_approved: list[dict]
    result: dict[str, dict] = field(default_factory=dict)  # 테이블 → {header, rows}

    @property
    def published(self) -> bool:
        return bool(self.meta.get("published"))

    def items_named(self, name: str) -> list[dict]:
        return [i for i in self.inputs if i["name"] == name]


def _read_json(folder: Path, rel: str, files: dict, default=None):
    if rel not in files:
        return default
    return json.loads((folder / rel).read_text(encoding="utf-8"))


def read_csv(data: bytes, encoding: str = "utf-8", delimiter: str = ",") -> tuple[list[str], list[list[str]]]:
    rows = list(csv.reader(io.StringIO(data.decode(encoding), newline=""), delimiter=delimiter, strict=True))
    if not rows:
        return [], []
    return rows[0], rows[1:]


def load_bundle(folder: Path) -> Bundle:
    folder = Path(folder)
    meta_path = folder / "export.json"
    if not meta_path.is_file():
        raise BundleError("export.json이 없다")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("format") != FORMAT:
        raise BundleError(f"내보내기 형식이 다르다: {meta.get('format')!r}")
    files = meta.get("files") or {}
    present = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()} - {"export.json"}
    if present != set(files):
        raise BundleError(f"목록과 실제 파일이 다르다: 더 있음 {sorted(present - set(files))[:5]}, "
                          f"없음 {sorted(set(files) - present)[:5]}")
    for rel, sha in files.items():
        if hashlib.sha256((folder / rel).read_bytes()).hexdigest() != sha:
            raise BundleError(f"내보낸 파일이 바뀌었다: {rel}")
    mapping = {m["item_id"]: m for m in _read_json(folder, "mapping.json", files, [])}
    bundle = Bundle(folder, meta, _read_json(folder, "inputs.json", files, []), mapping,
                    _read_json(folder, "lineage.json", files), _read_json(folder, "diagnosis.json", files),
                    _read_json(folder, "attempt/changes.json", files, []) if meta.get("published") else [],
                    _read_json(folder, "attempt/held.json", files, []),
                    _read_json(folder, "attempt/not-approved.json", files, []))
    if meta.get("published"):
        for rel in files:
            name = rel.removeprefix("result/")
            if rel.startswith("result/") and name.endswith(".csv") and name != "changes.csv":
                header, rows = read_csv((folder / rel).read_bytes())
                bundle.result[name[:-4]] = {"header": header, "rows": rows}
    return bundle


@dataclass
class Evidence:
    """채점에 쓰는 서비스 판단 근거(뷰 좌표: 테이블·뷰 레코드·논리 컬럼)."""
    cell: dict = field(default_factory=lambda: defaultdict(list))  # (t, r, c) → [(category, code, detail)]
    groups: dict = field(default_factory=lambda: defaultdict(list))  # (t, r) → [group]
    held: set = field(default_factory=set)
    not_approved: set = field(default_factory=set)
    changes: dict = field(default_factory=dict)  # (t, r, c) → change

    @classmethod
    def of(cls, bundle: Bundle) -> Evidence:
        ev = cls()
        report = bundle.diagnosis or {"issues": [], "duplicate_groups": []}
        for i in report["issues"]:
            for c in i["columns"]:
                ev.cell[(i["table"], i["record"], c)].append((i["category"], i["code"], i.get("detail")))
        for g in report.get("duplicate_groups", []):
            for r in g["records"]:
                ev.groups[(g["table"], r)].append(g)
        for h in bundle.held:
            for c in h["columns"]:
                ev.held.add((h["table"], h["record"], c))
        ev.not_approved = {(n["table"], n["record"], n["column"]) for n in bundle.not_approved}
        ev.changes = {(c["table"], c["record"], c["column"]): c for c in bundle.changes}
        return ev
