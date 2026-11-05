"""M6-4 매핑 전 스키마 변경 확인 단계 (FS F10 AC-10-1·10-3, FS 6절, AC-08-4, SC03 API). 개발 입력의 변형 복사본만 쓴다."""

import csv
import hashlib
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from prep import publish
from prep.preview import Workspace

ROOT = Path(__file__).resolve().parents[1]
DATA = (ROOT / "data/dirty/development/categories.csv").read_bytes()


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws))


def reshape(drop=(), add=None, data=DATA) -> bytes:
    """헤더를 빼거나 새 컬럼(값 x)을 더한 복사본."""
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    keep = [i for i, h in enumerate(rows[0]) if h not in drop]
    out = [[r[i] for i in keep] + ([add] if add and n == 0 else ["x"] if add else []) for n, r in enumerate(rows)]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(out)
    return buf.getvalue().encode("utf-8")


def upload(client, data=DATA, source="거래처A", set_name="shop"):
    r = client.post("/api/runs", files=[("files", ("categories.csv", data, "text/csv"))],
                    data={"set": set_name, "source": source})
    assert r.status_code == 200, r.text
    return r.json()


def put_mapping(client, run):
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    return client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps})


def publish_run(client, run):
    d = client.get(f"/api/runs/{run['id']}/drift").json()
    if d["required"] and not d["confirmed"]:
        assert client.post(f"/api/runs/{run['id']}/drift/confirm").status_code == 200
    assert put_mapping(client, run).status_code == 200
    client.post(f"/api/runs/{run['id']}/diagnose")
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    return client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]}).json()


def drift(client, run):
    return client.get(f"/api/runs/{run['id']}/drift").json()


def kinds(item):
    return sorted({c["kind"] for c in item["changes"]})


def test_first_upload_creates_baseline_without_confirmation(client):
    run = upload(client)
    d = drift(client, run)
    assert d["required"] is False and d["confirmed"] is True
    assert [i["state"] for i in d["items"]] == ["baseline_create"]
    assert put_mapping(client, run).status_code == 200


def test_same_file_again_has_no_change(client):
    publish_run(client, upload(client))
    d = drift(client, upload(client))
    (item,) = d["items"]
    assert item["state"] == "no_change" and d["required"] is False and item["baseline"]["run"]


def test_ac_10_1_change_blocks_mapping_until_confirmed(client, ws):
    """AC-10-1: 컬럼 하나를 지우고 하나를 더하면 두 변경이 모두 보고되고, 확인 전에는 매핑할 수 없다(FS 6절)."""
    publish_run(client, upload(client))
    run = upload(client, reshape(drop=["상위분류번호"], add="비고"))
    assert client.get(f"/api/runs/{run['id']}").json()["status_reason"] == "schema_drift"
    d = drift(client, run)
    (item,) = d["items"]
    assert d["required"] is True and d["confirmed"] is False and item["state"] == "changed"
    removed = [c["column"] for c in item["changes"] if c["kind"] == "column_removed"]
    added = [c["column"] for c in item["changes"] if c["kind"] == "column_added"]
    assert removed == ["상위분류번호"] and added == ["비고"]
    r = put_mapping(client, run)
    assert r.status_code == 409 and r.json()["code"] == "drift_unconfirmed"
    assert client.post(f"/api/runs/{run['id']}/drift/confirm").json()["confirmed"] is True
    assert put_mapping(client, run).status_code == 200
    events = [e for e in Workspace(ws).store.events("run", Workspace(ws).load(run["id"])["db_run"])
              if e["kind"].startswith("drift_")]
    assert [e["kind"] for e in events] == ["drift_checked", "drift_confirmed"]
    assert len(events[1]["payload"]["hash"]) == 64 and events[1]["payload"]["items"][0]["changes"] == len(item["changes"])


def test_ac_10_3_drift_changes_no_values(client, ws):
    """AC-10-3: 변경 감지·확인은 원본·매핑 제안을 바꾸지 않는다."""
    publish_run(client, upload(client))
    data = reshape(drop=["상위분류번호"], add="비고")
    run = upload(client, data)
    before = client.get(f"/api/runs/{run['id']}").json()
    client.post(f"/api/runs/{run['id']}/drift/confirm")
    after = client.get(f"/api/runs/{run['id']}").json()
    assert [f["proposal"] for f in after["files"]] == [f["proposal"] for f in before["files"]]
    (f,) = after["files"]
    assert f["sha256"] == hashlib.sha256(data).hexdigest()
    assert (ws / "blobs" / f["sha256"]).read_bytes() == data


def test_tie_needs_user_choice(client):
    """후보 양식 1위가 동률이면 자동으로 정하지 않는다(TD 10-3)."""
    for data in (DATA, reshape(add="비고")):  # 양식 두 개: 원본 / 원본 + 비고
        run = upload(client, data)
        client.post(f"/api/runs/{run['id']}/drift/confirm") if drift(client, run)["required"] else None
        assert put_mapping(client, run).status_code == 200
    run = upload(client, reshape(drop=["사용여부"], add="메모"))  # 두 양식과 3개씩 겹침
    (item,) = drift(client, run)["items"]
    assert item["state"] == "choose" and item["template_id"] is None and len(item["candidates"]) == 2
    r = client.post(f"/api/runs/{run['id']}/drift/confirm")
    assert r.status_code == 409 and r.json()["code"] == "drift_choice_needed"
    assert put_mapping(client, run).json()["code"] == "drift_unconfirmed"
    first = item["candidates"][0]["template_id"]
    chosen = client.post(f"/api/runs/{run['id']}/drift/select",
                         json={"item_id": item["item_id"], "template_id": first}).json()
    assert chosen["items"][0]["state"] == "no_baseline" and chosen["items"][0]["chosen_by"] == "user"
    assert chosen["confirmed"] is False  # 고른 뒤에도 확인은 따로
    assert client.post(f"/api/runs/{run['id']}/drift/confirm").json()["confirmed"] is True
    assert put_mapping(client, run).status_code == 200


def test_select_validation_and_baseline_create(client):
    publish_run(client, upload(client))
    run = upload(client, reshape(drop=["상위분류번호"], add="비고"))
    (item,) = drift(client, run)["items"]
    r = client.post(f"/api/runs/{run['id']}/drift/select", json={"item_id": item["item_id"], "template_id": 999})
    assert r.status_code == 400 and r.json()["code"] == "bad_template"
    r = client.post(f"/api/runs/{run['id']}/drift/select", json={"item_id": 999, "template_id": None})
    assert r.json()["code"] == "bad_item"
    d = client.post(f"/api/runs/{run['id']}/drift/select",
                    json={"item_id": item["item_id"], "template_id": None}).json()
    assert d["items"][0]["state"] == "baseline_create" and d["items"][0]["changes"] == []


def test_baseline_cannot_change_after_mapping(client):
    publish_run(client, upload(client))
    run = upload(client, reshape(drop=["상위분류번호"], add="비고"))
    client.post(f"/api/runs/{run['id']}/drift/confirm")
    put_mapping(client, run)
    (item,) = drift(client, run)["items"]
    r = client.post(f"/api/runs/{run['id']}/drift/select", json={"item_id": item["item_id"], "template_id": None})
    assert r.status_code == 409 and r.json()["code"] == "mapping_confirmed"


def test_restore_does_not_move_comparison_basis(client):
    """AC-08-4: 오래된 결과를 복원해도 다음 업로드는 마지막 업로드 실행 기준으로 비교한다."""
    publish_run(client, upload(client))
    changed = reshape(add="비고")
    publish_run(client, upload(client, changed))  # 다른 양식(비고 추가) — 그 양식의 기준이 된다
    publish_run(client, upload(client))  # 원래 양식 두 번째 발행
    sid = next(s["id"] for s in client.get("/api/sources").json() if s["name"] == "거래처A")
    first = client.get(f"/api/sources/{sid}/versions").json()[0]
    client.post(f"/api/versions/{first['id']}/restore")
    (item,) = drift(client, upload(client))["items"]
    third = client.get(f"/api/sources/{sid}/versions").json()[2]
    assert item["state"] == "no_change" and item["baseline"]["result_version_id"] == third["id"]


def test_retry_rechecks_drift(client, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(publish, "write_files", lambda *a, **k: (_ for _ in ()).throw(OSError("쓰기 실패 흉내")))
        run = upload(client)
        assert publish_run(client, run)["status"] == "failed"
    retried = client.post(f"/api/runs/{run['id']}/retry").json()
    d = drift(client, retried)
    assert [i["state"] for i in d["items"]] == ["no_baseline"]  # 첫 실행의 양식은 있으나 발행한 기준이 없다


def test_no_settings_run_has_no_drift_step(client):
    run = upload(client, set_name="none")
    assert drift(client, run) == {"required": False, "confirmed": True, "items": []}
    r = client.post(f"/api/runs/{run['id']}/drift/confirm")
    assert r.status_code == 409 and r.json()["code"] in ("no_drift", "finished_run")
