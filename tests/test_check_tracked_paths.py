"""M0-6 Git 경로 검사 (docs/development-plan.md 7절)."""

import subprocess
import sys
from pathlib import Path

import pytest

from tools import check_tracked_paths as ctp

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "check_tracked_paths.py"


@pytest.mark.parametrize(
    "path",
    [
        "evaluation_private/evaluation/cases.jsonl",
        "Evaluation_Private/x",
        "deliverables/evaluation-answers.zip",
        "deliverables/service-inputs.zip",
        "data/clean/orders.csv",
        "data/dirty/evaluation/orders.csv",
        "workspace/app.db",
        "reports/dirty-validation.json",
        "matrices.json",
        "scripts/matrices.json",
        ".venv/bin/python",
    ],
)
def test_blocked(path):
    assert ctp.violation(path, "100644")


def test_symlink_blocked():
    assert ctp.violation("scripts/node_modules", "120000")


@pytest.mark.parametrize(
    "path",
    ["data/dirty/development/orders.csv", "data/manifest.json", "docs/benchmark.md", "evaluation/__init__.py",
     "prep/guard.py", "tests/fixtures/cli-baseline/baseline.json", "data/cleaning-notes.md"],
)
def test_allowed(path):
    assert ctp.violation(path, "100644") is None


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _run(repo, mode):
    return subprocess.run([sys.executable, str(TOOL), mode], cwd=repo, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "user.email", "t@example.com")
    (tmp_path / "ok.txt").write_text("x")
    _git(tmp_path, "add", "ok.txt")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_staged_allows_normal_files(repo):
    (repo / "a.txt").write_text("a")
    _git(repo, "add", "a.txt")
    assert _run(repo, "--staged").returncode == 0


def test_staged_blocks_answer_file_and_rename(repo):
    """음성 사례: 새 파일과 이름 변경 모두 막는다."""
    (repo / "evaluation_private").mkdir()
    (repo / "evaluation_private" / "c.jsonl").write_text("x")
    _git(repo, "add", "-f", "evaluation_private/c.jsonl")
    r = _run(repo, "--staged")
    assert r.returncode == 1 and "evaluation_private/c.jsonl" in r.stderr
    _git(repo, "reset", "-q")
    (repo / "data" / "clean").mkdir(parents=True)
    _git(repo, "mv", "ok.txt", "data/clean/ok.txt")
    assert _run(repo, "--staged").returncode == 1


def test_staged_blocks_symlink(repo):
    (repo / "link").symlink_to(repo / "ok.txt")
    _git(repo, "add", "link")
    assert _run(repo, "--staged").returncode == 1


def test_staged_allows_removing_blocked_path(repo):
    (repo / "reports").mkdir()
    (repo / "reports" / "r.json").write_text("{}")
    _git(repo, "add", "-f", "reports/r.json")
    _git(repo, "commit", "-q", "--no-verify", "-m", "mistake")
    _git(repo, "rm", "-q", "--cached", "reports/r.json")
    assert _run(repo, "--staged").returncode == 0
    assert _run(repo, "--tracked").returncode == 0


def test_tracked_reports_existing_violation(repo):
    (repo / "workspace").mkdir()
    (repo / "workspace" / "app.db").write_text("")
    _git(repo, "add", "-f", "workspace/app.db")
    _git(repo, "commit", "-q", "--no-verify", "-m", "mistake")
    r = _run(repo, "--tracked")
    assert r.returncode == 1 and "workspace/app.db" in r.stderr


def test_this_repository_is_clean():
    assert _run(ROOT, "--tracked").returncode == 0


def test_bad_arguments():
    assert _run(ROOT, "--all").returncode == 2
