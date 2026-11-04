"""M5-1 결과 버전 이력·복원 (FS F08 AC-08-1~4, TD 10-1). 개발 입력만 쓴다."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from prep import publish, versions
from prep.preview import Workspace

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data/dirty/development"


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws))


def flow(client, data: bytes, source="거래처A", select=None):
    run = client.post("/api/runs", files=[("files", ("categories.csv", data, "text/csv"))],
                      data={"set": "shop", "source": source}).json()
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps})
    client.post(f"/api/runs/{run['id']}/diagnose")
    if select is not None:
        client.put(f"/api/runs/{run['id']}/selection", json={"approved_groups": select})
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    return run, client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]}).json()


def counts(ws):
    c = sqlite3.connect(ws / "app.db")
    return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("approval", "mapping_version", "run", "attempt")} | {
        "approvals": c.execute("SELECT * FROM approval ORDER BY id").fetchall(),
        "cleaned": c.execute("SELECT * FROM result_version WHERE kind = 'cleaned' ORDER BY id").fetchall()}


@pytest.fixture
def two_runs(client):
    data = (DEV / "categories.csv").read_bytes()
    r1, res1 = flow(client, data, select=[])  # 첫 실행: 아무것도 승인하지 않음(원문 그대로 발행)
    r2, res2 = flow(client, data)  # 두 번째 실행: 전체 승인
    sid = next(s["id"] for s in client.get("/api/sources").json() if s["name"] == "거래처A")
    return sid, (r1, res1), (r2, res2)


def test_versions_listed_per_source(client, two_runs):
    sid, (r1, res1), (r2, res2) = two_runs
    vs = client.get(f"/api/sources/{sid}/versions").json()
    assert [v["kind"] for v in vs] == ["cleaned", "cleaned"] and [v["current"] for v in vs] == [False, True]
    assert [v["run"] for v in vs] == [r1["id"], r2["id"]]
    assert vs[0]["changed_cells"] == 0 and vs[1]["changed_cells"] == res2["changed_cells"] > 0
    assert vs[0]["data_hash"] != vs[1]["data_hash"]
    src = next(s for s in client.get("/api/sources").json() if s["id"] == sid)
    assert src["versions"] == 2 and src["current_version"] == vs[1]["id"]


def test_restore_makes_new_version_and_keeps_history(client, ws, two_runs):
    """AC-08-1·08-2·08-3: 되돌리기는 새 복원 버전, 데이터 해시 동일, 과거 버전·승인·매핑 기록 불변."""
    sid, (r1, _), _ = two_runs
    v1, v2 = client.get(f"/api/sources/{sid}/versions").json()
    before = counts(ws)
    r = client.post(f"/api/versions/{v1['id']}/restore")
    assert r.status_code == 200
    v3 = r.json()
    assert v3["kind"] == "restore" and v3["restored_from"] == v1["id"] and v3["origin"] == v1["id"] and v3["current"]
    assert v3["data_hash"] == v1["data_hash"] and v3["run"] == r1["id"]
    after = counts(ws)
    assert after == before  # 승인·매핑 버전·실행·시도·정제 버전 행이 그대로(복원은 결과 버전 한 행만 더함)
    vs = client.get(f"/api/sources/{sid}/versions").json()
    assert [v["id"] for v in vs] == [v1["id"], v2["id"], v3["id"]] and vs[-1]["current"]
    # 내려받은 데이터가 선택한 버전과 바이트까지 같다
    a = client.get(f"/api/versions/{v1['id']}/files/categories.csv").content
    b = client.get(f"/api/versions/{v3['id']}/files/categories.csv").content
    assert a == b and a != client.get(f"/api/versions/{v2['id']}/files/categories.csv").content
    # 당시 근거: 원래 정제 버전 실행의 매핑·설정·승인 해시
    d = client.get(f"/api/versions/{v3['id']}").json()
    d1 = client.get(f"/api/versions/{v1['id']}").json()
    assert d["refs"] == d1["refs"] and d["refs"]["approval_hashes"]["apply_hash"]
    assert [e["kind"] for e in d["events"]] == ["restored"]
    assert d["events"][0]["payload"]["previous_current"] == v2["id"]
    # 실행 결과 화면은 그 실행의 정제 버전을 그대로 보여 준다
    assert client.get(f"/api/runs/{r1['id']}/result").json()["result_version"] == v1["id"]


def test_restore_rules(client, two_runs):
    sid, _, _ = two_runs
    v1, v2 = client.get(f"/api/sources/{sid}/versions").json()
    assert client.post(f"/api/versions/{v2['id']}/restore").json()["code"] == "already_current"
    v3 = client.post(f"/api/versions/{v1['id']}/restore").json()
    assert client.post(f"/api/versions/{v3['id']}/restore").status_code == 409  # 현재 버전
    v4 = client.post(f"/api/versions/{v2['id']}/restore").json()
    assert v4["origin"] == v2["id"] and v4["current"]
    v5 = client.post(f"/api/versions/{v3['id']}/restore").json()  # 복원 버전으로 다시 되돌리기
    assert v5["restored_from"] == v3["id"] and v5["origin"] == v1["id"] and v5["data_hash"] == v1["data_hash"]
    assert client.post("/api/versions/9999/restore").status_code == 404


def test_schema_baseline_ignores_restore(client, ws, two_runs):
    """AC-08-4: 오래된 결과를 복원해도 스키마 변경 비교 기준은 마지막 업로드 실행이다."""
    sid, _, (r2, _) = two_runs
    v1 = client.get(f"/api/sources/{sid}/versions").json()[0]
    w = Workspace(ws)
    run2 = w.load(r2["id"])["db_run"]
    assert versions.schema_baseline_run(w.store, sid) == run2
    client.post(f"/api/versions/{v1['id']}/restore")
    assert versions.schema_baseline_run(w.store, sid) == run2


def test_failed_run_has_no_version(client, monkeypatch):
    def boom(*a, **k):
        raise OSError("쓰기 실패 흉내")
    with monkeypatch.context() as m:
        m.setattr(publish, "write_files", boom)
        flow(client, (DEV / "categories.csv").read_bytes(), source="거래처B")
    sid = next(s["id"] for s in client.get("/api/sources").json() if s["name"] == "거래처B")
    assert client.get(f"/api/sources/{sid}/versions").json() == []  # 실패 실행은 복원 대상이 없다


def test_versions_append_only(ws, two_runs):
    c = sqlite3.connect(ws / "app.db")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM result_version")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE result_version SET data_hashes = '{}'")


def test_version_file_checks(client, ws, two_runs):
    sid, _, _ = two_runs
    v1 = client.get(f"/api/sources/{sid}/versions").json()[0]
    assert client.get(f"/api/versions/{v1['id']}/files/../app.db").status_code == 404
    assert client.get(f"/api/versions/{v1['id']}/files/secret.txt").status_code == 404
    assert client.get(f"/api/versions/{v1['id']}/files/manifest.json").status_code == 200
