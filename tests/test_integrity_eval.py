"""M4-2 정합성 평가기 (docs/technical-design.md 9-2절·부록 A, FS F05 AC-05-1~6, T-10).

규칙마다 고의 오류·정상 예외·참조 부족·부모 키 중복·파싱 불가 fixture. 정상 기준 데이터(data/clean)는 쓰지 않는다.
"""

import copy
import json
from pathlib import Path

from prep.integrity import Data, evaluate, provider_overrides

ROOT = Path(__file__).resolve().parents[1]
RULES = json.loads((ROOT / "config/sets/shop/integrity.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))
TYPES = {t: {f["name"]: f["type"] for f in v["fields"]} for t, v in SCHEMA["tables"].items()}
KEYS = {t: [f["name"] for f in v["fields"] if f.get("primary_key")] for t, v in SCHEMA["tables"].items()}
T0, T1, T2, T3 = ("2026-07-01T09:00:00+09:00", "2026-07-02T09:00:00+09:00", "2026-07-03T09:00:00+09:00",
                  "2026-07-04T09:00:00+09:00")


def only(rule_id):
    doc = copy.deepcopy(RULES)
    doc["rules"] = [r for r in doc["rules"] if r["id"].startswith(rule_id)]
    return doc


def rows(cols, *values):
    return [dict(zip(cols.split(), v, strict=True)) for v in values]


def run(rule_id, tables, overrides=None):
    data = Data(tables, TYPES, KEYS, overrides or {})
    return evaluate(only(rule_id), data)


def statuses(result):
    rule = result["rules"][0]
    return rule["status"], rule["reason"], rule["counts"]


ITEMS = "order_item_id order_id quantity unit_price discount_amount line_total"


# ---- R01 행 내 계산 ----

def test_r01_line_total():
    t = {"order_items": rows(ITEMS, ("I1", "O1", "2", "600", "0", "1200"), ("I2", "O1", "2", "600", "100", "1200"),
                             ("I3", "O1", "2", "600", "0", "1,200"), ("I4", "O1", "", "600", "0", "0"))}
    r = run("R01", t)
    _, _, counts = statuses(r)
    assert counts["ok"] == 1 and counts["violation"] == 1
    assert counts["not_computable"] == {"parse_error": 1, "missing_value": 1}
    v = r["violations"][0]
    assert (v["table"], v["record"], v["left"], v["right"]) == ("order_items", 2, 1200, 1100)
    assert ["order_items", 2, "line_total"] in v["cells"] and ["order_items", 2, "discount_amount"] in v["cells"]
    pe = next(x for x in r["not_computable"] if x["reason"] == "parse_error")
    assert pe["cells"] == [("order_items", 3, "line_total")]
    # 가상 기준: 후보 '1,200' → '1200' 이면 계산된다(AC-05-5의 기준 전환)
    r2 = run("R01", t, {("order_items", 3, "line_total"): "1200"})
    assert statuses(r2)[2]["ok"] == 2
    assert t["order_items"][2]["line_total"] == "1,200"  # 값은 바꾸지 않는다(AC-05-4)


# ---- R02 테이블 간 합계 ----

def orders(*values):
    return rows("order_id order_status shipping_fee order_total", *values)


def test_r02_order_total():
    t = {"orders": orders(("O1", "paid", "3000", "5400"), ("O2", "paid", "0", "9999"), ("O3", "paid", "0", "0")),
         "order_items": rows(ITEMS, ("I1", "O1", "2", "600", "0", "1200"), ("I2", "O1", "1", "1200", "0", "1200"),
                             ("I3", "O2", "1", "500", "0", "500"))}
    r = run("R02", t)
    _, _, counts = statuses(r)
    assert counts == {"ok": 1, "violation": 2, "not_applicable": 0, "not_computable": {}}
    v = {x["record"]: x for x in r["violations"]}
    assert (v[2]["left"], v[2]["right"]) == (9999, 500)
    assert v[3]["detail"] == "empty_child"  # 상세 없는 주문은 위반(부록 A)
    assert sorted(map(tuple, v[2]["rows"])) == [("order_items", 3), ("orders", 2)]


def test_r02_duplicate_parent_and_missing_reference():
    t = {"orders": orders(("O1", "paid", "0", "1200"), ("O1", "paid", "0", "1200")),
         "order_items": rows(ITEMS, ("I1", "O1", "2", "600", "0", "1200"))}
    r = run("R02", t)
    assert statuses(r)[2]["not_computable"] == {"duplicate_parent": 2} and not r["violations"]
    r = run("R02", {"orders": orders(("O1", "paid", "0", "1200"))})
    assert statuses(r)[:2] == ("not_run", "missing_reference")  # 합계 0 = 정상으로 통과시키지 않음(AC-05-3)


def test_r02_parse_error_child_not_violation():
    t = {"orders": orders(("O1", "paid", "0", "1200")),
         "order_items": rows(ITEMS, ("I1", "O1", "2", "600", "0", "1,200"))}
    r = run("R02", t)
    assert statuses(r)[2]["not_computable"] == {"parse_error": 1} and not r["violations"]


# ---- R03·R04 시간 순서 ----

SHIP = "shipment_id order_id shipment_status shipped_at delivered_at"
PAY = "payment_id order_id payment_status amount paid_at"


def test_r03_paid_before_shipped():
    t = {"shipments": rows(SHIP, ("S1", "O1", "shipped", T2, ""), ("S2", "O2", "shipped", T0, ""),
                           ("S3", "O3", "shipped", T2, ""), ("S4", "O4", "preparing", "", "")),
         "payments": rows(PAY, ("P1", "O1", "succeeded", "1000", T1), ("P2", "O2", "succeeded", "1000", T1),
                          ("P3", "O2", "failed", "1000", ""), ("P4", "O3", "failed", "1000", ""))}
    r = run("R03", t)
    _, _, c = statuses(r)
    assert (c["ok"], c["violation"], c["not_applicable"]) == (1, 1, 1)  # 준비 상태의 빈 출고 시각은 미적용
    assert c["not_computable"] == {"missing_reference": 1}  # 성공 결제가 없으면 R06 몫
    assert r["violations"][0]["record"] == 2


def test_r04_timezone_required():
    t = {"shipments": rows(SHIP, ("S1", "O1", "delivered", T2, T1), ("S2", "O1", "delivered", T1, T2),
                           ("S3", "O1", "delivered", "2026-07-01 09:00", T2), ("S4", "O1", "shipped", T1, ""))}
    r = run("R04", t)
    _, _, c = statuses(r)
    assert (c["ok"], c["violation"], c["not_applicable"]) == (1, 1, 1)
    assert c["not_computable"] == {"parse_error": 1}  # 시간대 없는 시각은 비교하지 않는다


# ---- R05 다단계·여러 출고 ----

def test_r05_each_linked_delivery():
    t = {"returns": rows("return_id order_id return_status requested_at", ("R1", "O1", "requested", T3),
                         ("R2", "O1", "requested", T0), ("R3", "O1", "requested", T3), ("R4", "O1", "requested", T3)),
         "return_items": rows("return_item_id return_id shipment_item_id quantity",
                              ("RI1", "R1", "SI1", "1"), ("RI2", "R1", "SI2", "1"), ("RI3", "R2", "SI1", "1"),
                              ("RI4", "R3", "SI3", "1")),
         "shipment_items": rows("shipment_item_id shipment_id order_item_id quantity",
                                ("SI1", "S1", "I1", "1"), ("SI2", "S2", "I1", "1"), ("SI3", "S3", "I1", "1")),
         "shipments": rows(SHIP, ("S1", "O1", "delivered", T0, T1), ("S2", "O1", "delivered", T0, T2),
                           ("S3", "O1", "shipped", T0, ""))}
    r = run("R05", t)
    _, _, c = statuses(r)
    assert (c["ok"], c["violation"]) == (1, 2)
    assert {v["record"] for v in r["violations"]} == {2, 3}  # R2: 접수 < 배송 완료, R3: 배송 완료 시각 없음
    assert c["not_computable"] == {"missing_reference": 1}  # 상세 없는 반품


# ---- R06·R07 상태 조건 ----

def test_r06_one_success_payment():
    t = {"orders": orders(("O1", "paid", "0", "0"), ("O2", "completed", "0", "0"), ("O3", "processing", "0", "0"),
                          ("O4", "pending_payment", "0", "0")),
         "payments": rows(PAY, ("P1", "O1", "failed", "1", ""), ("P2", "O1", "succeeded", "1", T1),
                          ("P3", "O2", "succeeded", "1", T1), ("P4", "O2", "succeeded", "1", T2))}
    r = run("R06", t)
    _, _, c = statuses(r)
    assert (c["ok"], c["violation"], c["not_applicable"]) == (1, 2, 1)  # 시도 여러 번은 정상, 성공 0·2건은 위반
    v = {x["record"]: x for x in r["violations"]}
    assert (v[2]["left"], v[2]["right"]) == (2, 1) and (v[3]["left"], v[3]["right"]) == (0, 1)


def test_r07_pending_has_no_success_or_shipment():
    t = {"orders": orders(("O1", "pending_payment", "0", "0"), ("O2", "pending_payment", "0", "0")),
         "payments": rows(PAY, ("P1", "O1", "failed", "1", "")),
         "shipments": rows(SHIP, ("S1", "O2", "preparing", "", ""))}
    r = run("R07", t)
    assert statuses(r)[2]["ok"] == 1 and [v["record"] for v in r["violations"]] == [2]
    assert statuses(run("R07", {"orders": t["orders"], "payments": t["payments"]}))[:2] == \
        ("not_run", "missing_reference")


# ---- R08·R09·R10 누적 상한 ----

SI = "shipment_item_id shipment_id order_item_id quantity"


def test_r08_shipped_le_ordered_excludes_cancelled():
    t = {"order_items": rows(ITEMS, ("I1", "O1", "2", "1", "0", "2"), ("I2", "O1", "2", "1", "0", "2"),
                             ("I3", "O1", "2", "1", "0", "2")),
         "shipment_items": rows(SI, ("SI1", "S1", "I1", "2"), ("SI2", "S2", "I1", "2"), ("SI3", "S1", "I2", "3")),
         "shipments": rows(SHIP, ("S1", "O1", "shipped", T1, ""), ("S2", "O1", "cancelled", "", ""))}
    r = run("R08", t)
    _, _, c = statuses(r)
    assert (c["ok"], c["violation"]) == (2, 1)  # 취소 출고 제외, 출고 없음은 정상
    v = r["violations"][0]
    assert v["record"] == 2 and (v["left"], v["right"]) == (3, 2)
    assert ["shipment_items", 3, "quantity"] in v["cells"]


def test_r08_ambiguous_join():
    t = {"order_items": rows(ITEMS, ("I1", "O1", "2", "1", "0", "2")),
         "shipment_items": rows(SI, ("SI1", "S1", "I1", "1")),
         "shipments": rows(SHIP, ("S1", "O1", "shipped", T1, ""), ("S1", "O1", "shipped", T1, ""))}
    assert statuses(run("R08", t))[2]["not_computable"] == {"ambiguous_join": 1}


def test_r09_returned_le_shipped_excludes_rejected():
    t = {"shipment_items": rows(SI, ("SI1", "S1", "I1", "1"), ("SI2", "S1", "I2", "1")),
         "return_items": rows("return_item_id return_id shipment_item_id quantity",
                              ("RI1", "R1", "SI1", "1"), ("RI2", "R2", "SI1", "1"), ("RI3", "R3", "SI2", "1"),
                              ("RI4", "R4", "SI2", "1")),
         "returns": rows("return_id return_status", ("R1", "received"), ("R2", "rejected"), ("R3", "approved"),
                         ("R4", "requested"))}
    r = run("R09", t)
    assert [v["record"] for v in r["violations"]] == [2] and statuses(r)[2]["ok"] == 1


def test_r10_refund_le_payment():
    t = {"payments": rows(PAY, ("P1", "O1", "succeeded", "1000", T1), ("P2", "O2", "succeeded", "1000", T1),
                          ("P3", "O3", "failed", "1000", "")),
         "refunds": rows("refund_id payment_id refund_status amount", ("F1", "P1", "succeeded", "1000"),
                         ("F2", "P1", "failed", "1000"), ("F3", "P2", "succeeded", "600"),
                         ("F4", "P2", "succeeded", "600"))}
    r = run("R10", t)
    _, _, c = statuses(r)
    assert (c["ok"], c["violation"], c["not_applicable"]) == (1, 1, 1)  # 실패 환불 제외
    assert r["violations"][0]["record"] == 2


# ---- 규칙 단위 상태 ----

def test_disabled_and_not_run():
    doc = only("R01")
    doc["rules"][0]["enabled"] = False
    r = evaluate(doc, Data({"order_items": rows(ITEMS, ("I1", "O1", "1", "1", "0", "9"))}, TYPES, KEYS))
    assert r["rules"][0]["status"] == "disabled" and not r["violations"]  # 꺼진 규칙은 정상 처리 안 함(AC-05-6)
    assert statuses(run("R01", {}))[:2] == ("not_run", "excluded_target")
    t = {"order_items": rows("order_item_id order_id quantity unit_price line_total", ("I1", "O1", "1", "1", "1"))}
    assert statuses(run("R01", t))[:2] == ("not_run", "missing_column")


def test_providers():
    cells = {("order_items", 1, "line_total"): {"after": "1200"}, ("order_items", 1, "quantity"): {"after": "2"}}
    assert provider_overrides(cells) == {("order_items", 1, "line_total"): "1200", ("order_items", 1, "quantity"): "2"}
    assert provider_overrides(cells, {("order_items", 1, "quantity")}) == {("order_items", 1, "quantity"): "2"}


def test_category_conditions_use_comparison_normalization():
    """범주 조건은 설정의 비교 정규화(casefold)로 맞춘다 — 원문 기준에서 'SUCCEEDED' 표기 때문에 위반을 만들지 않는다."""
    t = {"orders": orders(("O1", "PAID", "0", "0")), "payments": rows(PAY, ("P1", "O1", "SUCCEEDED", "1", T1))}
    compare = {"orders": {"order_status": {"casefold": True}}, "payments": {"payment_status": {"casefold": True}}}
    r = evaluate(only("R06"), Data(t, TYPES, KEYS, {}, compare=compare))
    assert statuses(r)[2]["ok"] == 1 and not r["violations"]
    r = evaluate(only("R06"), Data(t, TYPES, KEYS, {}))  # 비교 정규화가 없으면 원문 그대로라 적용 대상이 아님
    assert statuses(r)[2]["not_applicable"] == 1
