"""개발셋 일괄 실행·채점 (M2-4, 개발 계획 부록 M2).

    uv run python -m tools.score_dev

개발 입력 13개를 서비스 흐름 그대로(업로드 → 자동 매핑 확정 → 진단 → 전체 승인 → 실행) 처리하고,
평가용으로 내보낸 뒤 채점한다. 서비스는 정답을 읽지 않으며, 채점만 개발 정답을 읽는다(8절 1).
사람이 고르는 단계는 '자동 제안을 그대로 확정, 미리 선택된 묶음 그대로 승인'으로 고정한다.
점수표는 현재 구현 범위의 중간 결과다. 사례별 상세는 workspace/scores/(Git 제외)에만 남는다.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from evaluation import score as scorer
from prep.export import export_run
from prep.preview import PROJECT_ROOT, FlowError, Workspace

DEV_INPUTS = PROJECT_ROOT / "data" / "dirty" / "development"


def run_service(ws: Workspace, inputs: Path, source: str = "개발셋") -> str:
    files = [(p.name, p.read_bytes()) for p in sorted(inputs.glob("*.csv"))]
    run = ws.create_run("shop", files, source)
    rid = run["id"]
    ws.confirm_mapping(rid, [{"item_id": f["item_id"], **f["mapping"]}
                             for f in ws.load(rid)["files"] if not f["read_error"]])
    ws.diagnose(rid)
    preview = ws.changes_preview(rid)
    result = ws.execute(rid, preview["approval"])
    print(f"실행 {rid}: {result['status']}", file=sys.stderr)
    return rid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="개발셋을 서비스로 처리하고 채점한다")
    parser.add_argument("--workspace", type=Path,
                        default=Path(os.environ.get("DPA_WORKSPACE", PROJECT_ROOT / "workspace")))
    parser.add_argument("--run", help="이미 끝난 실행을 내보내 채점만 한다")
    parser.add_argument("--answer-sums", type=Path)
    args = parser.parse_args(argv)
    ws = Workspace(args.workspace)
    try:
        rid = args.run or run_service(ws, DEV_INPUTS)
        folder = export_run(ws, rid)
    except FlowError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    return scorer.main([str(folder), "--split", "development"]
                       + (["--answer-sums", str(args.answer_sums)] if args.answer_sums else []))


if __name__ == "__main__":
    raise SystemExit(main())
