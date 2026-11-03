"""채점기 (docs/technical-design.md 11-2절, docs/quality-evaluation-spec.md 5절, FS F14, M2).

    uv run python -m evaluation.score <내보내기 폴더> --split development

- 서비스 내보내기(prep/export.py)와 정답을 대조한다. 서비스는 이 모듈을 import하지 않는다.
- 행동별·pattern_group별로 따로 집계하고, 서로 다른 단위를 하나의 정확도로 합치지 않는다.
- 사례별 상세는 출력 폴더(기본 workspace/scores/, Git 제외)에만 쓴다.
- 평가 분할은 --confirm-final 없이는 실행하지 않는다(④ 7절).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from evaluation.answers import (
    AnswerError,
    Case,
    check_manifest,
    check_observed,
    check_semantics,
    check_sums,
    load_cases,
)
from evaluation.bundle import Bundle, BundleError, Evidence, load_bundle, read_csv
from evaluation.judge import (
    JUDGE_VERSION,
    KEEP,
    KEEP_WRONG,
    MAP_AUTO,
    MAP_FINAL,
    NORMALIZE,
    REVIEW,
    VIOLATION,
    Placer,
    ScoreError,
    judge_keep,
    judge_map,
    judge_normalize,
    judge_review,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC_VERSION = "quality-evaluation-spec v0.2"
ANSWER_FILES = ("cases.jsonl", "row-lineage.jsonl", "baseline.json")
SHARED_ANSWER_FILES = ("manifest.json", "input-manifest.json")
SMALL = 10  # 10건 미만은 비율 없이 건수만(④ 1절)
NOTICE = "합성 벤치마크 결과이며 실제 업무 효과가 아니다."


class IsolationError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def answer_hashes(answers_root: Path, split: str) -> dict[str, str]:
    paths = [answers_root / split / n for n in ANSWER_FILES] + [answers_root / n for n in SHARED_ANSWER_FILES]
    return {p.name: _sha(p.read_bytes()) for p in paths if p.is_file()}


def check_isolation(input_dir: Path, bundle: Bundle, answers: dict[str, str]) -> None:
    """입력 폴더·서비스 입력에 정답 파일이 섞였는지(AC-14-2). 이름 또는 내용 해시로 판정한다."""
    names = set(ANSWER_FILES) | set(SHARED_ANSWER_FILES)
    hashes = set(answers.values())
    for p in sorted(input_dir.iterdir()):
        if p.is_dir() or p.suffix.lower() != ".csv" or p.name in names or _sha(p.read_bytes()) in hashes:
            raise IsolationError(f"입력 폴더에 입력 CSV가 아닌 파일이 있다: {p.name}")
    for item in bundle.inputs:
        if item["name"] in names or item["sha256"] in hashes:
            raise IsolationError(f"서비스 입력에 정답 파일이 섞였다: {item['name']}")


def read_inputs(input_dir: Path, bundle: Bundle) -> dict[str, dict]:
    """채점 기준 입력(고정된 오염 입력). 서비스가 받은 바이트와 같은지 해시로 확인한다."""
    out = {}
    for p in sorted(input_dir.glob("*.csv")):
        data = p.read_bytes()
        options = {"encoding": "utf-8-sig", "delimiter": ","}
        for item in bundle.items_named(p.name):
            if item["sha256"] != _sha(data):
                raise BundleError(f"서비스가 받은 파일과 채점 입력이 다르다: {p.name}")
            options = item.get("read_options") or options
        headers, records = read_csv(data, options["encoding"], options["delimiter"])
        out[p.name] = {"headers": headers, "records": records}
    return out


def primary_keys(schema: dict) -> dict[str, list[str]]:
    return {t: [f["name"] for f in spec["fields"] if f.get("primary_key")] for t, spec in schema["tables"].items()}


def _rate(num: int, den: int) -> float | None:
    return None if den < SMALL else round(num / den, 4)


def score(bundle: Bundle, cases: list[Case], inputs: dict[str, dict], schema: dict,
          registry: dict[str, int] | None = None) -> tuple[dict, list[dict]]:
    placer = Placer(bundle, registry)
    ev = Evidence.of(bundle)
    pk = primary_keys(schema)
    details = []
    norm = defaultdict(Counter)
    maps = {"auto": Counter(), "final": Counter()}
    review = defaultdict(Counter)
    keep = defaultdict(Counter)
    zero = Counter()
    notes = set()
    for c in sorted(cases, key=lambda c: c.case_id):
        d = {"case_id": c.case_id, "action": c.action, "issue_type": c.issue_type, "pattern_group": c.pattern_group}
        if c.action == "map_column":
            d |= judge_map(c, bundle, placer)
            maps["auto"][d["auto"]] += 1
            maps["final"][d["final"]] += 1
        else:
            p = placer.place(c.input_file, c.record, None if c.is_row else c.column)
            d["place"] = {"table": p.table, "record": p.record, "column": p.column}
            if p.logical is not None and c.canonical_column and p.logical != c.canonical_column:
                d["mapped_elsewhere"] = p.logical
                zero["매핑이 정답 컬럼과 다른 사례"] += 1
            if c.action == "normalize":
                d |= judge_normalize(c, p, ev)
                norm[(c.pattern_group, c.issue_type)][d["category"]] += 1
                if d.get("unrecorded_change"):
                    zero["변경 기록 없는 정답"] += 1
                if d.get("logical_null_mismatch"):
                    zero["논리 null 불일치"] += 1
            elif c.action == "review":
                d |= judge_review(c, p, ev, pk)
                r = review[(c.pattern_group, c.issue_type)]
                r["n"] += 1
                r["탐지"] += d["detected"]
                r["보존"] += d["preserved"]
                if d.get("note"):
                    notes.add(f"{c.issue_type}: {d['note']}")
            else:
                d |= judge_keep(c, p, ev)
                keep[(c.pattern_group, c.issue_type)][d["category"]] += 1
            if p.lost:
                d["lost"] = p.lost
        details.append(d)

    # ---- 불필요 변경 넷째 범주: 사례가 없는 모든 셀(행 단위 사례의 행도 셀 단위, 셀 사례가 우선) ----
    cell_cases = {c.cell for c in cases if not c.is_row and not c.is_header}
    other = Counter()
    nontarget = Counter()
    for name, table in sorted(inputs.items()):
        for r, values in enumerate(table["records"], start=1):
            for col, original in zip(table["headers"], values, strict=True):
                if (name, r, col) in cell_cases:
                    continue
                other["분모"] += 1
                p = placer.place(name, r, col)
                if p.lost:
                    other["유실"] += 1
                elif p.value != original:
                    other["변경"] += 1
                if p.logical and p.record:
                    for cat, _, _ in ev.cell.get((p.table, p.record, p.logical), []):
                        if cat in (VIOLATION, REVIEW):
                            nontarget[cat] += 1

    n_norm = sum(sum(v.values()) for v in norm.values())
    n_review = sum(v["n"] for v in review.values())
    n_keep = sum(sum(v.values()) for v in keep.values())
    wrong_norm = sum(v["오변환"] for v in norm.values())
    review_broken = sum(v["n"] - v["보존"] for v in review.values())
    keep_changed = sum(v["값 변경"] for v in keep.values())

    n_map = sum(maps["auto"].values())
    auto_proposed = maps["auto"]["정답"] + maps["auto"]["오답"]
    audit = (bundle.meta.get("attempt") or {}).get("audit") or {}
    report = {
        "map_column": {
            "n": n_map,
            "자동": {k: maps["auto"][k] for k in MAP_AUTO},
            "최종": {k: maps["final"][k] for k in MAP_FINAL},
            "자동 정확도": None if auto_proposed == 0 else round(maps["auto"]["정답"] / auto_proposed, 4),
            "자동 범위": None if n_map == 0 else round(auto_proposed / n_map, 4),
            "최종 정확도": None if n_map == 0 else round(maps["final"]["정답"] / n_map, 4),
        },
        "normalize": [
            {"pattern_group": g, "issue_type": t, "n": sum(cnt.values()), **{k: cnt[k] for k in NORMALIZE},
             "정답률": _rate(cnt["정답"], sum(cnt.values()))}
            for (g, t), cnt in sorted(norm.items())],
        "review": [
            {"pattern_group": g, "issue_type": t, "n": cnt["n"], "탐지": cnt["탐지"], "보존": cnt["보존"]}
            for (g, t), cnt in sorted(review.items())],
        "keep": [
            {"pattern_group": g, "issue_type": t, "n": sum(cnt.values()), **{k: cnt[k] for k in KEEP},
             "오판": sum(cnt[k] for k in KEEP_WRONG)}
            for (g, t), cnt in sorted(keep.items())],
        "불필요 변경": {
            "normalize 오변환": {"건수": wrong_norm, "분모": n_norm},
            "review 보호 대상 변경·삭제·병합": {"건수": review_broken, "분모": n_review},
            "keep 대상 변경": {"건수": keep_changed, "분모": n_keep},
            "그 밖의 셀 변경": {"건수": other["변경"], "유실": other["유실"], "분모": other["분모"]},
        },
        "0건 조건": {
            "승인 없는 변경(서비스 감사)": None if not audit else len(audit.get("problems") or []),
            "감사 통과": audit.get("passed") if audit else None,
            "정답 기준 비대상 오수정": other["변경"],
            **{k: zero[k] for k in ("변경 기록 없는 정답", "논리 null 불일치", "매핑이 정답 컬럼과 다른 사례")},
        },
        "비대상 셀 경고 건수": {"business_rule_violation": nontarget[VIOLATION], "human_review": nontarget[REVIEW]},
        "주의": sorted(notes),
    }
    return report, details


def table_digest(report: dict) -> str:
    """재현성 비교용(AC-14-4): 머리말(시각 등)을 뺀 표 내용의 해시."""
    return _sha(json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def header(bundle: Bundle, split: str, condition: str, retry_reason: str | None, answers: dict[str, str]) -> dict:
    return {
        "코드 커밋": _git("rev-parse", "HEAD"),
        "작업 트리 변경 있음": _git("status", "--porcelain") not in ("", "unknown"),
        "채점기": f"{JUDGE_VERSION} ({SPEC_VERSION} 5절)",
        "분할": split,
        "평가 조건": condition,
        "실행": bundle.meta["run_id"],
        "실행 상태": bundle.meta["status"],
        "설정 스냅샷": bundle.meta["config_hash"],
        "채점 시각": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "재시도 사유": retry_reason,
        "정답 파일 해시": dict(sorted(answers.items())),
        "안내": NOTICE,
    }


def markdown(head: dict, report: dict, digest: str) -> str:
    out = ["# 채점 결과", "", f"> {NOTICE} 이 표는 현재 구현 범위의 결과다.", ""]
    out += [f"- {k}: {v}" for k, v in head.items() if k not in ("안내", "정답 파일 해시")]
    out += [f"- 표 내용 해시: `{digest}`", ""]

    def table(title: str, rows: list[dict]) -> None:
        out.extend([f"## {title}", ""])
        if not rows:
            out.extend(["(사례 없음)", ""])
            return
        cols = list(rows[0])
        out.append("| " + " | ".join(cols) + " |")
        out.append("|" + "---|" * len(cols))
        out.extend("| " + " | ".join("—" if r[c] is None else str(r[c]) for c in cols) + " |" for r in rows)
        out.append("")

    m = report["map_column"]
    table("헤더 매핑 (map_column)", [{"구분": "자동", **m["자동"]}])
    table("헤더 매핑 — 최종", [{"구분": "최종", **m["최종"]}])
    out += [f"- 자동 정확도 {m['자동 정확도']} · 자동 범위 {m['자동 범위']} · 최종 정확도 {m['최종 정확도']} (n={m['n']})", ""]
    table("셀 표준화 (normalize)", report["normalize"])
    table("검토 (review) — 건수", report["review"])
    table("유지 (keep) — 건수", report["keep"])
    table("불필요 변경", [{"범주": k, **v} for k, v in report["불필요 변경"].items()])
    table("0건 조건", [{"항목": k, "값": v} for k, v in report["0건 조건"].items()])
    w = report["비대상 셀 경고 건수"]
    out += [f"비대상 셀 경고(오탐으로 판정하지 않음): 위반 {w['business_rule_violation']} · 사람 검토 {w['human_review']}", ""]
    if report["주의"]:
        out += ["## 주의", ""] + [f"- {n}" for n in report["주의"]] + [""]
    return "\n".join(out)


def run(export: Path, split: str, *, answers_root: Path, inputs_root: Path, out: Path | None,
        condition: str, registry: dict[str, int] | None = None, retry_reason: str | None = None,
        answer_sums: Path | None = None) -> Path:
    bundle = load_bundle(export)
    hashes = answer_hashes(answers_root, split)
    if answer_sums is not None:
        check_sums(hashes, answer_sums)
    input_dir = inputs_root / split
    check_isolation(input_dir, bundle, hashes)
    cases = load_cases(answers_root / split / "cases.jsonl")
    check_manifest(cases, json.loads((answers_root / "manifest.json").read_text(encoding="utf-8")), split)
    inputs = read_inputs(input_dir, bundle)
    check_observed(cases, inputs)
    schema = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))
    check_semantics(cases, schema)
    report, details = score(bundle, cases, inputs, schema, registry)
    head = header(bundle, split, condition, retry_reason, hashes)
    digest = table_digest(report)
    if out is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = ROOT / "workspace" / "scores" / f"{bundle.meta['run_id']}-{stamp}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "score.json").write_text(json.dumps({"header": head, "table_digest": digest, "report": report},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "score.md").write_text(markdown(head, report, digest), encoding="utf-8")
    with (out / "cases.jsonl").open("w", encoding="utf-8") as f:
        for d in details:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="서비스 내보내기를 정답과 대조해 채점한다")
    parser.add_argument("export", type=Path)
    parser.add_argument("--split", choices=("development", "evaluation"), required=True)
    parser.add_argument("--confirm-final", action="store_true", help="평가 분할 최종 실행(④ 7절)")
    parser.add_argument("--answers-root", type=Path, default=ROOT / "evaluation_private")
    parser.add_argument("--inputs-root", type=Path, default=ROOT / "data" / "dirty")
    parser.add_argument("--registry", type=Path, help="입력 등록표 JSON: {정답 input_file: 업로드 항목 ID}")
    parser.add_argument("--condition", default="rules-only")
    parser.add_argument("--retry-reason")
    parser.add_argument("--answer-sums", type=Path, help="정답 파일 SHA256SUMS(고정 기록·백업)와 대조")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.split == "evaluation" and not args.confirm_final:
        print("평가 분할은 --confirm-final 과 고정 기록(④ 8절) 뒤에만 채점한다", file=sys.stderr)
        return 2
    registry = None
    if args.registry:
        registry = {k: int(v) for k, v in json.loads(args.registry.read_text(encoding="utf-8")).items()}
    try:
        out = run(args.export, args.split, answers_root=args.answers_root, inputs_root=args.inputs_root,
                  out=args.out, condition=args.condition, registry=registry, retry_reason=args.retry_reason,
                  answer_sums=args.answer_sums)
    except (AnswerError, BundleError, IsolationError, ScoreError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(out / "score.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
