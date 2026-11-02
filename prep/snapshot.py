"""설정 스냅샷 (docs/technical-design.md 4-2·11-1절, M1-3a).

실행이 기대는 파일(설정 세트 폴더 + schema.json + config/normalization-policy.json)을
workspace/config-snapshots/<해시>/에 복사한다. 해시는 (상대 경로, 내용 SHA-256)을 경로순으로 정렬해 계산한다.
실행은 디스크 원본이 아니라 스냅샷을 쓴다. 프로젝트 설정을 읽는 곳은 이 모듈뿐이며 읽을 목록은 아래에 고정돼 있다.
스냅샷은 만든 뒤 바꾸지 않는다. 쓰기 전과 읽기 전에 내용을 다시 해시해 확인한다.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from prep.guard import Guard

SETS_DIR = "config/sets"
FIXED_FILES = ("schema.json", "config/normalization-policy.json")
SNAPSHOT_DIR = "config-snapshots"
SET_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
HASH_NAME = re.compile(r"[0-9a-f]{64}")


class SnapshotError(ValueError):
    """설정 세트를 스냅샷으로 만들 수 없다."""


class SnapshotIntegrityError(SnapshotError):
    """저장된 스냅샷 내용이 이름(해시)과 다르다."""


@dataclass(frozen=True)
class Snapshot:
    hash: str
    path: Path
    files: tuple[str, ...]


def collect(project_root: Path, set_name: str) -> list[tuple[str, bytes]]:
    """고정 목록의 (프로젝트 기준 상대 경로, 내용). 링크·특수 파일은 거부한다."""
    if not SET_NAME.fullmatch(set_name or ""):
        raise SnapshotError(f"설정 세트 이름이 올바르지 않다: {set_name!r}")
    root = Path(project_root).resolve()
    set_dir = root / SETS_DIR / set_name
    if set_dir.is_symlink() or not set_dir.is_dir():
        raise SnapshotError(f"설정 세트 폴더가 없다: {SETS_DIR}/{set_name}")
    paths = []
    for p in sorted(set_dir.rglob("*")):
        if p.is_symlink():
            raise SnapshotError(f"설정 세트 안의 링크는 허용하지 않는다: {p.relative_to(root)}")
        if p.is_file():
            paths.append(p)
        elif not p.is_dir():
            raise SnapshotError(f"일반 파일이 아니다: {p.relative_to(root)}")
    if not paths:
        raise SnapshotError(f"설정 세트가 비어 있다: {SETS_DIR}/{set_name}")
    for rel in FIXED_FILES:
        p = root / rel
        if p.is_symlink() or not p.is_file():
            raise SnapshotError(f"필수 파일이 없다: {rel}")
        paths.append(p)
    entries = [(p.relative_to(root).as_posix(), p.read_bytes()) for p in paths]
    return sorted(entries)


def snapshot_hash(entries: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for rel, data in sorted(entries):
        h.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def _read_tree(folder: Path) -> list[tuple[str, bytes]]:
    entries = []
    for p in sorted(folder.rglob("*")):
        if p.is_symlink() or (not p.is_file() and not p.is_dir()):
            raise SnapshotIntegrityError(f"스냅샷 안에 일반 파일이 아닌 항목: {p.name}")
        if p.is_file():
            entries.append((p.relative_to(folder).as_posix(), p.read_bytes()))
    return entries


def create_snapshot(project_root: Path, set_name: str, workspace: Path, guard: Guard) -> Snapshot:
    """스냅샷을 만든다. 같은 내용이 이미 있으면 검증 후 그대로 돌려준다."""
    entries = collect(project_root, set_name)
    digest = snapshot_hash(entries)
    base = guard.safe_join(workspace, SNAPSHOT_DIR)
    dest = guard.safe_join(workspace, SNAPSHOT_DIR, digest)
    if dest.exists():
        return load_snapshot(workspace, digest, guard)
    base.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=base))
    try:
        for rel, data in entries:
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            os.chmod(target, 0o444)
        if snapshot_hash(_read_tree(tmp)) != digest:
            raise SnapshotIntegrityError("복사한 스냅샷의 해시가 다르다")
        os.rename(tmp, dest)
    except FileExistsError:
        pass  # 다른 실행이 같은 스냅샷을 먼저 만들었다 → 아래에서 검증
    except OSError as exc:
        if not dest.exists():
            raise SnapshotError(f"스냅샷을 쓰지 못했다: {exc}") from exc
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    return load_snapshot(workspace, digest, guard)


def load_snapshot(workspace: Path, digest: str, guard: Guard) -> Snapshot:
    """저장된 스냅샷을 내용 확인 후 돌려준다. 실행은 이 경로 아래 파일만 쓴다."""
    if not HASH_NAME.fullmatch(digest or ""):
        raise SnapshotError(f"스냅샷 이름이 올바르지 않다: {digest!r}")
    path = guard.safe_join(workspace, SNAPSHOT_DIR, digest)
    if not path.is_dir():
        raise SnapshotError(f"스냅샷이 없다: {digest}")
    entries = _read_tree(path)
    if snapshot_hash(entries) != digest:
        raise SnapshotIntegrityError(f"스냅샷 내용이 해시와 다르다: {digest}")
    return Snapshot(hash=digest, path=path, files=tuple(rel for rel, _ in entries))
