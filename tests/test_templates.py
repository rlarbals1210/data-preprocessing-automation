"""M6-2 양식·기준 프로파일 저장 (FS 1절·F10, TD 10-3, 결정 M6-D3, AC-08-4). 개발 입력과 그 변형 복사본만 쓴다."""

import csv
import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from prep import publish, templates
from prep.preview import Workspace

ROOT = Path(__file__).resolve().parents[1]
DATA = (ROOT / "data/dirty/development/categories.csv").read_bytes()


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "workspace"


@pytest.fixture
def client(ws):
    return TestClient(create_app(ws))


def upload(client, data=DATA, source="거래처A", name="categories.csv"):
    return client.post("/api/runs", files=[("files", (name, data, "text/csv"))],
                       data={"set": "shop", "source": source}).json()


def confirm(client, run, columns=None):
    d = client.get(f"/api/runs/{run['id']}/drift").json()
    if d["required"] and not d["confirmed"]:  # M6-4: 같은 단위의 변경은 매핑 전에 확인한다
        assert client.post(f"/api/runs/{run['id']}/drift/confirm").status_code == 200
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    if columns is not None:
        maps[0]["columns"] = columns
    r = client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps})
    assert r.status_code == 200, r.text
    return r.json()


def finish(client, run):
    client.post(f"/api/runs/{run['id']}/diagnose")
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    return client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]}).json()


def flow(client, **kw):
    run = upload(client, **kw)
    confirm(client, run)
    return run, finish(client, run)


def drop_column(data: bytes, header: str) -> bytes:
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    i = rows[0].index(header)
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows([r[:i] + r[i + 1:] for r in rows])
    return out.getvalue().encode("utf-8")


def rename_header(data: bytes, old: str, new: str) -> bytes:
    text = data.decode("utf-8-sig")
    head, rest = text.split("\n", 1)
    return (head.replace(old, new) + "\n" + rest).encode("utf-8")


def db_run(ws, run):
    return Workspace(ws).load(run["id"])["db_run"]


def source_id(client, name="거래처A"):
    return next(s["id"] for s in client.get("/api/sources").json() if s["name"] == name)


# ---- header_signature ----

def test_header_signature_normalizes_and_ignores_order():
    a, na = templates.header_signature(["Order ID", "주문일", "Qty"])
    b, _ = templates.header_signature(["qty", "주문일", "order_id"])
    assert a == b and na == ["orderid", "qty", "주문일"]
    assert templates.header_signature(["a", ""])[0] == templates.header_signature(["a"])[0]  # 빈 머리글 제외
    assert templates.header_signature(["a", "b"])[0] != templates.header_signature(["a", "c"])[0]


# ---- 양식 연결 ----

def test_mapping_links_items_to_template(client, ws):
    run = upload(client)
    confirm(client, run)
    w = Workspace(ws)
    links = templates.for_run(w.store, db_run(ws, run))
    (item,) = [f["item_id"] for f in run["files"]]
    t = templates.get(w.store, links[item])
    assert t["std_table"] == "categories" and t["source_id"] == source_id(client)
    assert t["headers"] == templates.header_signature(run["files"][0]["headers"])[1]
    kinds = [e["kind"] for e in w.store.events("template", t["id"])]
    assert kinds == ["template_created"]


def test_first_run_has_no_baseline_until_published(client, ws):
    """현재 실행은 자기 비교 기준이 될 수 없다 — 발행 전에는 기준이 없다(TD 10-3)."""
    run = upload(client)
    confirm(client, run)
    w = Workspace(ws)
    tid = templates.for_run(w.store, db_run(ws, run))[run["files"][0]["item_id"]]
    assert templates.baseline(w.store, tid) is None
    assert finish(client, run)["status"] in ("completed", "completed_with_issues")
    base = templates.baseline(w.store, tid)
    assert base["run_id"] == db_run(ws, run) and len(base["profiles"]) == 1
    prof = base["profiles"][0]["profile"]
    assert prof["headers"] == run["files"][0]["headers"] and prof["rows"] == run["files"][0]["rows"]
    assert prof["columns"]["분류번호"]["logical"] == "category_id"
    assert [e["kind"] for e in w.store.events("template", tid)] == ["template_created", "baseline_saved"]


def test_same_unit_reuses_template_and_moves_baseline(client, ws):
    r1, _ = flow(client)
    r2, _ = flow(client)
    w = Workspace(ws)
    t1 = templates.for_run(w.store, db_run(ws, r1))
    t2 = templates.for_run(w.store, db_run(ws, r2))
    assert set(t1.values()) == set(t2.values())
    assert templates.baseline(w.store, next(iter(t2.values())))["run_id"] == db_run(ws, r2)


def test_unit_is_source_table_and_template(client, ws):
    """같은 헤더라도 출처가 다르면, 같은 출처라도 헤더 구성이 다르면 다른 양식이다(FS 1절)."""
    flow(client)
    flow(client, source="거래처B")
    flow(client, data=rename_header(DATA, "사용여부", "사용 여부 표시"))  # 정규화해도 다른 헤더
    w = Workspace(ws)
    a, b = source_id(client), source_id(client, "거래처B")
    assert len(templates.for_source(w.store, a)) == 2 and len(templates.for_source(w.store, b)) == 1


def test_normalization_equal_header_is_same_template(client, ws):
    """정규화하면 같은 헤더 차이는 같은 양식이다. 원래 헤더 차이는 프로파일로 따로 본다(QE 3-3)."""
    r1, _ = flow(client)
    r2 = upload(client, data=rename_header(DATA, "분류번호", " 분류_번호 "))
    w = Workspace(ws)
    confirm(client, r2, columns={**r2["files"][0]["mapping"]["columns"], " 분류_번호 ": "category_id"})
    assert set(templates.for_run(w.store, db_run(ws, r1)).values()) == set(
        templates.for_run(w.store, db_run(ws, r2)).values())


def test_failed_run_is_not_baseline(client, ws, monkeypatch):
    """M6-D3: 실패한 실행은 비교 기준이 되지 않는다. 직전 발행 실행이 그대로 기준이다."""
    r1, _ = flow(client)
    with monkeypatch.context() as m:
        m.setattr(publish, "write_files", lambda *a, **k: (_ for _ in ()).throw(OSError("쓰기 실패 흉내")))
        r2, res = flow(client)
    assert res["status"] == "failed"
    w = Workspace(ws)
    tid = next(iter(templates.for_run(w.store, db_run(ws, r1)).values()))
    assert templates.baseline(w.store, tid)["run_id"] == db_run(ws, r1)


def test_profile_failure_rolls_back_publish(client, ws, monkeypatch):
    """기준 저장이 실패하면 결과 버전도 남지 않는다(반쯤 발행된 상태 없음)."""
    def boom(*a, **k):
        raise sqlite3.IntegrityError("저장 실패 흉내")
    with monkeypatch.context() as m:
        m.setattr(templates, "save_profiles", boom)
        _, res = flow(client)
    assert res["status"] == "failed" and res["stage"] == "publish_failed"
    c = sqlite3.connect(ws / "app.db")
    assert c.execute("SELECT COUNT(*) FROM result_version").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM template_profile").fetchone()[0] == 0


def test_excluded_table_saves_no_baseline(client, ws):
    """필수 컬럼이 없어 발행에서 빠진 테이블은 기준이 되지 않는다."""
    run = upload(client, data=drop_column(DATA, "분류명"))
    confirm(client, run)
    res = finish(client, run)
    assert "categories" in res["excluded_tables"]
    w = Workspace(ws)
    tid = templates.for_run(w.store, db_run(ws, run))[run["files"][0]["item_id"]]
    assert templates.baseline(w.store, tid) is None


def test_mapping_reconfirm_records_new_links(client, ws):
    run = upload(client)
    confirm(client, run)
    cols = dict(run["files"][0]["mapping"]["columns"])
    cols["상위분류번호"] = None  # 선택 컬럼을 미매핑으로 바꿔 다시 확정
    confirm(client, run, columns=cols)
    c = sqlite3.connect(ws / "app.db")
    assert c.execute("SELECT COUNT(*) FROM run_template").fetchone()[0] == 2  # 매핑 버전마다
    assert len(templates.for_run(Workspace(ws).store, db_run(ws, run))) == 1


# ---- DB 보호 ----

def test_template_tables_are_append_only(client, ws):
    flow(client)
    c = sqlite3.connect(ws / "app.db")
    c.execute("PRAGMA recursive_triggers = ON")
    for sql in ("DELETE FROM template", "UPDATE template SET std_table = 'x'", "DELETE FROM run_template",
                "UPDATE template_profile SET profile = '{}'", "DELETE FROM template_profile"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute(sql)


def test_profile_only_on_own_cleaned_version(client, ws):
    """기준 프로파일은 그 실행이 발행한 정제 결과에만 붙는다 — 복원 버전·다른 실행 버전은 거부(AC-08-4)."""
    r1, _ = flow(client)
    r2, _ = flow(client)
    sid = source_id(client)
    v1 = client.get(f"/api/sources/{sid}/versions").json()[0]
    restored = client.post(f"/api/versions/{v1['id']}/restore").json()
    w = Workspace(ws)
    tid = next(iter(templates.for_run(w.store, db_run(ws, r1)).values()))
    item = r1["files"][0]["item_id"]
    row = {"template_id": tid, "upload_item_id": item, "order_no": 1, "profile": {}}
    with pytest.raises(sqlite3.IntegrityError, match="정제 결과 버전에만"):
        templates.save_profiles(w.store, restored["id"], db_run(ws, r1), [row])
    with pytest.raises(sqlite3.IntegrityError, match="정제 결과 버전에만"):
        templates.save_profiles(w.store, v1["id"], db_run(ws, r2), [row])
    assert templates.baseline(w.store, tid)["run_id"] == db_run(ws, r2)  # 복원 후에도 기준 불변
