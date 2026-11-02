"""표준화 뷰·역추적 (docs/technical-design.md 5-3절, M1-7).

- 행 순서: 실행 대상 순서(order_no) → 원래 레코드 번호.
- 컬럼: 매핑된 논리 컬럼(+ 추가 컬럼이 있으면 보조 컬럼 __extras_digest__). 매핑하지 않은 추가 컬럼은 뷰에 넣지 않고
  원문을 역추적 표에 둔다. 보조 컬럼은 (양식, 추가 컬럼 이름·값 — 없는 컬럼은 '없음')의 해시라서 엔진의 동일 행 중복 판정에
  들어가고, 추가 컬럼만 다른 두 행이 중복으로 묶이지 않는다. 화면·출력에는 나오지 않는다.
- 같은 표준 테이블로 여러 파일을 합칠 때 필수 논리 컬럼이 없는 파일은 합치지 않는다. 선택 컬럼이 일부 파일에만 있으면
  없는 파일의 셀은 빈 문자열로 쓰고 absent로 표시한다.
- 역추적 표: 뷰 (테이블, 레코드) → (업로드 항목, 원래 레코드, 물리 줄 범위), 뷰 컬럼 → 원래 컬럼 이름, absent 셀.
  엔진 결과의 모든 위치(진단 항목·중복 그룹·변경 내역·보류)를 이 표로 바꾼다.
- 뷰는 다시 쓴 파일이므로 셀 문자열은 같지만 파일 바이트는 같지 않다. 원본 해시는 업로드 원본으로 관리한다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from diagnostics.engine import read_table
from prep.guard import Guard

EXTRAS = "__extras_digest__"
ABSENT = "\0absent"
# 시간대 없는 날짜·시각(결정 Q8): 엔진은 형식 해석 실패 → 사람 검토로 둔다. 화면 이유만 보강한다(이중 집계 없음).
_NAIVE_DATETIME = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?")


class ViewError(ValueError):
    pass


@dataclass
class ViewInput:
    item_id: int
    table: str
    path: Path  # 보관 원본(blob) 경로
    columns: dict[str, str | None]  # 원래 컬럼 → 논리 컬럼(None이면 추가 컬럼)
    order_no: int
    encoding: str = "utf-8-sig"
    delimiter: str = ","
    name: str = ""


@dataclass
class View:
    folder: Path
    lineage: dict
    hash: str
    tables: dict[str, dict] = field(default_factory=dict)


def _template_key(headers: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(headers), ensure_ascii=False).encode()).hexdigest()[:16]


def build_view(inputs: list[ViewInput], folder: Path, guard: Guard, required: dict[str, list[str]] | None = None,
               column_order: dict[str, list[str]] | None = None) -> View:
    required = required or {}
    folder = guard.check(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if any(folder.iterdir()):
        raise ViewError("뷰 폴더가 비어 있지 않다")
    by_table: dict[str, list[ViewInput]] = {}
    for inp in sorted(inputs, key=lambda i: i.order_no):
        if len(set(v for v in inp.columns.values() if v)) != len([v for v in inp.columns.values() if v]):
            raise ViewError(f"{inp.name or inp.item_id}: 두 컬럼이 같은 논리 컬럼으로 간다")
        by_table.setdefault(inp.table, []).append(inp)
    lineage: dict = {"version": 1, "tables": {}}
    for table, group in by_table.items():
        loaded = []
        for inp in group:
            data = read_table(guard.check(inp.path), inp.encoding, inp.delimiter)
            if set(inp.columns) != set(data["headers"]):
                raise ViewError(f"{inp.name or inp.item_id}: 매핑의 원래 컬럼이 파일 헤더와 다르다")
            loaded.append((inp, data))
        logical_sets = [{v for v in inp.columns.values() if v} for inp, _ in loaded]
        if len(group) > 1:
            for (inp, _), have in zip(loaded, logical_sets, strict=True):
                lacking = [c for c in required.get(table, []) if c not in have]
                if lacking:
                    raise ViewError(f"{inp.name or inp.item_id}: 필수 컬럼 {lacking}이 없어 {table}에 합치지 않는다. "
                                    "실행 대상에서 빼거나 테이블을 발행 제외로 두세요")
        union = set().union(*logical_sets)
        order = [c for c in (column_order or {}).get(table, []) if c in union]
        for inp, _ in loaded:
            order += [v for v in (inp.columns[h] for h in inp.columns) if v and v not in order]
        extra_names = sorted({h for inp, _ in loaded for h, v in inp.columns.items() if v is None})
        header = order + ([EXTRAS] if extra_names else [])
        rows_out, lineage_rows, absent = [header], [], []
        view_record = 0
        for inp, data in loaded:
            back = {v: h for h, v in inp.columns.items() if v}
            extras_here = [h for h, v in inp.columns.items() if v is None]
            tkey = _template_key(data["headers"])
            for row in data["rows"]:
                view_record += 1
                values = []
                for c in order:
                    if c in back:
                        values.append(row["values"][back[c]])
                    else:
                        values.append("")
                        absent.append([view_record, c])
                extras = {h: row["values"][h] for h in extras_here}
                if extra_names:
                    payload = [tkey] + [[n, extras.get(n, ABSENT)] for n in extra_names]
                    values.append(hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:24])
                rows_out.append(values)
                lineage_rows.append({"record": view_record, "item_id": inp.item_id, "source_record": row["record"],
                                     "line_start": row["line_start"], "line_end": row["line_end"], "extras": extras})
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows_out)
        guard.safe_join(folder, f"{table}.csv").write_text(buf.getvalue(), encoding="utf-8")
        lineage["tables"][table] = {
            "columns": {c: {str(inp.item_id): next((h for h, v in inp.columns.items() if v == c), None)
                            for inp, _ in loaded} for c in order},
            "items": [{"item_id": inp.item_id, "name": inp.name, "order_no": inp.order_no,
                       "extra_columns": [h for h, v in inp.columns.items() if v is None]} for inp, _ in loaded],
            "extra_columns": extra_names,
            "rows": lineage_rows,
            "absent": absent,
        }
    text = json.dumps(lineage, ensure_ascii=False, sort_keys=True)
    guard.safe_join(folder, "lineage.json").write_text(text, encoding="utf-8")
    return View(folder=folder, lineage=lineage, hash=view_hash(folder, guard), tables=lineage["tables"])


def view_hash(folder: Path, guard: Guard) -> str:
    """뷰 해시(TD 4-2): 뷰 CSV들 + 역추적 표(행 순서 포함)."""
    h = hashlib.sha256()
    for p in sorted(guard.check(folder).iterdir()):
        if p.is_file():
            h.update(p.name.encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


class Locator:
    """엔진 위치(테이블, 뷰 레코드, 논리 컬럼)를 원본 위치로 바꾼다."""

    def __init__(self, lineage: dict):
        self.tables = lineage["tables"]
        self._rows = {t: {r["record"]: r for r in spec["rows"]} for t, spec in self.tables.items()}
        self._absent = {t: {(r, c) for r, c in spec["absent"]} for t, spec in self.tables.items()}

    def source(self, table: str, record: int, columns: list[str] | None = None) -> dict:
        row = self._rows[table][record]
        cols = [c for c in (columns or []) if c != EXTRAS]
        mapping = self.tables[table]["columns"]
        return {
            "item_id": row["item_id"], "record": row["source_record"],
            "line_start": row["line_start"], "line_end": row["line_end"],
            "columns": {c: mapping.get(c, {}).get(str(row["item_id"])) for c in cols},
            "absent": [c for c in cols if (record, c) in self._absent[table]],
        }

    def translate_report(self, report: dict) -> dict:
        """진단 결과의 모든 위치에 원본 위치를 붙이고 보조 컬럼을 지운다(사본을 돌려준다)."""
        out = json.loads(json.dumps(report))
        for issue in out["issues"]:
            issue["columns"] = [c for c in issue["columns"] if c != EXTRAS]
            issue["values"] = {k: v for k, v in issue["values"].items() if k != EXTRAS}
            if issue["table"] in self.tables:
                issue["source"] = self.source(issue["table"], issue["record"], issue["columns"])
            if issue["code"] == "type" and len(issue["columns"]) == 1 and _NAIVE_DATETIME.fullmatch(
                    issue["values"][issue["columns"][0]].strip()):
                issue["detail"] = "timezone_missing"
        for g in out["duplicate_groups"]:
            g["columns"] = [c for c in g["columns"] if c != EXTRAS]
            g["differing_columns"] = [c for c in g["differing_columns"] if c != EXTRAS]
            if g["table"] in self.tables:
                g["sources"] = [self.source(g["table"], r) for r in g["records"]]
        for spec in out["tables"].values():
            spec["profiles"].pop(EXTRAS, None)
            if EXTRAS in spec.get("configured_columns", []):
                spec["configured_columns"].remove(EXTRAS)
        return out

    def translate_changes(self, changes: list[dict]) -> list[dict]:
        out = []
        for c in changes:
            if c.get("column") == EXTRAS:
                raise ViewError("보조 컬럼이 바뀌었다")
            out.append(c | {"source": self.source(c["table"], c["record"], [c["column"]])})
        return out


def strip_extras(text: str) -> str:
    """결과 CSV에서 보조 컬럼을 뺀다(출력·다운로드용)."""
    rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    if not rows or EXTRAS not in rows[0]:
        return text
    i = rows[0].index(EXTRAS)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([r[:i] + r[i + 1:] for r in rows])
    return buf.getvalue()
