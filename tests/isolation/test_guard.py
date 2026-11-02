"""T-11 허용 경로 guard (docs/technical-design.md 11-1절). 실제 평가 자료는 쓰지 않고 임시 폴더로 흉내 낸다."""

import os

import pytest

from prep.guard import Guard, PathNotAllowed


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "workspace" / "runs").mkdir(parents=True)
    (root / "evaluation_private" / "evaluation").mkdir(parents=True)
    (root / "evaluation_private" / "evaluation" / "cases.jsonl").write_text("secret\n")
    (root / "data" / "clean").mkdir(parents=True)
    upload = tmp_path / "upload-tmp"
    upload.mkdir()
    return root, upload


@pytest.fixture
def guard(project):
    root, upload = project
    return Guard([root / "workspace", upload], project_root=root)


def test_allows_files_inside_roots(project, guard):
    root, upload = project
    f = root / "workspace" / "runs" / "a.csv"
    f.write_text("x")
    assert guard.check(f) == f.resolve()
    assert guard.check(upload / "new.csv") == (upload / "new.csv").resolve()  # 아직 없는 쓰기 대상
    with guard.open(f) as fh:
        assert fh.read() == "x"


def test_allows_symlink_staying_inside(project, guard):
    root, _ = project
    target = root / "workspace" / "runs" / "a.csv"
    target.write_text("x")
    link = root / "workspace" / "link.csv"
    link.symlink_to(target)
    assert guard.check(link) == target.resolve()


def test_rejects_symlink_to_answers(project, guard):
    root, _ = project
    link = root / "workspace" / "answers"
    link.symlink_to(root / "evaluation_private")
    with pytest.raises(PathNotAllowed):
        guard.check(link / "evaluation" / "cases.jsonl")
    with pytest.raises(PathNotAllowed):
        guard.open(link / "evaluation" / "cases.jsonl")


@pytest.mark.parametrize(
    "rel",
    ["../evaluation_private/evaluation/cases.jsonl", "runs/../../data/clean", "../../project/evaluation_private"],
)
def test_rejects_dotdot_escape(project, guard, rel):
    root, _ = project
    with pytest.raises(PathNotAllowed):
        guard.check(os.path.join(root / "workspace", rel))


def test_rejects_absolute_outside(guard):
    with pytest.raises(PathNotAllowed):
        guard.check("/etc/passwd")


@pytest.mark.parametrize(
    "name",
    ["../x.csv", "a/../../x.csv", "/etc/passwd", "..", ".", "", "a\\..\\x", "a\0b", "a//b", "a/./b"],
)
def test_safe_join_rejects_upload_name_tricks(project, guard, name):
    _, upload = project
    with pytest.raises(PathNotAllowed):
        guard.safe_join(upload, name)


def test_safe_join_allows_plain_names(project, guard):
    _, upload = project
    assert guard.safe_join(upload, "batch-1", "orders.csv") == (upload / "batch-1" / "orders.csv").resolve()


def test_safe_join_rejects_symlink_escape(project, guard):
    root, upload = project
    (upload / "evil").symlink_to(root / "evaluation_private")
    with pytest.raises(PathNotAllowed):
        guard.safe_join(upload, "evil", "evaluation", "cases.jsonl")


def test_safe_join_base_must_be_allowed(project, guard):
    root, _ = project
    with pytest.raises(PathNotAllowed):
        guard.safe_join(root / "evaluation_private", "evaluation")


def test_rejects_nul(guard, project):
    root, _ = project
    with pytest.raises(PathNotAllowed):
        guard.check(f"{root}/workspace/a\0.csv")


@pytest.mark.parametrize("bad", ["", ".", "evaluation_private", "data", "data/clean", "deliverables"])
def test_roots_overlapping_project_or_answers_rejected(project, bad):
    root, _ = project
    (root / "deliverables").mkdir(exist_ok=True)
    with pytest.raises(ValueError):
        Guard([root / bad], project_root=root)


def test_root_above_project_rejected(project):
    root, _ = project
    with pytest.raises(ValueError):
        Guard([root.parent], project_root=root)


def test_requires_existing_roots(project):
    root, _ = project
    with pytest.raises(ValueError):
        Guard([], project_root=root)
    with pytest.raises(ValueError):
        Guard([root / "workspace" / "missing"], project_root=root)
