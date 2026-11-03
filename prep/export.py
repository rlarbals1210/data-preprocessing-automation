"""평가용 내보내기 (docs/quality-evaluation-spec.md 5-1절, FS F14, M2-1).

끝난 실행(완료·완료(문제 남음)·실패) 하나의 산출물을 채점기가 읽을 고정 형식으로
workspace/exports/<실행>/export-<n>/ 에 쓴다. 서비스 쪽 코드이므로 정답·정상 기준 데이터는 읽지 않는다.
채점기(evaluation/)는 이 폴더만 읽고 서비스 내부(DB·run.json)에는 접근하지 않는다.

내보내는 것 (export-v1)
- export.json   형식·실행 상태·설정/뷰/계획 해시·결과 버전·시도·감사 요약·파일 해시
- inputs.json   업로드 항목(입력 등록표 초안): ID·이름·SHA-256·행 수·헤더·읽기 오류
- mapping.json  항목별 자동 제안(사람 확인 전 동결본)과 최종 매핑
- lineage.json  표준화 뷰 역추적 표(뷰 레코드 → 업로드 항목·원래 레코드, 뷰 컬럼 → 원래 이름)
- diagnosis.json 정제 전 진단(뷰 좌표 + 원본 위치)
- apply-plan.json 적용 계획(허용·보류 셀)
- attempt/      마지막 시도의 감사·변경 내역·보류·승인 안 함·정제 후 진단(엔진이 썼다면)
- result/       발행된 결과 버전의 파일(발행된 경우만, 해시 재대조 후 복사)

결과 CSV의 k번째 데이터 행은 뷰 레코드 k다(엔진은 행 순서를 바꾸지 않고, 감사 대조(M1-10b)가
행 수·전체 셀을 확인한다).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from prep import publish
from prep.preview import FINISHED, FlowError, Workspace

FORMAT = "export-v1"
ATTEMPT_FILES = ("changes.json", "held.json", "not-approved.json", "after.json")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def export_run(ws: Workspace, run_id: str) -> Path:
    run = ws.load(run_id)
    if run["status"] not in FINISHED:
        raise FlowError("not_finished", "끝난 실행만 내보낼 수 있다", 409)
    base = ws.run_dir(run_id)
    parent = ws.guard.safe_join(ws.root, "exports", run_id)
    parent.mkdir(parents=True, exist_ok=True)
    number = 1 + max((int(p.name.split("-")[1]) for p in parent.iterdir()
                      if p.name.startswith("export-") and p.name.split("-")[1].isdigit()), default=0)
    target = ws.guard.safe_join(parent, f"export-{number}")
    stage = ws.guard.safe_join(parent, f".staging-{number}")
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir()
    files: dict[str, str] = {}

    def put(rel: str, data: bytes) -> None:
        path = ws.guard.safe_join(stage, *rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[rel] = _sha(data)

    try:
        put("inputs.json", _dump([{k: f[k] for k in ("item_id", "name", "sha256", "rows", "headers", "read_error",
                                                       "read_options")} for f in run["files"]]))
        put("mapping.json", _dump([_mapping(f) for f in run["files"]]))
        for name in ("lineage.json",):
            src = base / "view" / name
            if src.is_file():
                put(name, src.read_bytes())
        for name, out in (("report.json", "diagnosis.json"), ("apply-plan.json", "apply-plan.json")):
            src = base / name
            if src.is_file():
                put(out, src.read_bytes())
        attempt = ws.store.conn.execute(
            "SELECT * FROM attempt WHERE run_id = ? ORDER BY number DESC LIMIT 1", (run["db_run"],)).fetchone()
        attempt_meta = None
        if attempt is not None:
            folder = ws.guard.safe_join(ws.root, attempt["path"])
            attempt_meta = {"number": attempt["number"], "outcome": attempt["outcome"], "error": attempt["error"],
                            "audit": None if attempt["audit"] is None else json.loads(attempt["audit"])}
            for name in ATTEMPT_FILES:
                src = folder / "engine" / name
                if src.is_file():
                    put(f"attempt/{name}", src.read_bytes())
        version = publish.latest(ws.store, run["db_run"])
        version_meta = None
        if version is not None:
            hashes = json.loads(version["data_hashes"])
            for name in sorted(hashes):
                put(f"result/{name}", publish.read_verified(ws.guard, ws.root, version, name))  # 해시 재대조
            manifest = json.loads(ws.guard.safe_join(ws.root, version["manifest_path"]).read_text(encoding="utf-8"))
            version_meta = {"id": version["id"], "kind": version["kind"], "attempt": manifest["attempt"],
                            "excluded_tables": manifest["excluded_tables"]}
        meta = {
            "format": FORMAT,
            "run_id": run_id,
            "retry_of": run.get("retry_of"),
            "set": run["set"],
            "status": run["status"],
            "config_hash": run["config_hash"],
            "view_hash": run.get("view_hash"),
            "plan_hash": (run.get("plan") or {}).get("hash"),
            "plan_tables": (run.get("plan") or {}).get("tables", {}),
            "published": version_meta is not None,
            "result_version": version_meta,
            "attempt": attempt_meta,
            "files": dict(sorted(files.items())),
        }
        (stage / "export.json").write_bytes(_dump(meta))
        for p in stage.rglob("*"):
            if p.is_file():
                os.chmod(p, 0o444)
        os.rename(stage, target)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return target


def _mapping(f: dict) -> dict:
    proposal = f.get("proposal")
    auto = None
    if proposal is not None:
        auto = {"table": proposal["table"]["table"],
                "table_auto": proposal["table"]["auto"],
                "columns": {c["original"]: {"logical": c["logical"], "grade": c["grade"],
                                            "needs_confirmation": c["needs_confirmation"]}
                            for c in proposal["columns"]}}
    return {"item_id": f["item_id"], "name": f["name"], "proposal": auto, "final": f.get("mapping"),
            "confirmed": f.get("confirmed", False)}


def main(argv: list[str] | None = None) -> int:
    import argparse

    from prep.preview import PROJECT_ROOT

    parser = argparse.ArgumentParser(description="끝난 실행을 채점용 폴더로 내보낸다")
    parser.add_argument("run_id")
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ.get("DPA_WORKSPACE", PROJECT_ROOT / "workspace")))
    args = parser.parse_args(argv)
    try:
        print(export_run(Workspace(args.workspace), args.run_id))
    except FlowError as exc:
        print(f"{exc.code}: {exc.message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
