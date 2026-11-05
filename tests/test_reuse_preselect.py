"""M6-6b 표준화 재사용 미리 선택 (결정 M6-D4, FS F09 AC-09-1~3, TD 10-2). 개발 입력과 그 변형만 쓴다."""

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from prep import reuse, templates
from prep.preview import Workspace
from prep.profile import signature

ROOT = Path(__file__).resolve().parents[1]
DATA = (ROOT / "data/dirty/development/customers.csv").read_bytes()
PHONE = "customers.phone.pattern"
STATUS = "customers.customer_status.comparison_normalization"


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws))


def prepare(client, data=DATA, source="거래처A"):
    run = client.post("/api/runs", files=[("files", ("customers.csv", data, "text/csv"))],
                      data={"set": "shop", "source": source}).json()
    d = client.get(f"/api/runs/{run['id']}/drift").json()
    if d["required"] and not d["confirmed"]:
        assert client.post(f"/api/runs/{run['id']}/drift/confirm").status_code == 200
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    assert client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps}).status_code == 200
    assert client.post(f"/api/runs/{run['id']}/diagnose").status_code == 200
    return run, client.get(f"/api/runs/{run['id']}/preview").json()


def groups(preview):
    return {g["id"]: g for g in preview["groups"]}


def cells(client, run, gid):
    return client.get(f"/api/runs/{run['id']}/preview/groups/{gid}", params={"limit": 500}).json()["items"]


def execute(client, run, preview, reuse_choice=None):
    body = {"approval": preview["approval"]} | ({"reuse": reuse_choice} if reuse_choice else {})
    res = client.post(f"/api/runs/{run['id']}/execute", json=body).json()
    assert res["status"] in ("completed", "completed_with_issues")
    return res


def seed(client, groups_=(PHONE, STATUS), drop_shape=None, source="거래처A"):
    """첫 실행: 묶음을 재사용 규칙으로 승인해 발행. drop_shape가 있으면 그 형태의 전화번호 셀은 제외하고 승인한다."""
    run, p = prepare(client, source=source)
    if drop_shape:
        off = [c["cell"] for c in cells(client, run, PHONE) if signature(c["before"]) == drop_shape]
        assert off
        p = client.put(f"/api/runs/{run['id']}/selection",
                       json={"view_hash": p["view_hash"], "approved_groups": [g["id"] for g in p["groups"] if g["cells"]],
                             "excluded_cells": off}).json()
    execute(client, run, p, {"groups": list(groups_), "mappings": []})
    return run


def phone_shapes(client, run):
    out = {}
    for c in cells(client, run, PHONE):
        out.setdefault(signature(c["before"]), set()).add(c["reuse"])
    return out


def test_no_rule_starts_all_selected(client):
    _, p = prepare(client)
    assert all(g["approved"] and g["excluded"] == 0 and g["reuse"] == 0 for g in p["groups"] if g["cells"])


def test_mapping_rule_only_keeps_default(client):
    """표준화 규칙이 없는 양식은 매핑 규칙만 있어도 지금처럼 전체 선택."""
    run, p = prepare(client)
    execute(client, run, p, {"groups": [], "mappings": [run["files"][0]["item_id"]]})
    _, p2 = prepare(client)
    assert all(g["approved"] and g["excluded"] == 0 for g in p2["groups"] if g["cells"])


def test_rule_cells_preselected_new_groups_unselected(client):
    seed(client)
    run, p = prepare(client)
    g = groups(p)
    assert g[PHONE]["approved"] and g[STATUS]["approved"]
    assert g[PHONE]["reuse"] == g[PHONE]["cells"] and g[PHONE]["excluded"] == 0
    others = [x for i, x in g.items() if i not in (PHONE, STATUS) and x["cells"]]
    assert others and not any(x["approved"] or x["reuse"] for x in others)  # 새 묶음은 일반 검토
    assert all(c["reuse"] for c in cells(client, run, PHONE))
    assert p["total_cells"] == g[PHONE]["cells"] + g[STATUS]["cells"]


def test_new_shape_cells_not_preselected(client):
    """규칙의 승인 서명에 없는 형태(새 패턴)는 같은 묶음 안에서도 미리 선택되지 않는다(AC-09-2)."""
    seed(client, groups_=(PHONE,), drop_shape="(9) 9-9")
    run, p = prepare(client)
    shapes = phone_shapes(client, run)
    assert shapes.pop("(9) 9-9") == {False}
    assert all(v == {True} for v in shapes.values())
    g = groups(p)[PHONE]
    assert g["approved"] and g["excluded"] == g["cells"] - g["reuse"] > 0
    assert [c["cell"] for c in cells(client, run, PHONE) if not c["reuse"]] == p["excluded_cells"]


def test_preselected_needs_final_confirm(client, ws):
    """AC-09-3: 미리 선택은 실행 승인 전 아무것도 바꾸지 않고, 승인하면 미리 선택된 셀만 적용된다."""
    seed(client)
    run, p = prepare(client)
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "awaiting_approval"
    assert client.get(f"/api/runs/{run['id']}/result").status_code == 409
    res = execute(client, run, p)
    g = groups(p)
    assert res["changed_cells"] == g[PHONE]["reuse"] + g[STATUS]["reuse"]


def test_drift_changed_column_not_preselected(client):
    """AC-09-2: 스키마 변경이 감지된 컬럼의 셀에는 재사용 표시·미리 선택이 없다."""
    seed(client)
    t = list(csv.reader(io.StringIO(DATA.decode("utf-8-sig"))))
    i = t[0].index("회원상태")
    t[1][i] = "DORMANT"
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(t)
    run, p = prepare(client, buf.getvalue().encode("utf-8"))
    d = client.get(f"/api/runs/{run['id']}/drift").json()
    (entry,) = d["items"]
    assert any(c["kind"] == "category_new" for c in entry["changes"])
    g = groups(p)
    assert g[STATUS]["reuse"] == 0 and not g[STATUS]["approved"]
    assert g[PHONE]["reuse"] == g[PHONE]["cells"] and g[PHONE]["approved"]


def test_other_source_not_affected(client):
    """AC-09-1: 다른 출처(다른 비교·재사용 단위)에는 미리 선택이 나타나지 않는다."""
    seed(client)
    _, p = prepare(client, source="거래처B")
    assert all(g["approved"] and g["reuse"] == 0 for g in p["groups"] if g["cells"])


@pytest.mark.parametrize("bad", ["config", "transform"])
def test_rule_with_other_config_or_transform_not_used(client, ws, bad):
    """승인 당시 설정 해시·변환 ID가 지금과 다른 규칙은 맞는 규칙으로 쓰지 않는다(규칙이 있는 양식이라 선택 해제)."""
    first = seed(client, groups_=())
    w = Workspace(ws)
    db_run = w.load(first["id"])["db_run"]
    tid = templates.for_run(w.store, db_run)[first["files"][0]["item_id"]]
    hashes = w.ledger.current_hashes(db_run)
    current = reuse.transform_id("pattern", w._catalog(w.load(first["id"])).rules["customers"]["phone"])
    spec = {"table": "customers", "column": "phone", "code": "pattern",
            "transform_id": "pattern:other" if bad == "transform" else current,
            "signatures": sorted({signature(c["before"]) for c in cells(client, first, PHONE)})}
    reuse.record(w.store, db_run, [{"template_id": tid, "kind": "standardize", "rule_key": PHONE,
                                    "transform_id": spec["transform_id"], "spec": spec}],
                 hashes | ({"config_hash": "0" * 64} if bad == "config" else {}))
    assert [r["rule_key"] for r in reuse.effective(w.store, tid)] == [PHONE]
    run, p = prepare(client)
    g = groups(p)[PHONE]
    assert g["reuse"] == 0 and not g["approved"]
    assert not any(x["approved"] for x in p["groups"] if x["cells"])


def test_own_rules_not_marked_on_finished_run(client):
    """규칙을 승인한 실행 자신의 미리보기에는 재사용 표시가 붙지 않는다."""
    run = seed(client)
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    assert not any(g["reuse"] for g in p["groups"])
