"""별칭 사전 생성 (QE 3-1, M1-5).

config/sets/shop/aliases.json의 별칭은 근거가 있는 것만 넣는다. 지금 근거는 개발용 한글 헤더
(legacy-header-map.json, 사람이 검토하는 대응표) 하나다. 평가셋 헤더는 근거로 쓰지 않는다.

    uv run python tools/build_aliases.py          # 생성
    uv run python tools/build_aliases.py --check  # 커밋본과 같은지 확인
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER_MAP = ROOT / "config/sets/shop/legacy-header-map.json"
OUTPUT = ROOT / "config/sets/shop/aliases.json"
BASIS = "dev_korean_header"


def build(header_map: dict) -> dict:
    tables = {}
    for spec in header_map["tables"].values():
        cols: dict[str, list[dict]] = {}
        for original, logical in spec["columns"].items():
            cols.setdefault(logical, []).append({"alias": original, "basis": BASIS})
        tables[spec["table"]] = {"aliases": [], "columns": cols}
    return {
        "version": 1,
        "bases": {BASIS: "개발용 오염 입력의 한글 헤더(legacy-header-map.json, 순서·설명 대조로 작성·검토)"},
        "tables": tables,
    }


def render(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = render(build(json.loads(HEADER_MAP.read_text(encoding="utf-8"))))
    if args.check:
        same = OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") == text
        print("일치" if same else "불일치: 다시 생성하세요")
        return 0 if same else 1
    OUTPUT.write_text(text, encoding="utf-8")
    print("생성:", OUTPUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
