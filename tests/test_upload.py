"""M1-4 업로드·원본 보관 (FS F01 AC-01-1~5)."""

import hashlib
import os
from pathlib import Path

import pytest

from diagnostics.engine import read_table
from prep import upload as up
from prep.guard import Guard
from prep.store import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    guard = Guard([ws], project_root=tmp_path / "project")
    store = Store(ws, guard)
    yield store, up.BlobStore(ws, guard), ws
    store.close()


def count(store, table):
    return store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_original_kept_byte_for_byte(env):
    """AC-01-1: 보관 원본 해시 = 업로드 바이트, 읽기 전용, 변조는 읽을 때 잡힌다."""
    store, blobs, _ = env
    data = (ROOT / "data/dirty/development/orders.csv").read_bytes()
    out = up.ingest(store, blobs, "판매팀", [("orders.csv", data)])
    item = out["items"][0]
    assert item["sha256"] == hashlib.sha256(data).hexdigest()
    assert blobs.get(item["sha256"]) == data
    assert not os.access(blobs.path(item["sha256"]), os.W_OK)
    assert item["rows"] == 689 and item["read_status"] == "ok"
    p = blobs.path(item["sha256"])
    os.chmod(p, 0o644)
    p.write_bytes(data + b"x")
    with pytest.raises(up.BlobIntegrityError):
        blobs.get(item["sha256"])
    with pytest.raises(up.BlobIntegrityError):
        up.item_rows(blobs, store.upload_items(out["batch_id"])[0])


def test_string_values_preserved(env):
    """AC-01-2: 007·NULL·빈 문자열·공백만 있는 값이 구분된다."""
    store, blobs, _ = env
    out = up.ingest(store, blobs, "s", [("t.csv", b'code,memo\n007,NULL\n"",   \n0,null\n')])
    rows = read_table(blobs.path(out["items"][0]["sha256"]))["rows"]
    assert [r["values"] for r in rows] == [{"code": "007", "memo": "NULL"}, {"code": "", "memo": "   "},
                                           {"code": "0", "memo": "null"}]


@pytest.mark.parametrize(
    "data, record, reason",
    [
        (b"a,a\n1,2\n", None, "header"),
        (b"a,\n1,2\n", None, "header"),
        (b"a,b\n1,2\n3\n", 2, "fields"),
        ('a,b\n가,나\n'.encode("cp949"), None, "인코딩"),
        (b'a,b\n"1,2\n', None, None),
    ],
)
def test_structure_errors_recorded(env, data, record, reason):
    """AC-01-3: 구조 오류는 위치·이유와 함께 기록된다(실행 대상 제외는 F03에서 이 상태로 판단)."""
    store, blobs, _ = env
    out = up.ingest(store, blobs, "s", [("bad.csv", data), ("ok.csv", b"a\n1\n")])
    bad, ok = out["items"]
    assert bad["read_status"] == "error" and bad["read_errors"][0]["record"] == record
    if reason:
        assert reason.lower() in bad["read_errors"][0]["reason"].lower()
    assert ok["read_status"] == "ok"
    rows = store.upload_items(out["batch_id"])
    assert [r["read_status"] for r in rows] == ["error", "ok"]


def test_same_name_files_are_separate_items(env):
    """AC-01-4."""
    store, blobs, _ = env
    out = up.ingest(store, blobs, "s", [("orders.csv", b"a\n1\n"), ("orders.csv", b"a\n2\n"),
                                        ("주문.csv", b"a\n1\n")])
    ids = [i["id"] for i in out["items"]]
    assert len(set(ids)) == 3
    assert out["items"][0]["sha256"] == out["items"][2]["sha256"] != out["items"][1]["sha256"]
    assert len(list(blobs.root.iterdir())) == 2


@pytest.mark.parametrize("source", ["", "   ", None])
def test_source_required(env, source):
    """AC-01-5: 출처 없이 올릴 수 없고, 아무것도 기록되지 않는다."""
    store, blobs, _ = env
    with pytest.raises(up.UploadError, match="출처"):
        up.ingest(store, blobs, source, [("a.csv", b"a\n1\n")])
    assert count(store, "batch") == count(store, "upload_item") == 0


def test_existing_source_reused(env):
    store, blobs, _ = env
    a = up.ingest(store, blobs, "판매팀", [("a.csv", b"a\n1\n")])
    b = up.ingest(store, blobs, " 판매팀 ", [("a.csv", b"a\n1\n")])
    assert a["source_id"] == b["source_id"] and a["batch_id"] != b["batch_id"]
    assert store.events("batch", b["batch_id"])[0]["payload"] == {"items": 1, "source": "판매팀"}


@pytest.mark.parametrize("name", ["../x.csv", "a/b.csv", "a\\b.csv", ".csv", ".hidden.csv", "x.txt", "x\n.csv", ""])
def test_bad_names_rejected(env, name):
    store, blobs, _ = env
    with pytest.raises(up.UploadError):
        up.ingest(store, blobs, "s", [(name, b"a\n1\n")])
    assert count(store, "upload_item") == 0


def test_read_options(env):
    store, blobs, _ = env
    out = up.ingest(store, blobs, "s", [("k.csv", "이름;값\n가;1\n".encode("cp949"))], encoding="cp949",
                    delimiter=";")
    item = out["items"][0]
    assert item["headers"] == ["이름", "값"] and item["rows"] == 1
    assert up.item_rows(blobs, store.upload_items(out["batch_id"])[0]).headers == ["이름", "값"]
    for enc, delim in [("latin-1", ","), ("utf-8", ",,")]:
        with pytest.raises(up.UploadError):
            up.ingest(store, blobs, "s", [("k.csv", b"a\n")], encoding=enc, delimiter=delim)


def test_db_failure_rolls_back_all_items(env, monkeypatch):
    store, blobs, _ = env
    original = store.add_upload_item
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("DB 실패")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "add_upload_item", flaky)
    with pytest.raises(RuntimeError):
        up.ingest(store, blobs, "새출처", [("a.csv", b"a\n1\n"), ("b.csv", b"a\n2\n")])
    assert count(store, "source") == count(store, "batch") == count(store, "upload_item") == 0


def test_blob_names_validated(env):
    _, blobs, _ = env
    for bad in ["../x", "a" * 63, "G" * 64]:
        with pytest.raises(up.UploadError):
            blobs.path(bad)
