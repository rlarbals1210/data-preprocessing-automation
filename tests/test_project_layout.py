"""M0-2 패키지 골격과 테스트 수집 범위 (docs/development-plan.md 3절)."""

import importlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pytest_config():
    with (ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)["tool"]["pytest"]["ini_options"]


def test_packages_importable():
    for name in ["diagnostics", "prep", "api", "evaluation"]:
        module = importlib.import_module(name)
        assert Path(module.__file__).resolve().parent == ROOT / name


def test_collection_limited_to_tests():
    assert pytest_config()["testpaths"] == ["tests"]


def test_dataset_build_validators_not_collected():
    """음성 사례: 평가 자료를 읽는 scripts/test_*.py는 수집 경로 밖에 있어야 한다."""
    roots = [(ROOT / p).resolve() for p in pytest_config()["testpaths"]]
    scripts = sorted((ROOT / "scripts").glob("test_*.py"))
    assert scripts, "구축 검증 스크립트가 있어야 이 검사가 의미 있다"
    for script in scripts:
        assert not any(script.resolve().is_relative_to(r) for r in roots), script.name
