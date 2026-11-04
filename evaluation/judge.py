"""위치 맞추기와 행동별 판정 (docs/quality-evaluation-spec.md 5-2·5-3절, judge-v1).

- 헤더 사례는 매핑 기록으로, 셀·행 사례는 서비스 역추적 표로 원본(업로드 항목·레코드·원래 컬럼)에 연결한다.
- PK가 같은 행도 원본 위치로만 맞춘다(키 값으로 맞추지 않음).
- 원래 셀이 간 곳을 따라간다: 최종 매핑의 논리 컬럼, 매핑하지 않은 컬럼은 결과에 다시 붙은 원래 이름 컬럼.
- 매핑 오류·발행 제외 테이블·결과 누락·발행 실패는 분모에서 빼지 않고 유실 등 실패 범주로 센다.
"""

from __future__ import annotations

from dataclasses import dataclass

from evaluation.answers import Case
from evaluation.bundle import Bundle, Evidence

JUDGE_VERSION = "judge-v1"

VIOLATION, REVIEW = "business_rule_violation", "human_review"
CANDIDATE, OBSERVATION = "standardization_candidate", "normal_observation"
MISSING_CODES = {"blank", "missing_candidate"}
RANGE_CODES = {"min", "max"}

NORMALIZE = ("유실", "정답", "오변환", "보류", "누락")
MAP_AUTO = ("유실", "정답", "오답", "확인 필요", "후보 없음")
MAP_FINAL = ("유실", "정답", "오답", "미완료")
KEEP = ("유실", "값 변경", "오류 확정", "추가 검토 요청", "참고 관측", "표시 없음")
KEEP_WRONG = {"값 변경", "오류 확정"}


class ScoreError(ValueError):
    pass


@dataclass(frozen=True)
class Place:
    item_id: int | None = None
    table: str | None = None
    column: str | None = None  # 결과 CSV에서 찾을 컬럼 이름
    logical: str | None = None  # 최종 매핑의 논리 컬럼(매핑 안 했으면 None)
    record: int | None = None  # 뷰 레코드(= 결과 데이터 행 번호)
    lost: str | None = None
    value: str | None = None


class Placer:
    def __init__(self, bundle: Bundle, registry: dict[str, int] | None = None):
        self.b = bundle
        self.registry = registry or {}
        self.rows = {}
        if bundle.lineage:
            for t, spec in bundle.lineage["tables"].items():
                self.rows[t] = {(r["item_id"], r["source_record"]): r["record"] for r in spec["rows"]}

    def item(self, input_file: str) -> dict | None:
        if input_file in self.registry:
            hit = [i for i in self.b.inputs if i["item_id"] == self.registry[input_file]]
            if not hit:
                raise ScoreError(f"입력 등록표의 업로드 항목이 없다: {input_file}")
            return hit[0]
        named = self.b.items_named(input_file)
        if len(named) > 1:
            raise ScoreError(f"같은 이름 업로드 항목이 여럿이다. 입력 등록표가 필요하다: {input_file}")
        return named[0] if named else None

    def place(self, input_file: str, record: int, column: str | None) -> Place:
        item = self.item(input_file)
        if item is None:
            return Place(lost="업로드 안 됨")
        iid = item["item_id"]
        if item.get("read_error"):
            return Place(iid, lost="읽기 실패")
        final = (self.b.mapping.get(iid) or {}).get("final") or {}
        table = final.get("table")
        if not table:
            return Place(iid, lost="테이블 미지정")
        logical = (final.get("columns") or {}).get(column) if column else None
        name = logical or column
        rec = self.rows.get(table, {}).get((iid, record))
        if rec is None:
            return Place(iid, table, name, logical, lost="역추적 없음")
        if not self.b.published:
            return Place(iid, table, name, logical, rec, lost="발행 실패" if self.b.meta.get("status") == "failed"
                         else "미발행")
        result = self.b.result.get(table)
        if result is None:
            return Place(iid, table, name, logical, rec, lost="발행 제외 테이블")
        if rec - 1 >= len(result["rows"]):
            return Place(iid, table, name, logical, rec, lost="결과 행 없음")
        if column is None:
            return Place(iid, table, None, None, rec)
        if name not in result["header"]:
            return Place(iid, table, name, logical, rec, lost="결과 컬럼 없음")
        return Place(iid, table, name, logical, rec, value=result["rows"][rec - 1][result["header"].index(name)])


def _signals(ev: Evidence, p: Place) -> list[tuple[str, str, str | None]]:
    if p.table is None or p.record is None or p.logical is None:
        return []
    return ev.cell.get((p.table, p.record, p.logical), [])


def judge_normalize(case: Case, p: Place, ev: Evidence) -> dict:
    key = (p.table, p.record, p.logical)
    if p.lost:
        return {"category": "유실", "reason": p.lost}
    if p.value == case.expected:
        out = {"category": "정답"}
        change = ev.changes.get(key) if p.logical else None
        if case.observed != case.expected and change is None:
            out["unrecorded_change"] = True  # ④ 5-3: 정답이어도 승인된 변경 기록이 없으면 0건 조건 위반
        elif change is not None and case.expected == "" and change.get("logical_after") is not None:
            out["logical_null_mismatch"] = True
        return out
    if p.value != case.observed:
        return {"category": "오변환"}
    reasons = []
    if key in ev.held:
        reasons.append("보류 목록")
    if key in ev.not_approved:
        reasons.append("승인 안 함")
    if any(cat in (VIOLATION, REVIEW) for cat, _, _ in _signals(ev, p)):
        reasons.append("위반·검토")
    if reasons:
        return {"category": "보류", "reason": "·".join(reasons)}
    return {"category": "누락"}


def judge_map(case: Case, bundle: Bundle, placer: Placer) -> dict:
    item = placer.item(case.input_file)
    if item is None:
        return {"auto": "유실", "final": "유실"}
    m = bundle.mapping.get(item["item_id"]) or {}
    proposal = ((m.get("proposal") or {}).get("columns") or {}).get(case.column)
    if proposal is None:
        auto = "후보 없음"
    elif proposal["needs_confirmation"]:
        auto = "확인 필요"
    elif proposal["logical"] is None:
        auto = "후보 없음"
    else:
        auto = "정답" if proposal["logical"] == case.expected else "오답"
    final_cols = (m.get("final") or {}).get("columns") or {}
    chosen = final_cols.get(case.column)
    if not m.get("confirmed") or chosen is None:
        final = "미완료"
    else:
        final = "정답" if chosen == case.expected else "오답"
    return {"auto": auto, "final": final}


def detected(case: Case, p: Place, ev: Evidence, pk: dict[str, list[str]]) -> tuple[bool, str | None]:
    """review 탐지 근거(④ 5-3 표). 같은 위치의 다른 종류 경고는 인정하지 않는다."""
    if p.table is None or p.record is None:
        return False, p.lost
    t = case.issue_type
    if t == "duplicate_pk":
        groups = ev.groups.get((p.table, p.record), [])
        return any(g["code"] == "unique" and g["columns"] == pk.get(p.table) for g in groups), None
    if t == "total_conflict":  # 그 주문을 기준 행으로 하는 R02 위반 묶음(원문 또는 가상 기준)
        hit = any(rule.startswith("R02") for rule in ev.integrity.get((p.table, p.record), set()))
        return hit, None
    sig = _signals(ev, p)
    if t == "required_missing":
        return any(cat == VIOLATION and code == "required" for cat, code, _ in sig), None
    if t == "truncated_phone":
        return any(cat == REVIEW and code == "pattern" for cat, code, _ in sig), None
    if t == "ambiguous_date":
        return any(cat == REVIEW and code == "type" for cat, code, _ in sig), None
    if t == "ambiguous_missing":
        return any(cat == REVIEW and code in MISSING_CODES for cat, code, _ in sig), None
    if t == "unknown_fk":
        return any(cat == VIOLATION and code == "reference" for cat, code, _ in sig), None
    if t == "zero_quantity":
        return any((cat == VIOLATION and code in RANGE_CODES) or cat == REVIEW for cat, code, _ in sig), None
    raise ScoreError(f"탐지 근거가 정해지지 않은 review 유형: {t}")


def judge_review(case: Case, p: Place, ev: Evidence, pk: dict[str, list[str]]) -> dict:
    hit, note = detected(case, p, ev, pk)
    if p.lost:
        preserved = False
    elif case.is_row:
        preserved = True  # 원본 행이 결과에 그대로 남음(엔진은 행을 지우거나 합치지 않고, 감사가 행 수를 대조)
    else:
        preserved = p.value == case.observed
    out = {"detected": hit, "preserved": preserved}
    if note:
        out["note"] = note
    if p.lost:
        out["lost"] = p.lost
    return out


def judge_keep(case: Case, p: Place, ev: Evidence) -> dict:
    if p.lost:
        return {"category": "유실", "reason": p.lost}
    if p.value != case.observed:
        return {"category": "값 변경"}
    sig = _signals(ev, p)
    if any(cat == VIOLATION for cat, _, _ in sig) or any(
            cat == CANDIDATE for cat, _, _ in sig):
        return {"category": "오류 확정"}
    if any(cat == REVIEW for cat, _, _ in sig):
        return {"category": "추가 검토 요청"}
    if any(cat == OBSERVATION for cat, _, _ in sig):
        return {"category": "참고 관측"}
    return {"category": "표시 없음"}
