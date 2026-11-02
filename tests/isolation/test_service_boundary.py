"""T-11 서비스 경계 (docs/technical-design.md 11-1절 4·5항).

- 서비스 패키지는 evaluation을 import하지 않는다(AST 검사).
- 서비스 코드에 평가 자료 경로 문자열이 없다(보조 검사, 격리의 증명 아님).
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE_PACKAGES = ["prep", "api", "diagnostics", "ai"]
# 평가 자료 경로 이름을 가질 수 있는 서비스 파일(TD 11-1 5항: guard·설정 스냅샷 생성기)
STRING_EXEMPT = {"prep/guard.py"}
FORBIDDEN_STRINGS = ["evaluation_private", "data/clean", "deliverables", "data/dirty/evaluation", "matrices.json"]


def service_files():
    for pkg in SERVICE_PACKAGES:
        base = ROOT / pkg
        if base.is_dir():
            yield from sorted(base.rglob("*.py"))


def evaluation_imports(source: str, module: str) -> list[str]:
    """source에서 evaluation 패키지를 가져오는 구문을 찾는다. module은 'prep.x' 같은 점 표기."""
    found = []
    tree = ast.parse(source)
    package_parts = module.split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] == "evaluation"]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - node.level + 1]
                full = ".".join(base + ([node.module] if node.module else []))
                if full.split(".")[0] == "evaluation" or (not full and any(a.name == "evaluation" for a in node.names)):
                    found.append("." * node.level + (node.module or ""))
            elif node.module and node.module.split(".")[0] == "evaluation":
                found.append(node.module)
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            if name in {"import_module", "__import__"} and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.split(".")[0] == "evaluation":
                    found.append(arg.value)
    return found


def module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def test_service_files_found():
    assert any(p.name == "guard.py" for p in service_files())


@pytest.mark.parametrize("path", list(service_files()), ids=lambda p: str(p.relative_to(ROOT)))
def test_service_does_not_import_evaluation(path):
    assert evaluation_imports(path.read_text(encoding="utf-8"), module_name(path)) == []


@pytest.mark.parametrize(
    "path",
    [p for p in service_files() if str(p.relative_to(ROOT)) not in STRING_EXEMPT],
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_service_has_no_answer_paths(path):
    text = path.read_text(encoding="utf-8")
    assert [s for s in FORBIDDEN_STRINGS if s in text] == []


@pytest.mark.parametrize(
    "source",
    [
        "import evaluation",
        "import evaluation.score as s",
        "from evaluation import score",
        "from evaluation.score import run",
        "from .. import evaluation",
        "from ..evaluation import score",
        "import importlib\nimportlib.import_module('evaluation.score')",
        "__import__('evaluation')",
    ],
)
def test_checker_detects_evaluation_imports(source):
    """음성 사례: 검사기가 여러 import 형태를 잡는다."""
    assert evaluation_imports(source, "prep.runs") != []


@pytest.mark.parametrize("source", ["import prep.guard", "from . import guard", "from diagnostics.engine import diagnose"])
def test_checker_allows_other_imports(source):
    assert evaluation_imports(source, "prep.runs") == []
