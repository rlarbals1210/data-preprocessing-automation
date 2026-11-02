"""T-01 명령줄 회귀 (docs/technical-design.md 1절, docs/development-plan.md 3절 M0-4)."""

import json
from pathlib import Path

import pytest

from tests import cli_regression as cr

BASELINE = json.loads(cr.BASELINE.read_text(encoding="utf-8"))["cases"]


@pytest.fixture(scope="module")
def current(tmp_path_factory):
    import sys

    work = tmp_path_factory.mktemp("cli")
    return {name: cr.run_case(name, script, args, sys.executable, work) for name, script, args in cr.CASES}


@pytest.mark.parametrize("name", [c[0] for c in cr.CASES])
def test_cli_matches_baseline(current, name):
    assert current[name] == BASELINE[name]


def test_baseline_covers_success_and_failure_exit_codes():
    codes = {case["exit_code"] for case in BASELINE.values()}
    assert {1, 2} <= codes
    assert all(case["source_unchanged"] for case in BASELINE.values())


def test_baseline_uses_development_inputs_only():
    for _, _, args in cr.CASES:
        assert not any("evaluation" in a or "data/clean" in a for a in args)


def _digest(tmp_path, data):
    path = tmp_path / "x.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return cr.digest_file(path, tmp_path / "out")


def test_digest_ignores_allowed_additions_and_volatile_fields(tmp_path):
    base = {"status": "completed", "issues": [{"code": "a"}]}
    assert _digest(tmp_path, base) == _digest(tmp_path, {**base, "rule_execution": {"r": 1}})
    assert _digest(tmp_path, base) == _digest(tmp_path, {**base, "checked_at": "2026-11-02T00:00:00Z"})


def test_digest_detects_changes(tmp_path):
    """음성 사례: 값 변경과 허용 목록 밖의 새 필드는 기준과 달라야 한다."""
    base = {"status": "completed", "issues": [{"code": "a"}]}
    assert _digest(tmp_path, base) != _digest(tmp_path, {**base, "status": "incomplete"})
    assert _digest(tmp_path, base) != _digest(tmp_path, {**base, "new_field": 1})
    assert _digest(tmp_path, base) != _digest(tmp_path, {"status": "completed", "issues": [{"code": "b"}]})


def test_paths_are_normalized(tmp_path):
    out = tmp_path / "out"
    assert cr.normalize_json({"f": f"{out}/data/a.csv", "g": f"{cr.ROOT}/x"}, out) == {
        "f": "<OUT>/data/a.csv", "g": "<ROOT>/x"}
    assert Path(cr.ROOT / "scripts/diagnose.py").exists()
