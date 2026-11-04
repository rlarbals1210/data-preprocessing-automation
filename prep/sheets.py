"""엑셀 입력 읽기 (FS F02, docs/technical-design.md 7절, M4-5).

- xlsx 시트 하나 = 업로드 항목 하나(시트당 표 하나, 첫 행이 머리글 한 줄).
- 셀은 표시 형식이 아니라 저장값을 문자열로 읽고, 원래 자료형(text·number·datetime·bool·empty)을 함께 둔다.
  숫자는 정수값이면 정수 문자열(`123`), 앞자리 0을 붙이지 않는다. 날짜·시각은 시간대 없는 ISO 문자열이며
  해석하지 않는다(엔진이 사람 검토로 보냄, 결정 Q8).
- 지원 안 하는 구조(병합 셀·수식 셀·빈 머리글 열·중복 머리글·빈 시트)는 시트 이름·셀 좌표·이유와 함께 오류로 두고
  그 시트를 실행 대상에서 뺀다. 추정해서 변환하지 않는다.
- 원본 위치는 시트·엑셀 행 번호·셀 좌표다. CSV 물리 줄 번호(line_start/line_end)는 붙이지 않는다(None).
- read_source(): 업로드 항목의 읽기 조건으로 CSV(엔진 read_table)와 xlsx를 같은 모양의 표로 돌려준다.
"""

from __future__ import annotations

import datetime as dt
import io
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from diagnostics.engine import read_table


class SheetError(ValueError):
    pass


def cell_text(value) -> tuple[str, str]:
    """저장값 → (문자열, 자료형)."""
    if value is None:
        return "", "empty"
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE"), "bool"
    if isinstance(value, int):
        return str(value), "number"
    if isinstance(value, float):
        return (str(int(value)) if value.is_integer() else repr(value)), "number"
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat(), "datetime"
    return str(value), "text"


def read_workbook(data: bytes) -> list[dict]:
    """시트별 결과: {sheet, status(ok|error), errors[{sheet, cell, reason}], headers, rows[...]}"""
    wb = load_workbook(io.BytesIO(data), data_only=False)
    out = []
    for ws in wb.worksheets:
        out.append(_read_sheet(ws))
    return out


def _read_sheet(ws) -> dict:
    name = ws.title
    errors: list[dict] = []

    def fail(cell: str | None, reason: str) -> None:
        errors.append({"sheet": name, "cell": cell, "reason": reason})

    for rng in sorted(ws.merged_cells.ranges, key=str):
        fail(str(rng), "병합 셀은 지원하지 않는다")
    grid = [list(r) for r in ws.iter_rows()]
    formulas = [c.coordinate for row in grid for c in row if c.data_type == "f"]
    for coord in formulas[:20]:
        fail(coord, "수식 셀은 지원하지 않는다(저장값을 추정하지 않음)")
    # 실제로 값이 있는 범위(뒤쪽 빈 행·열 제외)
    last_row = max((i for i, row in enumerate(grid, start=1) if any(c.value is not None for c in row)), default=0)
    last_col = max((j for row in grid[:last_row] for j, c in enumerate(row, start=1) if c.value is not None),
                   default=0)
    if last_row == 0:
        fail(None, "빈 시트")
        return {"sheet": name, "status": "error", "errors": errors, "headers": [], "rows": []}
    headers = []
    for j in range(1, last_col + 1):
        text, _ = cell_text(grid[0][j - 1].value if j <= len(grid[0]) else None)
        coord = f"{get_column_letter(j)}1"
        if text.strip() == "":
            fail(coord, "빈 머리글 열")
        elif text in headers:
            fail(coord, f"중복 머리글: {text}")
        headers.append(text)
    rows = []
    for i in range(2, last_row + 1):
        values, types = {}, {}
        line = grid[i - 1]
        for j, h in enumerate(headers, start=1):
            text, kind = cell_text(line[j - 1].value if j <= len(line) else None)
            values[h] = text
            if kind not in ("text", "empty"):
                types[h] = kind
        rows.append({"record": i - 1, "line_start": None, "line_end": None, "sheet": name, "sheet_row": i,
                     "values": values, "types": types})
    status = "error" if errors else "ok"
    return {"sheet": name, "status": status, "errors": errors, "headers": headers if not errors else [],
            "rows": rows if not errors else []}


def read_source(path: Path, options: dict) -> dict:
    """업로드 항목 하나를 표로 읽는다. xlsx는 options = {format: xlsx, sheet}."""
    if options.get("format") == "xlsx":
        sheets = {s["sheet"]: s for s in read_workbook(path.read_bytes())}
        s = sheets.get(options.get("sheet"))
        if s is None:
            raise SheetError(f"시트가 없다: {options.get('sheet')!r}")
        if s["status"] != "ok":
            raise SheetError(s["errors"][0]["reason"])
        return {"headers": s["headers"], "rows": s["rows"], "sheet": s["sheet"]}
    return read_table(path, options.get("encoding", "utf-8-sig"), options.get("delimiter", ","))


def coordinate(headers: list[str], original: str | None, sheet_row: int | None) -> str | None:
    """엑셀 셀 좌표(예: B12). 원래 컬럼이 없거나 CSV면 None."""
    if original is None or sheet_row is None or original not in headers:
        return None
    return f"{get_column_letter(headers.index(original) + 1)}{sheet_row}"
