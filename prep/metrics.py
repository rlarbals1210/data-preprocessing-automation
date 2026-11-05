"""검토 항목 기록 (QE ④ 6-1, FS F09, TD 4-3 `run_metrics`, M6-8).

검토 항목(화면 판단 항목 지표) = 확인 필요 매핑 + 미리 선택되지 않은 표준화 묶음 + 사람 검토 고유 셀
+ 업무 위반 그룹 + 보류 셀. 서로 다른 단위를 더한 지표이며 작업 시간과 같다고 보지 않는다.

- 확인 필요 매핑: 실행 대상 파일마다 테이블 분류가 자동이 아니면 1 + 확인이 필요한 컬럼 수(유사·형태 후보, 이유 있음).
  재사용 규칙(확신 등급 `reuse`)으로 자동 제안된 컬럼은 확인 필요가 아니다.
- 미리 선택되지 않은 묶음(결정 M6-D4): 적용 후보 셀이 있는 묶음 중 재사용 규칙과 맞는 셀이 전부가 아닌 묶음
  (일부만 맞아도 1). 규칙이 없는 양식의 기본 전체 선택은 미리 선택으로 세지 않는다.
- 사람 검토: `human_review` 이슈가 걸린 (테이블, 레코드, 컬럼) 고유 셀 수.
- 업무 위반 그룹: 중복 그룹 수 + 중복이 아닌 업무 위반의 (테이블, 레코드, 진단 코드) 수 + 정합성 위반 묶음 수
  (원문·가상 기준의 규칙 ID·관련 행 목록 합집합).
- 보류 셀: 적용 계획의 보류 셀 수.
실행 승인(최종 확인) 때 한 번 기록한다. 계산 근거(묶음 ID·컬럼 목록 등)를 `inputs`에 함께 남긴다.
"""

from __future__ import annotations

import json

from prep.store import Store

COMPONENTS = ("mapping", "groups", "human_review", "business_groups", "held")


def review_items(proposals: dict[int, dict], groups: list[dict], report: dict, held_cells: int,
                 integrity: dict | None) -> tuple[dict, dict]:
    """(review_items, inputs). proposals: 실행 대상 업로드 항목 → 매핑 제안. groups: 미리보기 묶음(`reuse` 포함)."""
    mapping, mapping_in = 0, {}
    for item, p in sorted(proposals.items()):
        cols = [c["original"] for c in p["columns"] if c["needs_confirmation"]]
        table = 0 if p["table"].get("auto") else 1
        mapping += table + len(cols)
        mapping_in[str(item)] = {"table": bool(table), "columns": cols}
    open_groups = sorted(g["id"] for g in groups if g["cells"] and g.get("reuse", 0) < g["cells"])
    covered = sorted(g["id"] for g in groups if g["cells"] and g.get("reuse", 0) >= g["cells"])
    review = {(i["table"], i["record"], c) for i in report["issues"] if i["category"] == "human_review"
              for c in i["columns"]}
    business = {(i["table"], i["record"], i["code"]) for i in report["issues"]
                if i["category"] == "business_rule_violation" and i["code"] != "duplicate_row"}
    bundles = set()
    for basis in ("raw", "virtual"):
        for v in (integrity or {}).get(basis, {}).get("violations", []):
            bundles.add((v["rule"], json.dumps(v["rows"], sort_keys=True)))
    duplicates = len(report.get("duplicate_groups") or [])
    items = {"mapping": mapping, "groups": len(open_groups), "human_review": len(review),
             "business_groups": duplicates + len(business) + len(bundles), "held": held_cells}
    items["total"] = sum(items[k] for k in COMPONENTS)
    inputs = {"mapping": mapping_in, "groups_not_preselected": open_groups, "groups_preselected": covered,
              "business": {"duplicate_groups": duplicates, "violations": len(business),
                           "integrity_bundles": len(bundles)}}
    return items, inputs


def record(store: Store, run_id: int, items: dict, inputs: dict) -> None:
    with store.tx() as c:
        c.execute("INSERT INTO run_metrics (run_id, review_items, inputs) VALUES (?, ?, ?)",
                  (run_id, json.dumps(items, sort_keys=True), json.dumps(inputs, ensure_ascii=False, sort_keys=True)))


def get(store: Store, run_id: int) -> dict | None:
    row = store.conn.execute("SELECT * FROM run_metrics WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return {"review_items": json.loads(row["review_items"]), "inputs": json.loads(row["inputs"]),
            "created_at": row["created_at"]}
