"""M6-8 검토 항목 기록(run_metrics)·재사용 측정 도구 (QE ④ 6-1). 합성 fixture와 개발 입력만 쓴다."""

import csv
import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from prep import metrics
from prep.preview import PROJECT_ROOT, Workspace
from tools import measure_reuse as mr

ROOT = Path(__file__).resolve().parents[1]
CUSTOMERS = (ROOT / "data/dirty/development/customers.csv").read_bytes()
PHONES = ["010-1234-5678", "01012345678", "010 1234 5678", "(010) 1234-5678", "+82 10-1234-5678"]


def table(header, rows) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows([header, *rows])
    return buf.getvalue().encode("utf-8")


def fixture(n=24) -> dict[str, bytes]:
    """합성 입력: 고객·주소·주문·주문상세·결제·환불과 공유 기준표(상품). 고객 하나는 중복 행."""
    cust = [[f"C{i:03d}", f" 가상{i} ", f"c{i}@example.com", PHONES[i % len(PHONES)], "ACTIVE" if i % 2 else "active",
             "TRUE", "2026/06/01 09:00:00+09:00", "2026-10-01T09:00:00+09:00"] for i in range(n)]
    cust.append(list(cust[3]))
    orders = [[f"O{i:03d}", f"C{i % n:03d}", "2026-07-01T09:00:00+09:00", "web", "completed"] for i in range(n * 2)]
    items = [[f"I{i:03d}", f"O{i:03d}", "SKU001", "상품", "기본", "1", "1000", "0", "1000"] for i in range(n * 2)]
    pays = [[f"P{i:03d}", f"O{i:03d}", "card", "SUCCEEDED", "1000", "2026-07-01T09:00:00+09:00",
             "2026/07/01 09:01:00+09:00", ""] for i in range(n * 2)]
    refunds = [[f"R{i:03d}", f"P{i:03d}", "", "order_cancellation", "succeeded", "1000",
                "2026-07-02T09:00:00+09:00", "2026-07-02T09:10:00+09:00", ""] for i in range(0, n * 2, 5)]
    return {
        "customers.csv": table(["customer_id", "customer_name", "email", "phone", "customer_status",
                                "marketing_consent", "joined_at", "updated_at"], cust),
        "orders.csv": table(["order_id", "customer_id", "ordered_at", "order_channel", "order_status"], orders),
        "order_items.csv": table(["order_item_id", "order_id", "sku_id", "product_name_snapshot", "option_name_snapshot",
                                  "quantity", "unit_price", "discount_amount", "line_total"], items),
        "payments.csv": table(["payment_id", "order_id", "payment_method", "payment_status", "amount", "requested_at",
                               "paid_at", "failure_code"], pays),
        "refunds.csv": table(["refund_id", "payment_id", "return_id", "refund_type", "refund_status", "amount",
                              "requested_at", "refunded_at", "failure_code"], refunds),
        "products.csv": table(["product_id", "category_id", "product_name", "brand_name"], [["PRD1", "CAT1", "상품", "B"]]),
    }


def rows(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8"))))[1:]


# ---- 나누기 ----
def test_split_follows_customer_and_shares_reference():
    a, b, summary = mr.split(fixture())
    assert rows(a["products.csv"]) == rows(b["products.csv"]) == rows(fixture()["products.csv"])
    side = {r[0]: "A" for r in rows(a["customers.csv"])} | {r[0]: "B" for r in rows(b["customers.csv"])}
    assert set(side.values()) == {"A", "B"}
    orders = {}
    for s, files in (("A", a), ("B", b)):
        for r in rows(files["orders.csv"]):
            assert side[r[1]] == s
            orders[r[0]] = s
        pays = {}
        for r in rows(files["order_items.csv"]) + rows(files["payments.csv"]):
            assert orders[r[1]] == s
        for r in rows(files["payments.csv"]):
            pays[r[0]] = s
        for r in rows(files["refunds.csv"]):
            assert pays[r[1]] == s
    dup = [r for f in (a, b) for r in rows(f["customers.csv"]) if r[0] == "C003"]
    assert len(dup) == 2 and (dup[0] in rows(a["customers.csv"])) == (dup[1] in rows(a["customers.csv"]))
    total = sum(v.get("A", 0) + v.get("B", 0) for v in summary.values())
    assert total == sum(len(rows(d)) for n, d in fixture().items() if n != "products.csv")


def test_split_is_deterministic():
    assert mr.split(fixture())[:2] == mr.split(fixture())[:2]


def test_split_rejects_unknown_table():
    with pytest.raises(mr.MeasureError):
        mr.split({"memo.csv": b"a,b\n1,2\n"})


def test_inputs_must_be_dev_or_outside_repo(tmp_path):
    assert mr.check_inputs(mr.DEV_INPUTS) == mr.DEV_INPUTS.resolve()
    assert mr.check_inputs(tmp_path) == tmp_path.resolve()
    for rel in ("data/dirty/evaluation", "data/clean", "evaluation_private", "tests"):
        with pytest.raises(mr.MeasureError):
            mr.check_inputs(PROJECT_ROOT / rel)  # 경로만 보고 거부(열지 않음)


# ---- run_metrics ----
def test_review_items_components():
    proposals = {1: {"table": {"auto": False}, "columns": [{"original": "a", "needs_confirmation": True},
                                                          {"original": "b", "needs_confirmation": False}]},
                 2: {"table": {"auto": True}, "columns": [{"original": "c", "needs_confirmation": False}]}}
    groups = [{"id": "t.a.type", "cells": 3, "reuse": 3}, {"id": "t.b.type", "cells": 2, "reuse": 1},
              {"id": "t.c.type", "cells": 4, "reuse": 0}, {"id": "t.d.type", "cells": 0, "held": 1, "reuse": 0}]
    report = {"issues": [
        {"category": "human_review", "table": "t", "record": 1, "columns": ["a", "b"], "code": "x"},
        {"category": "human_review", "table": "t", "record": 1, "columns": ["a"], "code": "y"},
        {"category": "business_rule_violation", "table": "t", "record": 2, "columns": ["a"], "code": "range"},
        {"category": "business_rule_violation", "table": "t", "record": 3, "columns": [], "code": "duplicate_row"}],
        "duplicate_groups": [{"id": "g1"}]}
    integrity = {"raw": {"violations": [{"rule": "r1", "rows": [[1, 2]]}]},
                 "virtual": {"violations": [{"rule": "r1", "rows": [[1, 2]]}, {"rule": "r2", "rows": [[3]]}]}}
    items, inputs = metrics.review_items(proposals, groups, report, 5, integrity)
    assert items == {"mapping": 2, "groups": 2, "human_review": 2, "business_groups": 4, "held": 5, "total": 15}
    assert inputs["groups_not_preselected"] == ["t.b.type", "t.c.type"] and inputs["groups_preselected"] == ["t.a.type"]
    assert inputs["mapping"]["1"] == {"table": True, "columns": ["a"]}


def test_run_metrics_recorded_once_at_final_confirm(tmp_path):
    ws = tmp_path / "workspace"
    client = TestClient(create_app(ws))
    run = client.post("/api/runs", files=[("files", ("customers.csv", CUSTOMERS, "text/csv"))],
                      data={"set": "shop", "source": "거래처A"}).json()
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps})
    client.post(f"/api/runs/{run['id']}/diagnose")
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    w = Workspace(ws)
    db_run = w.load(run["id"])["db_run"]
    assert metrics.get(w.store, db_run) is None  # 미리보기만으로는 기록하지 않음
    client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]})
    m = metrics.get(w.store, db_run)
    assert m["review_items"]["groups"] == len([g for g in p["groups"] if g["cells"]])  # 규칙 없음: 전부 검토
    assert m["review_items"]["total"] == sum(m["review_items"][k] for k in metrics.COMPONENTS)
    c = sqlite3.connect(ws / "app.db")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE run_metrics SET inputs = '{}'")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM run_metrics")


# ---- 측정 ----
def test_measure_on_synthetic_fixture(tmp_path):
    src = tmp_path / "inputs"
    src.mkdir()
    for name, data in fixture().items():
        (src / name).write_bytes(data)
    report = mr.measure(src, tmp_path / "out")
    one, two = report["without_reuse"]["review_items"], report["with_reuse"]["review_items"]
    assert report["same_result"] is True  # 같은 최종 결정 → 같은 결과(안전 비교의 전제)
    assert two["groups"] < one["groups"] and report["reduction"] > 0
    assert {k: one[k] for k in ("human_review", "business_groups", "held")} == {
        k: two[k] for k in ("human_review", "business_groups", "held")}  # 재사용과 무관한 항목은 같다
    assert report["without_reuse"]["inputs"]["reuse"] is False and report["with_reuse"]["inputs"]["reuse"] is True
    assert json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))["reduction"] == report["reduction"]
    for cond in ("b1", "b2"):  # 두 조건은 서로의 상태를 바꾸지 않는다(각자 A 직후 복제 + B 한 번)
        c = sqlite3.connect(tmp_path / "out" / cond / "app.db")
        assert c.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 2
    a = sqlite3.connect(tmp_path / "out" / "a" / "app.db")
    assert a.execute("SELECT COUNT(*) FROM run").fetchone()[0] == 1
    assert "합계" in mr.render(report)


def test_rate():
    assert mr.rate(0, 0) is None and mr.rate(10, 4) == 0.6
