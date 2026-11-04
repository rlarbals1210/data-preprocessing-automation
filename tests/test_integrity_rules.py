"""M4-1 정합성 규칙 설정·문법 검증기 (docs/technical-design.md 9-1절·부록 A, T-10 문법)."""

import copy
import json
import re
from pathlib import Path

import pytest

from prep.integrity import validate

ROOT = Path(__file__).resolve().parents[1]
RULES = json.loads((ROOT / "config/sets/shop/integrity.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))
COLUMNS = {t: [f["name"] for f in v["fields"]] for t, v in SCHEMA["tables"].items()}


def test_appendix_a_rules_committed_verbatim():
    """부록 A JSON을 그대로 설정으로 둔다(확정 계약)."""
    text = (ROOT / "docs/technical-design.md").read_text(encoding="utf-8")
    part = text[text.index("## 부록 A"):]
    block = re.search(r"```json\n(.*?)```", part, re.S).group(1)
    assert json.loads(block) == RULES
    assert [r["id"][:3] for r in RULES["rules"]] == [f"R{n:02d}" for n in range(1, 11)]


def test_appendix_a_rules_valid():
    assert validate(RULES, COLUMNS) == []


def mutate(rule_id, fn):
    doc = copy.deepcopy(RULES)
    fn(next(r for r in doc["rules"] if r["id"].startswith(rule_id)), doc)
    return validate(doc, COLUMNS)


@pytest.mark.parametrize("rule_id, fn, message", [
    ("R01", lambda r, d: r.update(type="magic"), "알 수 없는 type"),
    ("R01", lambda r, d: r.pop("right"), "필수 키 없음"),
    ("R01", lambda r, d: r.update(extra=1), "모르는 키"),
    ("R01", lambda r, d: r.update(enabled="yes"), "enabled"),
    ("R01", lambda r, d: r.update(anchor="nope"), "anchor"),
    ("R01", lambda r, d: r["left"].update(col="nope"), "없는 컬럼"),
    ("R01", lambda r, d: r.update(right={"sub": [{"col": "quantity"}]}), "sub는 표현식 2개"),
    ("R01", lambda r, d: r.update(right={"const": "1"}), "const는 정수"),
    ("R01", lambda r, d: r.update(right={"exec": "1+1"}), "값 표현식"),
    ("R01", lambda r, d: r.update(right={"sum": {"path": [{"to": "orders", "on": [["order_id", "order_id"]]}],
                                                 "col": "order_total"}}), "집계를 쓰지 않는다"),
    ("R02", lambda r, d: r.update(empty_child="maybe"), "empty_child"),
    ("R02", lambda r, d: r["right"]["add"][0]["sum"]["path"][0].update(to="nope"), "경로 대상 테이블"),
    ("R02", lambda r, d: r["right"]["add"][0]["sum"]["path"][0].update(on=["order_id"]), "on은"),
    ("R03", lambda r, d: r["when"]["all"][0].update(**{"in": []}), "in/not_in"),
    ("R03", lambda r, d: r.update(when={"col": "shipment_status", "like": "x"}), "모르는 조건"),
    ("R05", lambda r, d: r.update(earlier={"col": "requested_at"}), "each=true면"),
    ("R05", lambda r, d: r.update(missing_earlier="maybe"), "missing_earlier"),
    ("R06", lambda r, d: r["requires"][0].update(op="ne"), "op는"),
    ("R06", lambda r, d: r["requires"][0].update(value=1.5), "value는 정수"),
    ("R07", lambda r, d: r.update(requires=[]), "비어 있지 않은 목록"),
    ("R08", lambda r, d: r["total"]["sum"].update(col="payments.amount"), "경로에 없는 테이블"),
    ("R08", lambda r, d: r.update(total={"col": "quantity"}), "sum 집계"),
    ("R10", lambda r, d: d["rules"].append(copy.deepcopy(r)), "id가 겹친다"),
])
def test_invalid_rules_rejected(rule_id, fn, message):
    errors = mutate(rule_id, fn)
    assert errors and any(message in e for e in errors), errors


def test_top_level_shape():
    assert validate({"rules": []}, COLUMNS)
    assert validate({"version": 2, "rules": []}, COLUMNS)
    assert validate({"version": 1, "rules": [1]}, COLUMNS)
