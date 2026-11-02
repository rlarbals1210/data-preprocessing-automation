"""허용 경로 guard (docs/technical-design.md 11-1절).

서비스가 여는 모든 경로는 심볼릭 링크·`..`를 해석한 실제 경로가 허용 루트
(workspace/ 또는 업로드 임시 폴더) 안이어야 한다. 금지 목록이 아니라 허용 목록 방식이다.

보장 범위: 경로 해석 기준의 차단이다. 확인과 열기 사이에 링크를 바꾸는 경쟁 조건,
하드 링크, 사용자가 정답 파일 이름을 바꿔 직접 업로드하는 경우는 판별하지 않는다(TD 11-1).

이 모듈과 설정 스냅샷 생성기만 평가 자료 경로 이름을 코드에 가질 수 있다(TD 11-1 5항).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

# 허용 루트가 이 경로들을 품거나 그 안에 있으면 안 된다(설정 실수 방지).
FORBIDDEN_RELATIVE = (
    "evaluation_private",
    "deliverables",
    "data/clean",
    "data/dirty/evaluation",
    "matrices.json",
)


class PathNotAllowed(PermissionError):
    """허용 루트 밖의 경로."""


class Guard:
    def __init__(self, roots: Iterable[str | os.PathLike[str]], project_root: str | os.PathLike[str]):
        self.project_root = Path(project_root).resolve()
        resolved = []
        for root in roots:
            r = Path(root).resolve()
            if not r.is_dir():
                raise ValueError(f"허용 루트가 폴더가 아니다: {r}")
            if r == self.project_root or r in self.project_root.parents:
                raise ValueError(f"프로젝트 루트나 그 상위는 허용 루트가 될 수 없다: {r}")
            for rel in FORBIDDEN_RELATIVE:
                forbidden = self.project_root / rel
                if forbidden.is_relative_to(r) or r.is_relative_to(forbidden):
                    raise ValueError(f"허용 루트가 평가 자료 경로와 겹친다: {r}")
            resolved.append(r)
        if not resolved:
            raise ValueError("허용 루트가 하나 이상 필요하다")
        self.roots = tuple(resolved)

    def check(self, path: str | os.PathLike[str]) -> Path:
        """실제 경로를 돌려준다. 허용 루트 밖이면 PathNotAllowed."""
        raw = os.fspath(path)
        if "\0" in raw:
            raise PathNotAllowed("경로에 NUL 문자가 있다")
        real = Path(raw).resolve()
        if not any(real == r or real.is_relative_to(r) for r in self.roots):
            raise PathNotAllowed(f"허용 루트 밖의 경로: {raw}")
        return real

    def safe_join(self, root: str | os.PathLike[str], *parts: str) -> Path:
        """허용 루트 아래에 사용자 입력 조각을 붙인다. 절대 경로·`..`·구분자 우회는 거부."""
        base = self.check(root)
        for part in parts:
            if not part or "\0" in part or "\\" in part or Path(part).is_absolute():
                raise PathNotAllowed(f"허용하지 않는 경로 조각: {part!r}")
            if any(seg in ("", ".", "..") for seg in part.split("/")):
                raise PathNotAllowed(f"허용하지 않는 경로 조각: {part!r}")
        target = base.joinpath(*parts)
        real = self.check(target)
        if not real.is_relative_to(base):
            raise PathNotAllowed(f"기준 폴더 밖으로 나가는 경로: {target}")
        return real

    def open(self, path: str | os.PathLike[str], mode: str = "r", **kwargs):
        return open(self.check(path), mode, **kwargs)
