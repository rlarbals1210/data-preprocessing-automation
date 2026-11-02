"""M1-3a 설정 스냅샷 (docs/technical-design.md 4-2·11-1절)."""

import os
from pathlib import Path

import pytest

from prep import snapshot as snap
from prep.guard import Guard

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "config/sets/shop").mkdir(parents=True)
    (root / "config/sets/shop/logical-diagnostics.json").write_text('{"tables": {}}\n')
    (root / "config/sets/shop/aliases.json").write_text("{}\n")
    (root / "config/shop-diagnostics.json").write_text("{}\n")  # 스냅샷 대상 아님
    (root / "config/normalization-policy.json").write_text('{"p": 1}\n')
    (root / "schema.json").write_text('{"tables": {}}\n')
    (root / "workspace").mkdir()
    return root


@pytest.fixture
def guard(project):
    return Guard([project / "workspace"], project_root=project)


def make(project, guard, name="shop"):
    return snap.create_snapshot(project, name, project / "workspace", guard)


def test_snapshot_contains_fixed_list_only(project, guard):
    s = make(project, guard)
    assert s.files == ("config/normalization-policy.json", "config/sets/shop/aliases.json",
                       "config/sets/shop/logical-diagnostics.json", "schema.json")
    assert s.path == (project / "workspace/config-snapshots" / s.hash).resolve()
    assert (s.path / "schema.json").read_text() == '{"tables": {}}\n'
    assert not os.access(s.path / "schema.json", os.W_OK)


def test_same_content_same_snapshot(project, guard):
    a, b = make(project, guard), make(project, guard)
    assert a == b
    assert [p.name for p in (project / "workspace/config-snapshots").iterdir()] == [a.hash]


def test_changes_make_new_snapshot_and_keep_old(project, guard):
    old = make(project, guard)
    (project / "config/sets/shop/aliases.json").write_text('{"x": 1}\n')
    new = make(project, guard)
    assert new.hash != old.hash
    assert snap.load_snapshot(project / "workspace", old.hash, guard).files == old.files
    (project / "config/sets/shop/extra.json").write_text("{}\n")
    assert make(project, guard).hash not in {old.hash, new.hash}
    (project / "schema.json").write_text('{"tables": {"a": 1}}\n')
    assert len({old.hash, new.hash, make(project, guard).hash}) == 3


def test_hash_includes_paths():
    assert snap.snapshot_hash([("a.json", b"x")]) != snap.snapshot_hash([("b.json", b"x")])
    assert snap.snapshot_hash([("a", b"x"), ("b", b"y")]) == snap.snapshot_hash([("b", b"y"), ("a", b"x")])


def test_tampered_snapshot_rejected(project, guard):
    """음성 사례: 저장된 스냅샷을 바꾸면 읽기·재사용 모두 거부한다."""
    s = make(project, guard)
    target = s.path / "schema.json"
    os.chmod(target, 0o644)
    target.write_text("{}\n")
    with pytest.raises(snap.SnapshotIntegrityError):
        snap.load_snapshot(project / "workspace", s.hash, guard)
    with pytest.raises(snap.SnapshotIntegrityError):
        make(project, guard)


def test_added_file_in_snapshot_rejected(project, guard):
    s = make(project, guard)
    (s.path / "evil.json").write_text("{}")
    with pytest.raises(snap.SnapshotIntegrityError):
        snap.load_snapshot(project / "workspace", s.hash, guard)


@pytest.mark.parametrize("name", ["", "../shop", "shop/..", "Shop", "a/b", ".hidden", "x" * 70])
def test_bad_set_names(project, guard, name):
    with pytest.raises(snap.SnapshotError):
        make(project, guard, name)


@pytest.mark.parametrize("digest", ["", "../x", "abc", "0" * 63, "G" * 64])
def test_bad_snapshot_names(project, guard, digest):
    with pytest.raises(snap.SnapshotError):
        snap.load_snapshot(project / "workspace", digest, guard)


def test_missing_snapshot(project, guard):
    with pytest.raises(snap.SnapshotError):
        snap.load_snapshot(project / "workspace", "0" * 64, guard)


def test_links_and_missing_files_rejected(project, guard, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    link = project / "config/sets/shop/link.json"
    link.symlink_to(outside)
    with pytest.raises(snap.SnapshotError, match="링크"):
        make(project, guard)
    link.unlink()
    (project / "config/normalization-policy.json").unlink()
    with pytest.raises(snap.SnapshotError, match="필수 파일"):
        make(project, guard)


def test_empty_or_missing_set(project, guard):
    (project / "config/sets/empty").mkdir()
    with pytest.raises(snap.SnapshotError, match="비어"):
        make(project, guard, "empty")
    with pytest.raises(snap.SnapshotError, match="없다"):
        make(project, guard, "inventory")


def test_failed_write_leaves_nothing(project, guard, monkeypatch):
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(snap.os, "rename", boom)
    with pytest.raises(snap.SnapshotError, match="disk full"):
        make(project, guard)
    assert list((project / "workspace/config-snapshots").iterdir()) == []


def test_workspace_outside_guard_rejected(project, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    guard = Guard([other], project_root=project)
    with pytest.raises(PermissionError):
        make(project, guard)


def test_real_shop_set(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    guard = Guard([ws], project_root=ROOT)
    a = snap.create_snapshot(ROOT, "shop", ws, guard)
    assert set(a.files) == {"config/normalization-policy.json", "config/sets/shop/aliases.json",
                            "config/sets/shop/transforms.json",
                            "config/sets/shop/legacy-header-map.json",
                            "config/sets/shop/logical-diagnostics.json", "schema.json"}
    assert snap.create_snapshot(ROOT, "shop", ws, guard).hash == a.hash
