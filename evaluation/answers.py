"""정답 읽기·검증 (docs/quality-evaluation-spec.md 2·5절, M2-2·M2-3).

정답은 채점기만 읽는다. 읽은 정답은 형식·행동 규칙·입력 대조·manifest 집계로 검증하고,
하나라도 어긋나면 채점하지 않는다(변조된 정답으로 점수를 내지 않음, AC-14-2).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ACTIONS = {"map_column", "normalize", "review", "keep"}
REQUIRED = ("case_id", "input_file", "record_number", "column", "observed_value", "expected_action", "issue_type",
            "pattern_group")


class AnswerError(ValueError):
    pass


@dataclass(frozen=True)
class Case:
    case_id: str
    input_file: str
    record: int
    column: str
    observed: object  # 셀은 문자열, 행 단위 사례는 원래 헤더 → 값
    action: str
    expected: str | None
    issue_type: str
    pattern_group: str
    canonical_column: str | None

    @property
    def is_header(self) -> bool:
        return self.record == 0

    @property
    def is_row(self) -> bool:
        return self.column == "*"

    @property
    def cell(self) -> tuple[str, int, str]:
        return (self.input_file, self.record, self.column)


def load_cases(path: Path) -> list[Case]:
    cases, seen_ids, seen_cells = [], set(), set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AnswerError(f"{number}번째 줄이 JSON이 아니다") from exc
        missing = [k for k in REQUIRED if k not in o]
        if missing:
            raise AnswerError(f"{number}번째 줄에 필드가 없다: {missing}")
        action = o["expected_action"]
        if action not in ACTIONS:
            raise AnswerError(f"{number}번째 줄: 알 수 없는 행동 {action!r}")
        if not isinstance(o["record_number"], int) or o["record_number"] < 0:
            raise AnswerError(f"{number}번째 줄: record_number가 0 이상 정수가 아니다")
        # 행동별 형식 규칙(benchmark.md): review에는 기대값이 없고, 나머지는 문자열 기대값이 있다
        if action == "review":
            if "expected_value" in o:
                raise AnswerError(f"{number}번째 줄: review 사례에 기대값이 있다(복원값 주입 의심)")
        elif not isinstance(o.get("expected_value"), str):
            raise AnswerError(f"{number}번째 줄: {action} 사례에 문자열 기대값이 없다")
        is_header, is_row = o["record_number"] == 0, o["column"] == "*"
        if (action == "map_column") != is_header:
            raise AnswerError(f"{number}번째 줄: 헤더 사례(record_number=0)는 map_column이어야 한다")
        if is_row and (action != "review" or not isinstance(o["observed_value"], dict)):
            raise AnswerError(f"{number}번째 줄: 행 단위 사례는 review이고 관측값이 행이어야 한다")
        if not is_row and not isinstance(o["observed_value"], str):
            raise AnswerError(f"{number}번째 줄: 셀 사례의 관측값이 문자열이 아니다")
        case = Case(o["case_id"], o["input_file"], o["record_number"], o["column"], o["observed_value"], action,
                    o.get("expected_value"), o["issue_type"], o["pattern_group"], o.get("canonical_column"))
        if case.case_id in seen_ids:
            raise AnswerError(f"사례 ID가 겹친다: {case.case_id}")
        if case.cell in seen_cells:
            raise AnswerError(f"같은 위치에 사례가 둘 있다: {case.cell}")
        seen_ids.add(case.case_id)
        seen_cells.add(case.cell)
        cases.append(case)
    if not cases:
        raise AnswerError("정답이 비어 있다")
    return cases


def check_manifest(cases: list[Case], manifest: dict, split: str) -> None:
    """정답 집계가 manifest의 해당 분할 집계와 같은지(정답 변조·누락 탐지). 다른 분할 값은 보지 않는다."""
    expected = (manifest.get("splits") or {}).get(split)
    if not expected:
        raise AnswerError(f"manifest에 {split} 분할 집계가 없다")
    if expected.get("cases") != len(cases):
        raise AnswerError("정답 건수가 manifest와 다르다")
    if dict(Counter(c.action for c in cases)) != expected.get("actions"):
        raise AnswerError("행동별 건수가 manifest와 다르다")
    if dict(Counter(c.issue_type for c in cases)) != expected.get("issue_types"):
        raise AnswerError("유형별 건수가 manifest와 다르다")


def check_observed(cases: list[Case], inputs: dict[str, dict]) -> None:
    """관측값이 실제 입력과 같은지(정답 변조 탐지). inputs: 파일 이름 → {headers, records}."""
    for c in cases:
        table = inputs.get(c.input_file)
        if table is None:
            raise AnswerError(f"정답이 가리키는 입력 파일이 없다: {c.input_file}")
        headers, records = table["headers"], table["records"]
        if c.is_header:
            if c.column not in headers or c.observed != c.column:
                raise AnswerError(f"헤더 사례가 입력 헤더와 다르다: {c.case_id}")
            continue
        if c.record > len(records):
            raise AnswerError(f"레코드 번호가 입력 범위를 벗어난다: {c.case_id}")
        row = dict(zip(headers, records[c.record - 1], strict=True))
        if c.is_row:
            if c.observed != row:
                raise AnswerError(f"행 사례의 관측값이 입력 행과 다르다: {c.case_id}")
        elif c.column not in row or row[c.column] != c.observed:
            raise AnswerError(f"관측값이 입력과 다르다: {c.case_id}")


def check_semantics(cases: list[Case], schema: dict) -> None:
    """행동 의미와 맞지 않는 정답(변조 의심)을 거부한다. 공개 schema.json만 쓴다."""
    columns = {f["name"] for spec in schema["tables"].values() for f in spec["fields"]}
    for c in cases:
        if c.action == "normalize" and c.expected == c.observed:
            raise AnswerError(f"normalize 기대값이 관측값과 같다: {c.case_id}")
        if c.action == "keep" and c.expected != c.observed:
            raise AnswerError(f"keep 기대값이 관측값과 다르다: {c.case_id}")
        if c.action == "map_column" and c.expected not in columns:
            raise AnswerError(f"map_column 기대값이 표준 컬럼이 아니다: {c.case_id}")
        if not c.is_header and c.canonical_column not in columns | {"*", None}:
            raise AnswerError(f"canonical_column이 표준 컬럼이 아니다: {c.case_id}")


def check_sums(hashes: dict[str, str], sums_path: Path) -> None:
    """고정·백업해 둔 SHA256SUMS(개발 계획 8절 8)와 정답 파일 해시를 대조한다."""
    pinned = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            sha, name = line.split(maxsplit=1)
            pinned[Path(name.strip().lstrip("*")).name] = sha
    for name, sha in hashes.items():
        if name in pinned and pinned[name] != sha:
            raise AnswerError(f"정답 파일 해시가 고정 기록과 다르다: {name}")
    if not set(pinned) & set(hashes):
        raise AnswerError("고정 기록에 대조할 정답 파일이 없다")
