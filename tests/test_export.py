"""M2-1 평가용 내보내기 (④ 5-1절, FS F14). 개발 입력만 쓴다."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from prep import export, publish
from prep.preview import FlowError, Workspace

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws))


def dev(name):
    return (name, (DEV / name).read_bytes())


def flow(client, files, edit=None, execute=True):
    run = client.post("/api/runs", files=[("files", (n, d, "text/csv")) for n, d in files],
                      data={"set": "shop", "source": "s"}).json()
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"] if not f["read_error"]]
    if edit:
        edit(maps)
    assert client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps}).status_code == 200
    client.post(f"/api/runs/{run['id']}/diagnose")
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    if execute:
        client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]})
    return run


def read(folder, name):
    return json.loads((folder / name).read_text(encoding="utf-8"))


def test_export_published_run(client, ws):
    run = flow(client, [dev("orders.csv"), dev("customers.csv")])
    out = export.export_run(Workspace(ws), run["id"])
    assert out == ws / "exports" / run["id"] / "export-1"
    meta = read(out, "export.json")
    assert meta["format"] == "export-v1" and meta["published"] and meta["status"] == "completed_with_issues"
    assert meta["config_hash"] and meta["view_hash"] and meta["plan_hash"]
    assert meta["attempt"]["outcome"] == "engine_ok" and meta["attempt"]["audit"]["passed"]
    # 적어 둔 해시가 실제 파일과 같다
    import hashlib
    for rel, sha in meta["files"].items():
        assert hashlib.sha256((out / rel).read_bytes()).hexdigest() == sha
    assert {"inputs.json", "mapping.json", "lineage.json", "diagnosis.json", "apply-plan.json",
            "attempt/changes.json", "attempt/held.json", "attempt/after.json",
            "result/orders.csv", "result/customers.csv", "result/changes.csv"} <= set(meta["files"])
    inputs = read(out, "inputs.json")
    assert [i["name"] for i in inputs] == ["orders.csv", "customers.csv"]
    assert inputs[0]["sha256"] and inputs[0]["headers"][0] == "주문번호"
    mapping = read(out, "mapping.json")
    assert mapping[0]["proposal"]["columns"]["주문번호"] == {"logical": "order_id", "grade": "alias",
                                                           "needs_confirmation": False}
    assert mapping[0]["final"]["columns"]["주문번호"] == "order_id" and mapping[0]["confirmed"]
    # 결과 k번째 행 = 뷰 레코드 k
    lineage = read(out, "lineage.json")
    rows = (out / "result/orders.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) - 1 == len(lineage["tables"]["orders"]["rows"])
    # 파일은 읽기 전용
    assert (out / "export.json").stat().st_mode & 0o222 == 0


def test_export_keeps_frozen_proposal_when_user_changes_mapping(client, ws):
    """자동 제안은 사람 확인 전 동결본, 최종 매핑은 따로 남는다(④ 5-3 map_column)."""
    def edit(maps):
        maps[0]["columns"]["배송요청"] = None
    run = flow(client, [dev("orders.csv")], edit)
    m = read(export.export_run(Workspace(ws), run["id"]), "mapping.json")[0]
    assert m["proposal"]["columns"]["배송요청"]["logical"] == "delivery_memo"
    assert m["final"]["columns"]["배송요청"] is None


def test_export_failed_run_has_no_result(client, ws, monkeypatch):
    def boom(*a, **k):
        raise OSError("쓰기 실패 흉내")
    with monkeypatch.context() as m:
        m.setattr(publish, "write_files", boom)
        run = flow(client, [dev("categories.csv")])
    meta = read(export.export_run(Workspace(ws), run["id"]), "export.json")
    assert meta["status"] == "failed" and not meta["published"] and meta["result_version"] is None
    assert meta["attempt"]["outcome"] == "publish_failed" and "쓰기 실패" in meta["attempt"]["error"]
    assert not any(k.startswith("result/") for k in meta["files"])


def test_export_unfinished_run_refused(client, ws):
    run = flow(client, [dev("categories.csv")], execute=False)
    with pytest.raises(FlowError) as e:
        export.export_run(Workspace(ws), run["id"])
    assert e.value.code == "not_finished"
    assert not (ws / "exports" / run["id"]).exists() or not any((ws / "exports" / run["id"]).iterdir())


def test_export_numbers_do_not_overwrite(client, ws):
    run = flow(client, [dev("categories.csv")])
    a = export.export_run(Workspace(ws), run["id"])
    b = export.export_run(Workspace(ws), run["id"])
    assert (a.name, b.name) == ("export-1", "export-2")
    assert (a / "export.json").read_bytes() == (b / "export.json").read_bytes()  # 같은 실행 → 같은 내용


def test_export_tampered_result_refused(client, ws):
    run = flow(client, [dev("categories.csv")])
    folder = next((ws / "runs" / run["id"]).glob("attempt-*/result"))
    target = folder / "categories.csv"
    os.chmod(target, 0o644)
    target.write_text("변조\n", encoding="utf-8")
    with pytest.raises(publish.PublishError):
        export.export_run(Workspace(ws), run["id"])
    assert not any(p.name.startswith(".staging") for p in (ws / "exports" / run["id"]).iterdir())


def test_export_cli(client, ws, capsys):
    run = flow(client, [dev("categories.csv")])
    assert export.main([run["id"], "--workspace", str(ws)]) == 0
    assert capsys.readouterr().out.strip().endswith("export-1")
    assert export.main(["20990101T000000Z-000000", "--workspace", str(ws)]) == 1
