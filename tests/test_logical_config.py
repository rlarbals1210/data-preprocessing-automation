"""T-02 논리 설정 변환 (docs/technical-design.md 5-1절, M1-2)."""

import copy
import csv
import json
from pathlib import Path

import pytest

from diagnostics.engine import column_rule_kinds, diagnose, validate_config
from tools import build_logical_config as blc

ROOT = Path(__file__).resolve().parents[1]
SOURCE = blc.load(blc.SOURCE)
HEADER_MAP = blc.load(blc.HEADER_MAP)
SCHEMA = blc.load(blc.SCHEMA)
LOGICAL = blc.load(blc.OUTPUT)
COLS = blc.schema_columns(SCHEMA)


def test_committed_file_is_current():
    assert blc.main(["--check"]) == 0


def test_source_config_untouched_and_logical_valid():
    assert LOGICAL == blc.convert(SOURCE, HEADER_MAP, SCHEMA)
    validate_config(copy.deepcopy(LOGICAL))
    assert any("주문번호" in t["columns"] for t in SOURCE["tables"].values())  # 원 설정은 한글 그대로


def all_column_refs(config):
    """설정 안의 모든 컬럼 참조: (테이블, 컬럼)."""
    refs = []
    for name, spec in config["tables"].items():
        for col, rule in spec["columns"].items():
            refs.append((name, col))
            if "required_if" in rule:
                refs.append((name, rule["required_if"]["column"]))
        refs += [(name, c) for g in spec.get("unique", []) for c in g]
        for ref in spec.get("references", []):
            refs += [(name, c) for c in ref["columns"]]
            refs += [(ref["table"], c) for c in ref["target_columns"]]
    return refs


def test_every_reference_converted_and_in_schema():
    assert len(all_column_refs(LOGICAL)) == len(all_column_refs(SOURCE))
    for table, col in all_column_refs(LOGICAL):
        assert col in COLS[table], (table, col)


def test_table_and_rule_counts_equal():
    def counts(config):
        return {t: (len(s["columns"]), sum(len(column_rule_kinds(r)) for r in s["columns"].values()),
                    len(s.get("unique", [])), len(s.get("references", [])))
                for t, s in config["tables"].items()}
    assert counts(LOGICAL) == {HEADER_MAP["tables"][t]["table"]: v for t, v in counts(SOURCE).items()}


def test_rules_identical_except_names():
    for name, spec in SOURCE["tables"].items():
        cmap = HEADER_MAP["tables"][name]["columns"]
        for col, rule in spec["columns"].items():
            new = LOGICAL["tables"][name]["columns"][cmap[col]]
            stripped = {k: v for k, v in rule.items() if k not in ("required_if", "standardization")}
            assert {k: v for k, v in new.items() if k not in ("required_if", "standardization")} == stripped
            if "standardization" in rule:
                assert new["standardization"] == blc.POLICY_UPGRADE.get(rule["standardization"],
                                                                       rule["standardization"])
            if "required_if" in rule:
                assert new["required_if"] == {"column": cmap[rule["required_if"]["column"]],
                                              "in": rule["required_if"]["in"]}


def test_references_match_schema_foreign_keys():
    for name, spec in LOGICAL["tables"].items():
        fields = {f["name"]: f for f in SCHEMA["tables"][name]["fields"]}
        for ref in spec.get("references", []):
            for col, target_col in zip(ref["columns"], ref["target_columns"], strict=True):
                fk = fields[col].get("references")
                assert fk == {"table": ref["table"], "column": target_col}, (name, col)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda m: m["tables"]["orders"]["columns"].pop("주문번호"), "컬럼 대응 불일치"),
        (lambda m: m["tables"]["orders"]["columns"].__setitem__("주문번호", "order_no"), "schema orders에 없는"),
        (lambda m: m["tables"]["orders"]["columns"].__setitem__("주문총액", "order_id"), "같은 논리 컬럼"),
        (lambda m: m["tables"].pop("refunds"), "테이블 대응 불일치"),
        (lambda m: m["tables"]["refunds"].__setitem__("table", "payments"), "같은 표준 테이블"),
    ],
)
def test_bad_maps_rejected(mutate, message):
    """음성 사례: 빠짐·schema 밖 이름·중복 대응은 변환 전에 거부한다."""
    bad = copy.deepcopy(HEADER_MAP)
    mutate(bad)
    with pytest.raises(blc.MappingError, match=message):
        blc.convert(SOURCE, bad, SCHEMA)


def test_same_diagnosis_with_logical_headers(tmp_path):
    """개발 입력의 헤더만 논리 이름으로 바꾸면 논리 설정 진단이 원 설정 진단과 같은 결과를 낸다."""
    dev = ROOT / "data/dirty/development"
    for path in sorted(dev.glob("*.csv")):
        cmap = HEADER_MAP["tables"][path.stem]["columns"]
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        rows[0] = [cmap[h] for h in rows[0]]
        with (tmp_path / path.name).open("w", encoding="utf-8", newline="") as f:
            csv.writer(f, lineterminator="\n").writerows(rows)
    # 개발 입력에는 새 정책 ID로만 바뀌는 값(₩·+82·점 구분 날짜)이 없어 결과가 같아야 한다.
    before, after = diagnose(dev, SOURCE), diagnose(tmp_path, LOGICAL)

    def key(report, rename):
        return sorted(json.dumps([i["table"], i["record"], [rename(i["table"], c) for c in i["columns"]],
                                  i["code"], i["category"], sorted(i["values"].values())], ensure_ascii=False)
                      for i in report["issues"])

    assert key(before, lambda t, c: HEADER_MAP["tables"][t]["columns"][c]) == key(after, lambda t, c: c)
    assert before["summary"]["category_counts"] == after["summary"]["category_counts"]
    assert [(r["kind"], r["executed"]) for r in before["rule_execution"]] == [
        (r["kind"], r["executed"]) for r in after["rule_execution"]]
