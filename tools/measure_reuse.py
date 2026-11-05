"""규칙 재사용의 검토 감소 측정 (QE ④ 6-1, FS F09, M6-8).

    uv run python -m tools.measure_reuse [--inputs 개발 입력 폴더] [--out 출력 폴더]

1. 나누기: 입력을 고객 단위로 A·B 두 묶음으로 나눈다. 고객의 주문과 주문에 딸린 주문상세·결제·출고·반품·환불
   (출고상세·반품상세·환불은 부모를 따라)이 같은 쪽으로 가고, 키가 같은 중복 행도 함께 간다.
   상품·옵션(SKU)·카테고리 기준표는 양쪽이 공유한다. 정답은 읽지 않는다(헤더는 설정의 표준 이름·별칭으로만 찾는다).
2. A를 서비스 흐름 그대로 실행하고 적용한 묶음·매핑을 모두 재사용 규칙으로 승인한다(발행해야 효력, M6-D1).
3. A 직후의 앱 상태(DB·workspace 폴더)를 두 벌 복제해 B를 실행한다.
   ① 재사용 없이(미리 선택·자동 제안 끔) ② 재사용 규칙으로 미리 선택. 차이는 이것 하나다.
   두 조건 모두 같은 최종 결정(후보 전체 승인)으로 실행하므로 결과 데이터가 같아야 한다(안전 비교의 전제).
4. 실행 승인 때 기록된 `run_metrics`로 감소율 = (① − ②) ÷ ①과 구성 항목을 보고한다. ①이 0이면 N/A.

허용 입력은 개발 입력 폴더(data/dirty/development) 또는 저장소 밖 폴더(합성 fixture)뿐이다. 평가 분할은 쓰지 않는다.
검토 항목은 서로 다른 단위를 더한 지표이며 작업 시간 감소와 같다고 보지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from prep import metrics, publish
from prep.mapping import Catalog, norm
from prep.preview import PROJECT_ROOT, FlowError, Workspace

DEV_INPUTS = PROJECT_ROOT / "data" / "dirty" / "development"
SHARED = ("categories", "products", "product_variants")
# 표 → (자기 키, 부모 연결: [(부모 키 컬럼, 부모 표)]) — 부모 표의 자기 키로 쪽을 찾는다
LINKS = {
    "customers": ("customer_id", []),
    "customer_addresses": (None, [("customer_id", "customers")]),
    "orders": ("order_id", [("customer_id", "customers")]),
    "order_items": (None, [("order_id", "orders")]),
    "payments": ("payment_id", [("order_id", "orders")]),
    "shipments": ("shipment_id", [("order_id", "orders")]),
    "returns": ("return_id", [("order_id", "orders")]),
    "shipment_items": (None, [("shipment_id", "shipments")]),
    "return_items": (None, [("return_id", "returns")]),
    "refunds": (None, [("payment_id", "payments"), ("return_id", "returns")]),
}
ORDER = list(LINKS)


class MeasureError(RuntimeError):
    pass


def check_inputs(path: Path) -> Path:
    """개발 입력 폴더 또는 저장소 밖 폴더만 허용한다(평가 자료 경로는 열기 전에 거부)."""
    p = path.resolve()
    if p != DEV_INPUTS.resolve() and (p == PROJECT_ROOT or PROJECT_ROOT in p.parents):
        raise MeasureError(f"허용하지 않는 입력 폴더다(개발 입력 또는 저장소 밖 fixture만): {path}")
    return p


def catalog() -> Catalog:
    read = lambda rel: json.loads((PROJECT_ROOT / rel).read_text(encoding="utf-8"))  # noqa: E731
    return Catalog(read("schema.json"), read("config/sets/shop/logical-diagnostics.json"),
                   read("config/sets/shop/aliases.json"))


def _table_of(name: str, cat: Catalog) -> str | None:
    n = norm(Path(name).stem)
    return next((t for t, names in cat.table_aliases.items() if n in names), None)


def _key(value: str) -> str:
    return value.strip().upper()


def _side(key: str) -> str:
    return "A" if hashlib.sha256(key.encode("utf-8")).digest()[0] % 2 == 0 else "B"


def split(files: dict[str, bytes], cat: Catalog | None = None) -> tuple[dict[str, bytes], dict[str, bytes], dict]:
    """(A 파일, B 파일, 요약). 고객 키의 해시로 쪽을 정하고 하위 표는 부모를 따른다. 결정적."""
    cat = cat or catalog()
    parsed, tables = {}, {}
    for name, data in files.items():
        text = data.decode("utf-8")
        bom = text.startswith("﻿")
        rows = list(csv.reader(io.StringIO(text.removeprefix("﻿"), newline="")))
        table = _table_of(name, cat)
        if table is None or not rows:
            raise MeasureError(f"표준 테이블을 알 수 없는 입력이다: {name}")
        parsed[name] = (bom, rows[0], rows[1:])
        tables[name] = table
    side_of: dict[str, dict[str, str]] = {t: {} for t in LINKS}
    out: dict[str, dict[str, list[list[str]]]] = {"A": {}, "B": {}}
    summary = {}

    def col(header: list[str], table: str, logical: str) -> int | None:
        return next((i for i, h in enumerate(header) if cat.known(h, table) == logical), None)

    for name in sorted(files, key=lambda n: ORDER.index(tables[n]) if tables[n] in ORDER else -1):
        table, (bom, header, body) = tables[name], parsed[name]
        if table in SHARED:
            out["A"][name] = out["B"][name] = body
            summary[name] = {"shared": len(body)}
            continue
        if table not in LINKS:
            raise MeasureError(f"나누기 규칙이 없는 표다: {table}")
        own, parents = LINKS[table]
        own_i = col(header, table, own) if own else None
        parent_i = [(col(header, table, c), t) for c, t in parents]
        if own and own_i is None or any(i is None for i, _ in parent_i):
            raise MeasureError(f"나누기 키 컬럼을 찾지 못했다: {name}")
        rows = {"A": [], "B": []}
        for r in body:
            side = None
            if own_i is not None and _key(r[own_i]) in side_of[table]:
                side = side_of[table][_key(r[own_i])]  # 같은 키(중복 행)는 먼저 정한 쪽으로
            for i, t in parent_i:
                if side is None and r[i].strip():
                    side = side_of[t].get(_key(r[i])) or (_side(_key(r[i])) if t == "customers" else None)
            if side is None:  # 부모를 찾지 못한 행: 자기 키(없으면 행 전체)의 해시
                side = _side(_key(r[own_i]) if own_i is not None and r[own_i].strip() else "\x1f".join(r))
            if own_i is not None:
                side_of[table].setdefault(_key(r[own_i]), side)
            rows[side].append(r)
        out["A"][name], out["B"][name] = rows["A"], rows["B"]
        summary[name] = {"A": len(rows["A"]), "B": len(rows["B"])}

    def encode(name: str, body: list[list[str]]) -> bytes:
        bom, header, _ = parsed[name]
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows([header, *body])
        return (("﻿" if bom else "") + buf.getvalue()).encode("utf-8")

    return ({n: encode(n, b) for n, b in out["A"].items()}, {n: encode(n, b) for n, b in out["B"].items()}, summary)


def run_flow(ws: Workspace, files: dict[str, bytes], source: str, approve_reuse: bool) -> str:
    """업로드 → (변경 확인) → 자동 제안 그대로 매핑 확정 → 진단 → 후보 전체 승인 → 실행."""
    run = ws.create_run("shop", sorted(files.items()), source)
    rid = run["id"]
    drift = ws.drift_view(rid)
    if drift["required"] and not drift["confirmed"]:
        ws.drift_confirm(rid)
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in ws.load(rid)["files"] if not f["read_error"]]
    try:
        ws.confirm_mapping(rid, maps)
    except FlowError as exc:
        if exc.code != "reuse_impact_needed":
            raise
        ws.confirm_mapping(rid, maps, {str(i["item_id"]): "this_run" for i in exc.details["impacts"]})
    ws.diagnose(rid)
    p = ws.changes_preview(rid)
    p = ws.select(rid, {"view_hash": p["view_hash"], "approved_groups": None, "excluded_cells": []})  # 같은 최종 결정
    choice = None
    if approve_reuse:
        choice = {"groups": [g["id"] for g in p["groups"] if g["approved"] and g["cells"]],
                  "mappings": [f["item_id"] for f in ws.load(rid)["files"] if not f["read_error"]]}
    result = ws.execute(rid, p["approval"], choice)
    if result["status"] == "failed":
        raise MeasureError(f"실행 실패 {rid}: {result.get('error')}")
    return rid


def _summary(ws: Workspace, rid: str) -> dict:
    db_run = ws.load(rid)["db_run"]
    m = metrics.get(ws.store, db_run)
    version = publish.latest(ws.store, db_run)
    hashes = version["data_hashes"] if isinstance(version["data_hashes"], dict) else json.loads(version["data_hashes"])
    return {"run": rid, "review_items": m["review_items"], "inputs": m["inputs"],
            "data_hashes": dict(sorted(hashes.items()))}


def _data(summary: dict) -> dict:
    """결과 데이터(CSV) 해시만 — 엔진 JSON 산출물에는 실행 폴더 경로가 들어가 조건마다 다르다."""
    return {k: v for k, v in summary["data_hashes"].items() if k.endswith(".csv")}


def rate(before: int, after: int) -> float | None:
    return None if before == 0 else round((before - after) / before, 4)


def measure(inputs: Path, out: Path, source: str = "재사용 측정") -> dict:
    inputs = check_inputs(inputs)
    files = {p.name: p.read_bytes() for p in sorted(inputs.glob("*.csv"))}
    if not files:
        raise MeasureError(f"CSV 입력이 없다: {inputs}")
    a, b, parts = split(files)
    out.mkdir(parents=True)
    first = Workspace(out / "a")
    run_a = run_flow(first, a, source, approve_reuse=True)
    base = _summary(first, run_a)
    first.store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    first.store.close()  # DB 파일을 다 쓴 뒤에 복제한다
    shutil.copytree(out / "a", out / "b1")  # A 직후 상태 복제(두 조건의 같은 시작점)
    shutil.copytree(out / "a", out / "b2")
    w1 = Workspace(out / "b1", reuse_enabled=False)
    w2 = Workspace(out / "b2")
    one = _summary(w1, run_flow(w1, b, source, approve_reuse=False))
    two = _summary(w2, run_flow(w2, b, source, approve_reuse=False))
    t1, t2 = one["review_items"], two["review_items"]
    report = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"), "inputs": str(inputs), "split": parts,
        "a": {"run": run_a, "review_items": base["review_items"]},
        "without_reuse": one, "with_reuse": two,
        "reduction": rate(t1["total"], t2["total"]),
        "components": {k: {"without_reuse": t1[k], "with_reuse": t2[k], "reduction": rate(t1[k], t2[k])}
                       for k in (*metrics.COMPONENTS, "total")},
        "same_result": _data(one) == _data(two),
        "note": "검토 항목은 서로 다른 단위를 더한 지표이며 작업 시간 감소와 같지 않다(④ 6-1). 합성 벤치마크.",
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def render(report: dict) -> str:
    pct = lambda r: "N/A" if r is None else f"{r * 100:.1f}%"  # noqa: E731
    lines = ["| 항목 | ① 재사용 없음 | ② 재사용 | 감소율 |", "|---|---:|---:|---:|"]
    names = {"mapping": "확인 필요 매핑", "groups": "미리 선택되지 않은 묶음", "human_review": "사람 검토 셀",
             "business_groups": "업무 위반 그룹", "held": "보류 셀", "total": "합계"}
    for k, v in report["components"].items():
        lines.append(f"| {names[k]} | {v['without_reuse']} | {v['with_reuse']} | {pct(v['reduction'])} |")
    lines.append(f"\n결과 데이터 같음(같은 최종 결정): {'예' if report['same_result'] else '아니오'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="규칙 재사용의 검토 감소를 A·B 분할로 측정한다(④ 6-1)")
    parser.add_argument("--inputs", type=Path, default=DEV_INPUTS)
    parser.add_argument("--out", type=Path,
                        default=PROJECT_ROOT / "workspace" / "measure-reuse" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args(argv)
    try:
        report = measure(args.inputs, args.out)
    except (MeasureError, FlowError) as exc:
        print(f"측정 실패: {exc}", file=sys.stderr)
        return 1
    print(render(report))
    print(f"\n보고서: {args.out / 'report.json'}", file=sys.stderr)
    return 0 if report["same_result"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
