"""M1-1 엔진 확장 ① rule_execution (docs/technical-design.md 1·5절)."""

import json
from pathlib import Path

from diagnostics.engine import column_rule_kinds, diagnose

ROOT = Path(__file__).resolve().parents[1]


def write(folder, name, text):
    (folder / f"{name}.csv").write_text(text, encoding="utf-8")


CONFIG = {
    "tables": {
        "customers": {
            "columns": {
                "id": {"required": True, "type": "integer"},
                "grade": {"allowed": ["a", "b"], "strip": True, "casefold": True, "missing_values": ["없음"]},
            },
            "unique": [["id"]],
        },
        "orders": {
            "columns": {
                "id": {"required": True, "type": "integer", "min": 1},
                "customer_id": {"type": "integer"},
                "status": {"allowed": ["paid", "cancelled"]},
                "memo": {"required_if": {"column": "status", "in": ["cancelled"]}},
            },
            "unique": [["id"]],
            "references": [{"columns": ["customer_id"], "table": "customers", "target_columns": ["id"]}],
        },
    }
}


def by_key(report):
    return {(r["table"], r["kind"], tuple(r["columns"])): r for r in report["rule_execution"]}


def test_profile_mode_records_profile_only(tmp_path):
    write(tmp_path, "a", "x\n1\n")
    report = diagnose(tmp_path)
    assert report["rule_execution"] == [{"table": "a", "kind": "profile", "columns": [], "executed": True, "reason": None}]


def test_all_rules_executed_even_without_issues(tmp_path):
    """실행 증거는 rule_counts가 아니다: 문제 0건인 규칙도 실행 기록이 있어야 한다."""
    write(tmp_path, "customers", "id,grade\n1,a\n2,b\n")
    write(tmp_path, "orders", "id,customer_id,status,memo\n1,1,paid,\n")
    report = diagnose(tmp_path, CONFIG)
    rec = by_key(report)
    assert report["status"] == "completed"
    assert all(r["executed"] and r["reason"] is None for r in report["rule_execution"])
    assert "min" not in report["summary"]["rule_counts"]
    assert rec[("orders", "min", ("id",))]["executed"]
    assert rec[("orders", "reference", ("customer_id",))]["target"] == "customers"
    assert {k for (t, k, _) in rec if t == "customers"} == {
        "profile", "required", "type", "allowed", "comparison_normalization", "missing_values", "unique"}
    assert ("orders", "required_if", ("memo",)) in rec


def test_missing_column_excludes_whole_table(tmp_path):
    write(tmp_path, "customers", "id,grade\n1,a\n")
    write(tmp_path, "orders", "id,customer_id,status\n1,1,paid\n")  # memo 없음
    report = diagnose(tmp_path, CONFIG)
    orders = [r for r in report["rule_execution"] if r["table"] == "orders" and r["kind"] != "profile"]
    assert orders and all(not r["executed"] and r["reason"] == "columns_unavailable" for r in orders)
    assert by_key(report)[("orders", "profile", ())]["executed"]
    assert all(r["executed"] for r in report["rule_execution"] if r["table"] == "customers")


def test_missing_table_and_reference_target(tmp_path):
    write(tmp_path, "orders", "id,customer_id,status,memo\n1,1,paid,\n")
    report = diagnose(tmp_path, CONFIG)
    rec = by_key(report)
    assert rec[("customers", "unique", ("id",))] == {
        "table": "customers", "kind": "unique", "columns": ["id"], "executed": False, "reason": "table_unavailable"}
    ref = rec[("orders", "reference", ("customer_id",))]
    assert not ref["executed"] and ref["reason"] == "reference_target_unavailable"
    assert rec[("orders", "type", ("customer_id",))]["executed"]
    assert report["status"] == "incomplete"


def test_read_error(tmp_path):
    write(tmp_path, "customers", "id,id\n1,2\n")  # 중복 헤더 → 읽기 실패
    write(tmp_path, "orders", "id,customer_id,status,memo\n1,1,paid,\n")
    report = diagnose(tmp_path, CONFIG)
    rec = by_key(report)
    assert rec[("customers", "profile", ())]["reason"] == "read_error"
    assert rec[("customers", "required", ("id",))]["reason"] == "read_error"
    assert rec[("orders", "reference", ("customer_id",))]["reason"] == "reference_target_unavailable"


def test_column_rule_kinds_ignores_inactive_settings():
    assert column_rule_kinds({"required": False, "strip": False, "type": "string"}) == []
    assert column_rule_kinds({"standardization": "kr_mobile", "pattern": "x"}) == ["pattern", "standardization"]


def test_development_set_shop_config_all_executed():
    config = json.loads((ROOT / "config/shop-diagnostics.json").read_text(encoding="utf-8"))
    report = diagnose(ROOT / "data/dirty/development", config)
    records = report["rule_execution"]
    expected = sum(len(column_rule_kinds(r)) for t in config["tables"].values() for r in t["columns"].values())
    expected += sum(len(t.get("unique", [])) + len(t.get("references", [])) for t in config["tables"].values())
    expected += report["summary"]["tables_read"]
    assert len(records) == expected
    assert all(r["executed"] for r in records)
    assert records == diagnose(ROOT / "data/dirty/development", config)["rule_execution"]
