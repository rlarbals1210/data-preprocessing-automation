"""웹 미리보기 API (prep/preview.py, api/app.py). 개발 입력만 쓴다."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "workspace"))


def upload(client, names, set_name="shop", source="판매팀"):
    files = [("files", (n, (DEV / n).read_bytes(), "text/csv")) for n in names]
    r = client.post("/api/runs", files=files, data={"set": set_name, "source": source})
    assert r.status_code == 200, r.text
    return r.json()


def confirm(client, run, edit=None):
    files = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"] if not f["read_error"]]
    if edit:
        edit(files)
    return client.put(f"/api/runs/{run['id']}/mapping", json={"files": files})


def execute(client, run, approval=None):
    if approval is None:
        approval = client.get(f"/api/runs/{run['id']}/preview").json()["approval"]
    return client.post(f"/api/runs/{run['id']}/execute", json={"approval": approval})


def test_full_flow_all_tables(client):
    run = upload(client, sorted(p.name for p in DEV.glob("*.csv")))
    assert run["status"] == "needs_confirmation" and run["rows"] == 6409
    assert all(v for f in run["files"] for v in f["mapping"]["columns"].values())
    assert all(not f["proposal"]["needs_confirmation"] and not f["confirmed"] for f in run["files"])
    assert client.post(f"/api/runs/{run['id']}/diagnose").json()["code"] == "mapping_unconfirmed"  # AC-03-3
    assert confirm(client, run).status_code == 200
    d = client.post(f"/api/runs/{run['id']}/diagnose").json()
    assert d["engine_status"] == "completed" and d["rules"]["not_executed"] == 0
    assert d["category_counts"]["standardization_candidate"] == 4424
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    assert p["total_cells"] == 4424 and p["groups"][0]["examples"] and all(g["approved"] for g in p["groups"])
    res = execute(client, run).json()
    assert res["changed_cells"] == 4424 and res["rows"] == 6409
    full = client.get(f"/api/runs/{run['id']}/result").json()
    assert len(full["files"]) == 13
    ch = client.get(f"/api/runs/{run['id']}/changes", params={"limit": 5}).json()
    assert ch["total"] == 4424 and len(ch["items"]) == 5
    first = ch["items"][0]
    assert first["source"]["file"] == f"{first['table']}.csv" and first["source"]["record"] >= 1
    iss = client.get(f"/api/runs/{run['id']}/issues", params={"category": "human_review", "limit": 3}).json()
    assert all(i["source"]["file"].endswith(".csv") and i["source"]["line_start"] >= 2 for i in iss["items"])
    dl = client.get(f"/api/runs/{run['id']}/files/orders.csv")
    assert dl.status_code == 200 and dl.text.startswith("order_id,")
    assert client.get("/api/runs").json()[0]["id"] == run["id"]


def test_partial_upload_skips_dependent_rules(client):
    run = upload(client, ["orders.csv"])
    confirm(client, run)
    d = client.post(f"/api/runs/{run['id']}/diagnose").json()
    assert d["engine_status"] == "completed"
    reasons = {(s["kind"], s["reason"]) for s in d["skipped_rules"]}
    assert ("reference", "excluded_target") in reasons
    assert d["completeness"] == "partial" and d["plan_tables"]["orders"]["publishable"]


def test_profile_mode(client):
    run = upload(client, ["categories.csv"], "none")
    d = client.post(f"/api/runs/{run['id']}/diagnose").json()
    assert d["mode"] == "profile"


@pytest.mark.parametrize("name", ["orders.txt", ".csv", "x" * 300 + ".csv", "x.csv.txt"])
def test_bad_file_names_rejected(client, name):
    r = client.post("/api/runs", files=[("files", (name, b"a\n1\n", "text/csv"))],
                    data={"set": "shop", "source": "s"})
    assert r.status_code == 400 and r.json()["code"] == "bad_upload"


def test_source_required_and_listed(client):
    r = client.post("/api/runs", files=[("files", ("a.csv", b"a\n1\n", "text/csv"))], data={"set": "shop"})
    assert r.status_code == 400 and "출처" in r.json()["message"]
    upload(client, ["categories.csv"], source="물류팀")
    upload(client, ["categories.csv"], source="물류팀")
    assert [s["name"] for s in client.get("/api/sources").json()] == ["물류팀"]


def test_same_name_files_kept_separately(client):
    data = (DEV / "categories.csv").read_bytes()
    r = client.post("/api/runs", files=[("files", ("categories.csv", data, "text/csv"))] * 2,
                    data={"set": "shop", "source": "s"})
    run = r.json()
    assert len({f["item_id"] for f in run["files"]}) == 2
    bad = confirm(client, run)
    assert bad.status_code == 400 and "같은 표준 테이블" in bad.json()["message"]

    def drop_second(files):
        files[1]["table"] = None  # 두 번째 파일은 실행 대상에서 제외
    assert confirm(client, run, drop_second).status_code == 200
    assert client.post(f"/api/runs/{run['id']}/diagnose").status_code == 200


def test_mapping_edit_keeps_auto_proposal_and_invalidates(client):
    """AC-03-2 자동 제안·최종 결과 따로 저장, AC-03-5 매핑 변경 시 이전 진단 무효."""
    run = upload(client, ["orders.csv", "customers.csv"])
    confirm(client, run)
    client.post(f"/api/runs/{run['id']}/diagnose")

    def drop_ordered_at(files):
        orders = next(f for f in files if f["table"] == "orders")
        orders["columns"]["주문일시"] = None
    after = confirm(client, run, drop_ordered_at).json()
    orders = next(f for f in after["files"] if f["name"] == "orders.csv")
    assert orders["mapping"]["columns"]["주문일시"] is None
    assert next(c for c in orders["proposal"]["columns"] if c["original"] == "주문일시")["logical"] == "ordered_at"
    assert after["status"] == "needs_confirmation"
    assert client.get(f"/api/runs/{run['id']}/diagnosis").status_code == 409
    d = client.post(f"/api/runs/{run['id']}/diagnose").json()
    assert {"table": "orders", "kind": "required", "columns": ["ordered_at"], "reason": "missing_column"} in d[
        "skipped_rules"]
    assert d["plan_tables"]["orders"] == {"diagnosable": True, "publishable": False,
                                          "missing_required": ["ordered_at"], "reason": "missing_required"}


def test_mapping_violations_rejected(client):
    run = upload(client, ["orders.csv"])

    def dup(files):
        files[0]["columns"]["고객번호"] = "order_id"
    r = confirm(client, run, dup)
    assert r.status_code == 400 and "같은 표준 컬럼" in r.json()["message"]

    def unknown(files):
        files[0]["columns"]["고객번호"] = "nope"
    assert confirm(client, run, unknown).status_code == 400
    assert client.put(f"/api/runs/{run['id']}/mapping", json={"files": []}).status_code == 400
    assert client.put(f"/api/runs/{run['id']}/mapping", json={"files": "x"}).status_code == 400


def test_propose_other_table(client):
    run = upload(client, ["returns.csv"])
    item = run["files"][0]["item_id"]
    p = client.post(f"/api/runs/{run['id']}/propose", json={"item_id": item, "table": "refunds"}).json()
    assert p["table"]["basis"] == "user" and p["needs_confirmation"]
    assert client.post(f"/api/runs/{run['id']}/propose", json={"item_id": item, "table": "nope"}).status_code == 400


def test_catalog_in_run_view(client):
    run = upload(client, ["orders.csv"])
    v = client.get(f"/api/runs/{run['id']}").json()
    assert "order_id" in v["catalog"]["orders"]["columns"] and "ordered_at" in v["catalog"]["orders"]["required"]


@pytest.mark.parametrize("name", ["../orders.csv", "folder/orders.csv", "../../x/orders.csv"])
def test_upload_paths_reduced_to_file_name(client, tmp_path, name):
    """폴더 업로드·경로 조작 이름은 파일 이름만 남기고 실행 폴더 안에만 쓴다."""
    r = client.post("/api/runs", files=[("files", (name, b"a\n1\n", "text/csv"))],
                    data={"set": "shop", "source": "s"})
    run = r.json()
    assert r.status_code == 200 and run["files"][0]["name"] == "orders.csv"
    assert not list(tmp_path.rglob("orders.csv"))  # 원본은 이름이 아니라 해시로 blobs/에 보관
    assert (tmp_path / "workspace/blobs" / run["files"][0]["sha256"]).is_file()


def test_unknown_run_and_files(client):
    assert client.get("/api/runs/20260101T000000Z-abcdef").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fetc").status_code == 404
    assert client.get("/api/nothing").status_code == 404
    run = upload(client, ["categories.csv"])
    assert client.get(f"/api/runs/{run['id']}/files/../run.json").status_code == 404
    assert client.get(f"/api/runs/{run['id']}/result").status_code == 409


def test_broken_csv_reported(client):
    r = client.post("/api/runs", files=[("files", ("orders.csv", b"a,a\n1,2\n", "text/csv"))],
                    data={"set": "shop", "source": "s"})
    assert r.json()["files"][0]["read_error"]
    assert client.post(f"/api/runs/{r.json()['id']}/diagnose").status_code == 400




def test_extra_columns_not_in_outputs_and_not_duplicates(client):
    """추가 컬럼만 다른 두 행은 중복이 아니고, 보조 컬럼은 다운로드에 나오지 않는다."""
    text = (DEV / "categories.csv").read_text(encoding="utf-8-sig").splitlines()
    rows = [text[0] + ",비고"] + [text[1] + ",첫째", text[1] + ",둘째"] + [r + "," for r in text[2:]]
    r = client.post("/api/runs", files=[("files", ("categories.csv", ("\n".join(rows) + "\n").encode(), "text/csv"))],
                    data={"set": "shop", "source": "s"})
    run = r.json()
    confirm(client, run)
    client.post(f"/api/runs/{run['id']}/diagnose")
    dup = client.get(f"/api/runs/{run['id']}/issues", params={"category": "human_review"}).json()["items"]
    assert not any(i["code"] == "duplicate_row" and i["record"] in (1, 2) for i in dup)
    execute(client, run)
    body = client.get(f"/api/runs/{run['id']}/files/categories.csv").text
    assert "__extras_digest__" not in body and body.startswith("category_id,")


def test_selection_controls_execution(client):
    """T-08·AC-06-5: 선택한 묶음만 적용, 미리보기 건수 = 실행 건수, 잘못된 선택 거부, 재진단 시 선택 무효."""
    run = upload(client, ["orders.csv"])
    confirm(client, run)
    client.post(f"/api/runs/{run['id']}/diagnose")
    groups = client.get(f"/api/runs/{run['id']}/preview").json()["groups"]
    keep = [g["id"] for g in groups if g["column"] == "order_total"]
    p = client.put(f"/api/runs/{run['id']}/selection", json={"approved_groups": keep}).json()
    assert p["total_cells"] == sum(g["cells"] for g in groups if g["id"] in keep)
    assert [g["id"] for g in p["groups"] if g["approved"]] == keep
    bad = client.put(f"/api/runs/{run['id']}/selection", json={"approved_groups": ["orders.nope.type"]})
    assert bad.status_code == 400 and bad.json()["code"] == "bad_selection"
    stale = client.put(f"/api/runs/{run['id']}/selection", json={"view_hash": "x" * 64})
    assert stale.status_code == 400
    client.post(f"/api/runs/{run['id']}/diagnose")  # 다시 진단하면 선택이 초기화된다
    assert all(g["approved"] for g in client.get(f"/api/runs/{run['id']}/preview").json()["groups"])
    p = client.put(f"/api/runs/{run['id']}/selection", json={"approved_groups": keep}).json()
    res = execute(client, run).json()
    assert res["changed_cells"] == p["total_cells"] and res["not_approved"] == p["candidates"] - p["total_cells"]
    changes = client.get(f"/api/runs/{run['id']}/changes", params={"limit": 500}).json()["items"]
    assert {c["column"] for c in changes} == {"order_total"}


def test_empty_selection_changes_nothing(client):
    run = upload(client, ["categories.csv"])
    confirm(client, run)
    client.post(f"/api/runs/{run['id']}/diagnose")
    assert client.put(f"/api/runs/{run['id']}/selection", json={"approved_groups": []}).json()["total_cells"] == 0
    assert execute(client, run).json()["changed_cells"] == 0


def ready(client, names=("orders.csv",)):
    run = upload(client, list(names))
    confirm(client, run)
    client.post(f"/api/runs/{run['id']}/diagnose")
    return run, client.get(f"/api/runs/{run['id']}/preview").json()


def test_status_lifecycle_in_db(client):
    run = upload(client, ["categories.csv"])
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "needs_confirmation"
    confirm(client, run)
    client.post(f"/api/runs/{run['id']}/diagnose")
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "diagnosed"
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "awaiting_approval"
    assert set(p["approval"]) == {"config_hash", "input_hash", "mapping_hash", "plan_hash", "view_hash",
                                  "candidate_hash", "apply_hash"}
    assert execute(client, run).json()["status"] in ("completed", "completed_with_issues")
    v = client.get(f"/api/runs/{run['id']}").json()
    assert v["status"] in ("completed", "completed_with_issues")
    assert [a["kind"] for a in v["approvals"] if a["invalidated_at"] is None] == ["structure", "execution"]
    # 끝난 실행은 다시 진단·매핑·선택·실행할 수 없다
    assert client.post(f"/api/runs/{run['id']}/diagnose").json()["code"] == "finished_run"
    assert confirm(client, run).json()["code"] == "finished_run"
    assert execute(client, run, p["approval"]).json()["code"] == "finished_run"


def test_execution_rejected_when_selection_changed_after_preview(client):
    """AC-06-4: 화면이 본 해시와 지금이 다르면 실행하지 않는다."""
    run, p = ready(client)
    keep = [g["id"] for g in p["groups"]][:1]
    client.put(f"/api/runs/{run['id']}/selection", json={"approved_groups": keep})
    r = execute(client, run, p["approval"])
    assert r.status_code == 409 and r.json()["code"] == "approval_mismatch"
    assert r.json()["details"]["mismatched"] == ["apply_hash"]  # 후보는 그대로, 선택만 바뀜
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "awaiting_approval"
    fresh = client.get(f"/api/runs/{run['id']}/preview").json()["approval"]
    assert execute(client, run, fresh).status_code == 200


def test_execution_rejected_after_mapping_change(client):
    run, p = ready(client)

    def drop(files):
        files[0]["columns"]["배송요청"] = None
    confirm(client, run, drop)
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "needs_confirmation"
    r = execute(client, run, p["approval"])
    assert r.status_code == 409


def test_execution_rejected_when_snapshot_tampered(client, tmp_path):
    """AC-07-6: 승인 이후 설정(스냅샷)이 바뀌면 실행하지 않는다."""
    run, p = ready(client)
    snap = tmp_path / "workspace/config-snapshots" / p["approval"]["config_hash"] / "schema.json"
    snap.chmod(0o644)
    snap.write_text(snap.read_text(encoding="utf-8") + " ", encoding="utf-8")
    r = execute(client, run, p["approval"])
    assert r.status_code == 409 and r.json()["code"] == "stale_plan"
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "awaiting_approval"


def test_execute_requires_preview(client):
    run = upload(client, ["orders.csv"])
    confirm(client, run)
    client.post(f"/api/runs/{run['id']}/diagnose")
    r = client.post(f"/api/runs/{run['id']}/execute", json={"approval": {}})
    assert r.status_code == 409 and r.json()["code"] == "not_ready"
