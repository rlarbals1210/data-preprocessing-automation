"""명령줄 회귀 T-01 — 기존 scripts/diagnose.py·clean.py의 결과를 기준 파일과 대조한다.

docs/development-plan.md 3절 M0-4:
- 개발 입력(data/dirty/development)과 재고 예제만 쓴다. 평가 자료는 쓰지 않는다.
- 시각·출력/저장소 경로는 정규화하고, 엔진 확장 ①이 추가하는 `rule_execution`은 비교에서 뺀다.
- 분류·후보·값·반환값·종료 코드의 의미는 그대로 보존돼야 한다.

기준 갱신(계약 변경이 승인된 경우에만):
    uv run python tests/cli_regression.py --update [--python <인터프리터>]
이 파일은 기준을 만든 Python 3.9에서도 돌도록 3.9 문법만 쓴다.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests/fixtures/cli-baseline/baseline.json"
DEV = "data/dirty/development"
SHOP = "config/shop-diagnostics.json"
INV_IN = "examples/inventory/input"
INV_CFG = "examples/inventory/rules.json"

# (이름, 스크립트, 인자) — 출력 폴더 인자는 실행 때 붙인다.
CASES = [
    ("diagnose-dev-profile", "diagnose", [DEV]),
    ("diagnose-dev-shop", "diagnose", [DEV, "--config", SHOP]),
    ("diagnose-inventory", "diagnose", [INV_IN, "--config", INV_CFG]),
    ("clean-dev-shop", "clean", [DEV, "--config", SHOP]),
    ("clean-inventory", "clean", [INV_IN, "--config", INV_CFG]),
    ("diagnose-missing-config", "diagnose", [DEV, "--config", "config/__missing__.json"]),
    ("clean-existing-output", "clean", [INV_IN, "--config", INV_CFG]),
]

VOLATILE_KEYS = {"checked_at", "started_at", "completed_at"}  # 실행 시각
ALLOWED_NEW_KEYS = {"rule_execution"}  # 엔진 확장 ①로 추가되는 필드(TD 1절)


def _replace_paths(text, out):
    return text.replace(str(out), "<OUT>").replace(str(ROOT), "<ROOT>")


def normalize_json(value, out):
    if isinstance(value, dict):
        return {k: normalize_json(v, out) for k, v in value.items()
                if k not in VOLATILE_KEYS and k not in ALLOWED_NEW_KEYS}
    if isinstance(value, list):
        return [normalize_json(v, out) for v in value]
    if isinstance(value, str):
        return _replace_paths(value, out)
    return value


def digest_file(path, out):
    if path.suffix == ".json":
        data = normalize_json(json.loads(path.read_text(encoding="utf-8")), out)
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    else:
        raw = _replace_paths(path.read_bytes().decode("utf-8"), out).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _tree_hash(folder):
    h = hashlib.sha256()
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(folder)).encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def _key_summary(out):
    """실패 시 읽기 쉬운 요약(비교에도 포함)."""
    summary = {}
    report = out / "report.json"
    if report.exists():
        r = json.loads(report.read_text(encoding="utf-8"))
        summary.update(status=r["status"], rows_read=r["summary"]["rows_read"],
                       category_counts=r["summary"]["category_counts"])
    manifest = out / "manifest.json"
    if manifest.exists():
        m = json.loads(manifest.read_text(encoding="utf-8"))
        summary.update(status=m["status"], rows=m["rows"], changed_cells=m["changed_cells"],
                       after_categories=m["after_categories"])
    return summary


def run_case(name, script, args, python, work):
    out = work / name
    if name == "clean-existing-output":
        out.mkdir()  # 기존 출력 폴더는 거부돼야 한다
    inputs = ROOT / args[0]
    before = _tree_hash(inputs)
    proc = subprocess.run([python, str(ROOT / "scripts" / (script + ".py")), *args, "--output", str(out)],
                          cwd=str(ROOT), capture_output=True, text=True)
    files = {}
    if out.is_dir():
        for p in sorted(out.rglob("*")):
            if p.is_file():
                files[str(p.relative_to(out))] = digest_file(p, out)
    return {
        "exit_code": proc.returncode,
        "stdout": _replace_paths(proc.stdout, out).splitlines(),
        "stderr": _replace_paths(proc.stderr, out).splitlines(),
        "summary": _key_summary(out),
        "files": files,
        "source_unchanged": _tree_hash(inputs) == before,
    }


def run_all(python):
    with tempfile.TemporaryDirectory() as tmp:
        return {name: run_case(name, script, args, python, Path(tmp)) for name, script, args in CASES}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true", help="기준 파일을 다시 쓴다")
    parser.add_argument("--python", default=sys.executable, help="스크립트를 실행할 인터프리터")
    args = parser.parse_args()
    python_version = subprocess.run([args.python, "-c", "import platform;print(platform.python_version())"],
                                    capture_output=True, text=True, check=True).stdout.strip()
    result = run_all(args.python)
    if args.update:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"generated_with_python": python_version, "cases": result}
        BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("기준 갱신:", BASELINE.relative_to(ROOT), "Python", python_version)
        return 0
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["cases"]
    bad = [n for n in baseline if baseline[n] != result.get(n)]
    print("Python", python_version, "불일치:", bad or "없음")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
