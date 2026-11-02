"""논리 설정 변환 (docs/technical-design.md 5-1절, M1-2).

legacy-header-map.json(한글 헤더 → 논리 컬럼, 원 테이블 → 표준 테이블)으로
config/shop-diagnostics.json의 컬럼 키·required_if.column·unique·references를 모두 논리 이름으로 바꿔
config/sets/shop/logical-diagnostics.json을 만든다. 원 설정은 바꾸지 않는다.

    uv run python tools/build_logical_config.py          # 생성
    uv run python tools/build_logical_config.py --check  # 커밋된 파일과 같은지 확인
"""

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/shop-diagnostics.json"
HEADER_MAP = ROOT / "config/sets/shop/legacy-header-map.json"
SCHEMA = ROOT / "schema.json"
OUTPUT = ROOT / "config/sets/shop/logical-diagnostics.json"


class MappingError(ValueError):
    pass


def schema_columns(schema):
    return {t: [f["name"] for f in spec["fields"]] for t, spec in schema["tables"].items()}


def check_map(config, header_map, schema):
    """대응표가 원 설정을 빠짐없이, 겹치지 않게, schema에 있는 이름으로만 옮기는지 확인한다."""
    cols = schema_columns(schema)
    tmap = header_map["tables"]
    if set(tmap) != set(config["tables"]):
        raise MappingError(f"테이블 대응 불일치: 없음 {sorted(set(config['tables']) - set(tmap))}, "
                           f"남음 {sorted(set(tmap) - set(config['tables']))}")
    targets = [m["table"] for m in tmap.values()]
    if len(set(targets)) != len(targets):
        raise MappingError("두 원 테이블이 같은 표준 테이블로 간다")
    for name, spec in config["tables"].items():
        m = tmap[name]
        std = m["table"]
        if std not in cols:
            raise MappingError(f"{name}: schema에 없는 표준 테이블 {std}")
        original, mapped = set(spec["columns"]), m["columns"]
        if set(mapped) != original:
            raise MappingError(f"{name}: 컬럼 대응 불일치: 없음 {sorted(original - set(mapped))}, "
                               f"남음 {sorted(set(mapped) - original)}")
        logical = list(mapped.values())
        if len(set(logical)) != len(logical):
            raise MappingError(f"{name}: 두 원래 컬럼이 같은 논리 컬럼으로 간다")
        unknown = [c for c in logical if c not in cols[std]]
        if unknown:
            raise MappingError(f"{name}: schema {std}에 없는 논리 컬럼 {unknown}")


def convert(config, header_map, schema):
    check_map(config, header_map, schema)
    tmap = header_map["tables"]
    out = {k: copy.deepcopy(v) for k, v in config.items() if k != "tables"}
    out["tables"] = {}
    for name, spec in config["tables"].items():
        cmap = tmap[name]["columns"]
        new = {}
        columns = {}
        for col, rule in spec["columns"].items():
            rule = copy.deepcopy(rule)
            if "required_if" in rule:
                rule["required_if"]["column"] = cmap[rule["required_if"]["column"]]
            columns[cmap[col]] = rule
        new["columns"] = columns
        if "unique" in spec:
            new["unique"] = [[cmap[c] for c in group] for group in spec["unique"]]
        if "references" in spec:
            new["references"] = [
                {"columns": [cmap[c] for c in ref["columns"]],
                 "table": tmap[ref["table"]]["table"],
                 "target_columns": [tmap[ref["table"]]["columns"][c] for c in ref["target_columns"]]}
                for ref in spec["references"]]
        out["tables"][tmap[name]["table"]] = new
    return out


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def render(config):
    return json.dumps(config, ensure_ascii=False, indent=2) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="논리 설정 변환")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    text = render(convert(load(SOURCE), load(HEADER_MAP), load(SCHEMA)))
    if args.check:
        same = OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") == text
        print("일치" if same else "불일치: 다시 생성하세요")
        return 0 if same else 1
    OUTPUT.write_text(text, encoding="utf-8")
    print("생성:", OUTPUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
