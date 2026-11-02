#!/usr/bin/env python3
"""Git 경로 검사 — 평가 자료·실행 데이터·로컬 링크가 커밋되지 않게 막는다.

docs/development-plan.md 7절. 파일 이름과 Git 모드만 보고, 파일 내용은 읽지 않는다.
  --staged   커밋 직전 index 변경(추가·수정·이름 변경). 차단 경로를 지우는 변경은 허용.
  --tracked  현재 추적 중인 전체 파일(CI용).
macOS의 Python 3.9에서도 돌도록 3.9 문법만 쓴다(pre-commit 훅이 /usr/bin/python3로 실행).
"""
import subprocess
import sys

BLOCKED_PREFIXES = (
    "evaluation_private/",
    "deliverables/evaluation-answers.zip",
    "deliverables/service-inputs.zip",
    "data/clean/",
    "data/dirty/evaluation/",
    "workspace/",
    "reports/",
    ".venv/",
)
BLOCKED_NAMES = ("matrices.json",)
SYMLINK_MODE = "120000"


def violation(path, mode):
    """차단 사유를 돌려준다. 문제없으면 None. 대소문자를 구분하지 않는 macOS를 고려해 casefold로 비교."""
    p = path.casefold()
    for prefix in BLOCKED_PREFIXES:
        if p == prefix.rstrip("/") or p.startswith(prefix):
            return "차단 경로 " + prefix
    if p.rsplit("/", 1)[-1] in BLOCKED_NAMES:
        return "차단 파일 이름 " + p.rsplit("/", 1)[-1]
    if mode == SYMLINK_MODE:
        return "심볼릭 링크"
    return None


def _git(*args):
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def staged_entries():
    """(경로, 새 모드) — 삭제는 제외. 이름 변경은 새 경로로 검사."""
    out = _git("diff", "--cached", "--raw", "-z", "--no-renames", "--no-abbrev")
    fields = out.split(b"\0")
    entries = []
    i = 0
    while i < len(fields) - 1:
        meta = fields[i].decode()
        path = fields[i + 1].decode("utf-8", "surrogateescape")
        i += 2
        _, new_mode, _, _, status = meta.lstrip(":").split(" ")
        if status != "D":
            entries.append((path, new_mode))
    return entries


def tracked_entries():
    out = _git("ls-files", "-s", "-z")
    entries = []
    for rec in out.split(b"\0"):
        if rec:
            meta, path = rec.split(b"\t", 1)
            entries.append((path.decode("utf-8", "surrogateescape"), meta.decode().split(" ")[0]))
    return entries


def main(argv):
    if argv[1:] == ["--staged"]:
        entries = staged_entries()
    elif argv[1:] == ["--tracked"]:
        entries = tracked_entries()
    else:
        print("사용법: check_tracked_paths.py --staged | --tracked", file=sys.stderr)
        return 2
    bad = [(p, r) for p, r in ((p, violation(p, m)) for p, m in entries) if r]
    for path, reason in bad:
        print(f"커밋 금지: {path} ({reason})", file=sys.stderr)
    if bad:
        print("docs/development-plan.md 7절을 확인하세요.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
