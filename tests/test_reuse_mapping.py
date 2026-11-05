"""M6-6a 매핑 재사용(확신 등급 reuse, M6-D2)·매핑 후 재사용 영향(F03-6) (FS F09 AC-09-1, F10 AC-10-2). 개발 입력의 변형만 쓴다."""

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from prep import publish, reuse, templates
from prep.preview import Workspace

ROOT = Path(__file__).resolve().parents[1]
DATA = (ROOT / "data/dirty/development/categories.csv").read_bytes()
ENGLISH = {"분류번호": "category_id", "상위분류번호": "parent_category_id", "분류명": "category_name",
           "사용여부": "is_active"}


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws))


def rows():
    return list(csv.reader(io.StringIO(DATA.decode("utf-8-sig"))))


def encode(table):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(table)
    return buf.getvalue().encode("utf-8")


def english() -> bytes:
    """AC-10-2용 개발 테스트 입력: 한글 헤더 → 영문 표준 이름(값은 그대로)."""
    t = rows()
    return encode([[ENGLISH[h] for h in t[0]], *t[1:]])


def numeric_names() -> bytes:
    """분류명만 값 모양을 바꾼 복사본(헤더 동일)."""
    t = rows()
    i = t[0].index("분류명")
    return encode([t[0], *[r[:i] + [str(n)] + r[i + 1:] for n, r in enumerate(t[1:])]])


def upload(client, data=DATA, source="거래처A"):
    run = client.post("/api/runs", files=[("files", ("categories.csv", data, "text/csv"))],
                      data={"set": "shop", "source": source}).json()
    d = client.get(f"/api/runs/{run['id']}/drift").json()
    if d["required"] and not d["confirmed"]:
        assert client.post(f"/api/runs/{run['id']}/drift/confirm").status_code == 200
        run = client.get(f"/api/runs/{run['id']}").json()
    return run


def put_mapping(client, run, columns=None, impact=None):
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    if columns:
        maps[0]["columns"] = {**maps[0]["columns"], **columns}
    body = {"files": maps} | ({"reuse_impact": impact} if impact else {})
    return client.put(f"/api/runs/{run['id']}/mapping", json=body)


def finish(client, run, reuse_choice=None):
    client.post(f"/api/runs/{run['id']}/diagnose")
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    body = {"approval": p["approval"]} | ({"reuse": reuse_choice} if reuse_choice else {})
    return client.post(f"/api/runs/{run['id']}/execute", json=body).json()


def seed(client, source="거래처A", approve_mapping=True):
    """첫 실행: 매핑을 재사용 규칙으로 승인하고 발행한다."""
    run = upload(client, source=source)
    assert put_mapping(client, run).status_code == 200
    item = run["files"][0]["item_id"]
    res = finish(client, run, {"groups": [], "mappings": [item] if approve_mapping else []})
    assert res["status"] in ("completed", "completed_with_issues")
    return run


def grades(run):
    return {c["original"]: c["grade"] for c in run["files"][0]["proposal"]["columns"]}


def test_same_template_gets_reuse_grade(client):
    seed(client)
    run = upload(client)
    p = run["files"][0]["proposal"]
    assert set(grades(run).values()) == {"reuse"} and p["needs_confirmation"] is False
    assert p["table"]["basis"] == "reuse" and {c["reuse_version"] for c in p["columns"]} == {1}
    assert put_mapping(client, run).status_code == 200  # 규칙대로면 영향 선택 없음


def test_no_rule_no_reuse_grade(client):
    seed(client, approve_mapping=False)
    assert "reuse" not in grades(upload(client)).values()


def test_failed_run_rule_is_not_used(client, monkeypatch):
    """M6-D1: 결과를 발행하지 못한 실행의 매핑 규칙은 다음 실행의 자동 제안이 되지 않는다."""
    with monkeypatch.context() as m:
        m.setattr(publish, "write_files", lambda *a, **k: (_ for _ in ()).throw(OSError("쓰기 실패 흉내")))
        run = upload(client)
        put_mapping(client, run)
        assert finish(client, run, {"groups": [], "mappings": [run["files"][0]["item_id"]]})["status"] == "failed"
    assert "reuse" not in grades(upload(client)).values()


def test_drift_changed_column_is_not_reused(client):
    """AC-09-2: 스키마 변경(값 모양)이 감지된 컬럼에는 재사용 등급이 붙지 않는다."""
    seed(client)
    run = upload(client, numeric_names())
    g = grades(run)
    assert g["분류명"] != "reuse" and g["분류번호"] == "reuse"


def test_reuse_only_within_same_unit(client):
    """AC-09-1: 같은 출처의 다른 양식·다른 출처에는 재사용 규칙이 나타나지 않는다."""
    seed(client)
    t = rows()
    extra = encode([[*t[0], "비고"], *[[*r, "x"] for r in t[1:]]])
    assert "reuse" not in grades(upload(client, extra)).values()
    assert "reuse" not in grades(upload(client, source="거래처B")).values()


def test_mapping_changed_requires_impact_choice(client, ws):
    seed(client)
    run = upload(client)
    r = put_mapping(client, run, columns={"상위분류번호": None})
    assert r.status_code == 409 and r.json()["code"] == "reuse_impact_needed"
    (impact,) = r.json()["details"]["impacts"]
    assert impact["kind"] == "mapping_changed" and impact["version"] == 1
    assert impact["diff"] == [{"column": "상위분류번호", "rule": "parent_category_id", "now": None}]
    item = run["files"][0]["item_id"]
    bad = put_mapping(client, run, columns={"상위분류번호": None}, impact={str(item): "keep"})
    assert bad.status_code == 409
    assert put_mapping(client, run, columns={"상위분류번호": None}, impact={str(item): "this_run"}).status_code == 200
    finish(client, run)
    w = Workspace(ws)
    tid = templates.for_run(w.store, w.load(run["id"])["db_run"])[item]
    assert [r["version"] for r in reuse.effective(w.store, tid)] == [1]  # 이번만: 규칙 그대로


def test_new_version_recorded_at_final_confirm(client, ws):
    seed(client)
    run = upload(client)
    item = run["files"][0]["item_id"]
    assert put_mapping(client, run, columns={"상위분류번호": None}, impact={str(item): "new_version"}).status_code == 200
    w = Workspace(ws)
    tid = templates.for_run(w.store, w.load(run["id"])["db_run"])[item]
    assert [r["version"] for r in reuse.for_template(w.store, tid)] == [1]  # 매핑 확정만으로는 기록 안 함
    assert finish(client, run)["reuse_rules"] == 1
    (rule,) = reuse.effective(w.store, tid)
    assert rule["version"] == 2 and rule["spec"]["columns"]["상위분류번호"] is None


def test_ac_10_2_header_change_goes_through_impact(client, ws):
    """AC-10-2: 한글 → 영문 헤더로 바꾼 파일은 매핑이 같아도 재사용 규칙이 F03 선택지를 거친다."""
    first = seed(client)
    run = upload(client, english())
    assert "reuse" not in grades(run).values()
    r = put_mapping(client, run)
    assert r.status_code == 409
    (impact,) = r.json()["details"]["impacts"]
    assert impact["kind"] == "header_changed"
    assert impact["headers"] == sorted(v.replace("_", "") for v in ENGLISH.values())
    assert [p["rules"] for p in impact["prior"]] == [1]
    item = run["files"][0]["item_id"]
    assert put_mapping(client, run, impact={str(item): "new_version"}).status_code == 200
    assert finish(client, run)["reuse_rules"] == 1
    w = Workspace(ws)
    old_t = templates.for_run(w.store, w.load(first["id"])["db_run"])[first["files"][0]["item_id"]]
    new_t = templates.for_run(w.store, w.load(run["id"])["db_run"])[item]
    assert old_t != new_t
    assert [r["version"] for r in reuse.effective(w.store, old_t)] == [1]  # 규칙을 새 양식으로 옮기지 않음
    assert [r["version"] for r in reuse.effective(w.store, new_t)] == [1]
