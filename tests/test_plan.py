"""T-03 실행 계획 (docs/technical-design.md 5-2절, M1-6). 개발 입력만 쓴다."""

import csv
import json
from pathlib import Path

import pytest

from diagnostics.engine import diagnose
from prep.plan import build_plan

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"
LOGICAL = json.loads((ROOT / "config/sets/shop/logical-diagnostics.json").read_text(encoding="utf-8"))
HMAP = json.loads((ROOT / "config/sets/shop/legacy-header-map.json").read_text(encoding="utf-8"))["tables"]
ALL = {t: set(spec["columns"]) for t, spec in LOGICAL["tables"].items()}


def view(tmp_path, present):
    """개발 입력을 논리 헤더 뷰로 쓰되 present에 있는 테이블·컬럼만 남긴다."""
    for table, cols in present.items():
        with (DEV / f"{table}.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        logical = [HMAP[table]["columns"][h] for h in rows[0]]
        keep = [i for i, c in enumerate(logical) if c in cols]
        with (tmp_path / f"{table}.csv").open("w", encoding="utf-8", newline="") as f:
            csv.writer(f, lineterminator="\n").writerows([[logical[i] for i in keep]] + [[r[i] for i in keep]
                                                                                          for r in rows[1:]])
    return tmp_path


def run_engine(tmp_path, present):
    plan = build_plan(LOGICAL, present)
    report = diagnose(view(tmp_path, present), plan["config"])
    return plan, report


def executed_set(rules):
    return {(r["table"], r["kind"], tuple(r["columns"]), r.get("target")) for r in rules}


def engine_executed(report):
    return executed_set(r for r in report["rule_execution"] if r["executed"] and r["kind"] != "profile")


def skipped(plan):
    return {(r["table"], r["kind"], tuple(r["columns"]), r["reason"]) for r in plan["skipped"]}


def test_full_development_set(tmp_path):
    plan, report = run_engine(tmp_path, ALL)
    assert plan["completeness"] == "full" and plan["skipped"] == []
    assert all(t["publishable"] for t in plan["tables"].values())
    assert report["status"] == "completed"
    assert engine_executed(report) == executed_set(plan["executed"])


def test_single_table(tmp_path):
    """단일 테이블: 참조는 excluded_target, 참조 오류로 세지 않는다. 발행은 가능."""
    plan, report = run_engine(tmp_path, {"orders": ALL["orders"]})
    assert ("orders", "reference", ("customer_id",), "excluded_target") in skipped(plan)
    assert plan["tables"]["orders"] == {"diagnosable": True, "publishable": True, "missing_required": [],
                                        "reason": None}
    assert plan["completeness"] == "partial"
    assert report["status"] == "completed" and not any(i["code"] == "reference" for i in report["issues"])
    assert engine_executed(report) == executed_set(plan["executed"])


def test_optional_column_missing(tmp_path):
    plan, report = run_engine(tmp_path, {"orders": ALL["orders"] - {"delivery_memo"}, "customers": ALL["customers"]})
    assert ("orders", "missing_values", ("delivery_memo",), "missing_column") in skipped(plan)
    assert plan["tables"]["orders"]["publishable"]
    assert report["status"] == "completed"


def test_required_column_missing(tmp_path):
    """필수 컬럼 없음: 관련 검사를 건너뛴 부분 검사, 진단은 하되 발행 대상 아님(AC-03-4)."""
    plan, report = run_engine(tmp_path, {"orders": ALL["orders"] - {"ordered_at"}})
    assert {("orders", k, ("ordered_at",), "missing_column") for k in ("required", "type", "standardization")} <= \
        skipped(plan)
    assert plan["tables"]["orders"] == {"diagnosable": True, "publishable": False,
                                        "missing_required": ["ordered_at"], "reason": "missing_required"}
    assert report["status"] == "completed"
    assert not any("ordered_at" in i["columns"] for i in report["issues"])


def test_parent_table_excluded(tmp_path):
    """부모 테이블 제외: 자식의 참조는 excluded_target으로 건너뛴다."""
    present = {t: ALL[t] for t in ("order_items", "product_variants")}
    plan, report = run_engine(tmp_path, present)
    assert ("order_items", "reference", ("order_id",), "excluded_target") in skipped(plan)
    assert ("order_items", "reference", ("sku_id",), "product_variants") in executed_set(plan["executed"])
    assert report["status"] == "completed"
    assert engine_executed(report) == executed_set(plan["executed"])


def test_dependency_skipped(tmp_path):
    no_status = ALL["orders"] - {"order_status"}
    plan, report = run_engine(tmp_path, {"orders": no_status, "customers": ALL["customers"] - {"customer_id"}})
    s = skipped(plan)
    assert ("orders", "required_if", ("cancelled_at",), "dependency_skipped") in s
    assert ("orders", "reference", ("customer_id",), "dependency_skipped") in s  # 대상 컬럼 없음
    assert ("customers", "unique", ("customer_id",), "dependency_skipped") in s
    assert ("orders", "type", ("cancelled_at",)) in {(t, k, c) for t, k, c, _ in executed_set(plan["executed"])}
    assert report["status"] == "completed"


def test_no_rules_table():
    plan = build_plan(LOGICAL, {"orders": set()})
    t = plan["tables"]["orders"]
    assert (t["diagnosable"], t["publishable"], t["reason"]) == (False, False, "no_rules")
    assert "order_id" in t["missing_required"]
    assert plan["config"]["tables"]["orders"]["columns"] == {}


def test_hash_stable_and_sensitive():
    a, b = build_plan(LOGICAL, ALL), build_plan(LOGICAL, {t: set(c) for t, c in ALL.items()})
    assert a["hash"] == b["hash"]
    assert build_plan(LOGICAL, {**ALL, "orders": ALL["orders"] - {"delivery_memo"}})["hash"] != a["hash"]


def test_unknown_table_rejected():
    with pytest.raises(ValueError):
        build_plan(LOGICAL, {"nope": set()})


def test_logical_config_not_mutated():
    before = json.dumps(LOGICAL, sort_keys=True)
    build_plan(LOGICAL, {"orders": ALL["orders"] - {"order_status"}})
    assert json.dumps(LOGICAL, sort_keys=True) == before
