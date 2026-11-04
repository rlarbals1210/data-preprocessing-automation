"""S3 E2E fixture 만들기 (M4-7). 개발 입력 customers.csv를 CSV 1개 + xlsx 시트 2개로 나누고, 수식이 든 메모 시트를 더한다.

    uv run python -m tools.build_e2e_fixtures

결과: web/e2e/fixtures/고객-앞.csv, web/e2e/fixtures/고객-뒤.xlsx(시트 1월·2월·메모). 개발 입력만 쓴다.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/dirty/development/customers.csv"
OUT = ROOT / "web/e2e/fixtures"


def main() -> None:
    rows = list(csv.reader(io.StringIO(SRC.read_text(encoding="utf-8-sig"), newline="")))
    head, body = rows[0], rows[1:]
    OUT.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([head] + body[:60])
    (OUT / "고객-앞.csv").write_text(buf.getvalue(), encoding="utf-8")
    wb = Workbook()
    wb.remove(wb.active)
    for name, part in (("1월", body[60:130]), ("2월", body[130:])):
        ws = wb.create_sheet(name)
        ws.append(head)
        for r in part:
            ws.append(r)  # 모두 텍스트 셀(앞자리 0 보존)
    memo = wb.create_sheet("메모")
    memo.append(["항목", "값"])
    memo.append(["합계", "=1+1"])  # 수식 셀 → 지원 안 하는 구조
    fixed = dt.datetime(2026, 11, 4, 0, 0, 0)
    wb.properties.created = wb.properties.modified = fixed
    wb.save(OUT / "고객-뒤.xlsx")
    print(OUT)


if __name__ == "__main__":
    main()
