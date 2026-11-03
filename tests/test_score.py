"""M2-2 채점기 코어 (④ 5절, FS F14). 합성 입력·합성 정답만 쓴다(개발·평가 정답을 읽지 않음)."""

import json

import pytest

from evaluation import score as scorer
from evaluation.bundle import Bundle, Evidence, load_bundle
from evaluation.judge import Placer, judge_normalize
from prep import publish
from tests.score_helpers import CASES, do_score, load_cases_from, make_env, service_run


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


def test_score_categories(env):
    data, d = do_score(env, service_run(env))
    rep = data["report"]
    # map_column: 자동·최종을 따로
    assert {k: d[k]["auto"] for k in ("H1", "H2", "H3")} == {"H1": "정답", "H2": "정답", "H3": "오답"}
    assert rep["map_column"]["자동 정확도"] == 0.6667 and rep["map_column"]["자동 범위"] == 1.0
    assert rep["map_column"]["최종"]["정답"] == 2 and rep["map_column"]["최종"]["오답"] == 1
    # normalize: 위에서부터 첫 범주
    got = {k: d[k]["category"] for k in ("N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8")}
    assert got == {"N1": "정답", "N2": "정답", "N3": "정답", "N4": "정답", "N5": "정답",
                   "N6": "오변환", "N7": "보류", "N8": "누락"}
    assert d["N7"]["reason"] == "위반·검토"
    # review: 탐지와 보존을 따로
    assert {k: (d[k]["detected"], d[k]["preserved"]) for k in ("R1", "R2", "R3", "R4", "R5", "R6")} == {
        "R1": (True, True), "R2": (True, True), "R3": (True, True), "R4": (True, True), "R5": (True, True),
        "R6": (False, True)}
    assert any("R02" in n for n in rep["주의"])  # 미구현 기능은 실패를 그대로 표시
    # keep: 앞의 둘만 오판
    assert {k: d[k]["category"] for k in ("K1", "K2", "K3")} == {"K1": "추가 검토 요청", "K2": "참고 관측",
                                                              "K3": "값 변경"}  # K1은 중복 행 검토 행
    # 불필요 변경: 사례 없는 셀 중 C9 회원상태(ACTIVE→active)가 바뀜
    other = rep["불필요 변경"]["그 밖의 셀 변경"]
    assert other["건수"] == 1 and other["유실"] == 0
    assert other["분모"] == 9 * 8 + 2 * 4 - len([c for c in CASES if c["record_number"] and c["column"] != "*"])
    assert rep["불필요 변경"]["keep 대상 변경"] == {"건수": 1, "분모": 3}
    assert rep["0건 조건"]["감사 통과"] is True and rep["0건 조건"]["승인 없는 변경(서비스 감사)"] == 0
    assert rep["0건 조건"]["변경 기록 없는 정답"] == 0


def test_category_sums_equal_n(env):
    """④ 5-3: normalize 범주 합계 = n, keep 구분 합계 = n, map_column 자동·최종 합계 = n."""
    data, _ = do_score(env, service_run(env))
    rep = data["report"]
    for r in rep["normalize"]:
        assert sum(r[k] for k in ("유실", "정답", "오변환", "보류", "누락")) == r["n"]
    for r in rep["keep"]:
        assert sum(r[k] for k in ("유실", "값 변경", "오류 확정", "추가 검토 요청", "참고 관측", "표시 없음")) == r["n"]
    m = rep["map_column"]
    assert sum(m["자동"].values()) == sum(m["최종"].values()) == m["n"] == 3
    total = sum(r["n"] for r in rep["normalize"]) + sum(r["n"] for r in rep["review"]) + sum(
        r["n"] for r in rep["keep"]) + m["n"]
    assert total == len(CASES)


def test_failed_publish_counts_as_lost(env, monkeypatch):
    def boom(*a, **k):
        raise OSError("쓰기 실패 흉내")
    with monkeypatch.context() as m:
        m.setattr(publish, "write_files", boom)
        folder = service_run(env)
    data, d = do_score(env, folder)
    rep = data["report"]
    assert all(r["유실"] == r["n"] for r in rep["normalize"])  # 분모에서 빼지 않는다
    assert d["N1"]["reason"] == "발행 실패"
    assert all(r["보존"] == 0 for r in rep["review"])
    assert rep["불필요 변경"]["그 밖의 셀 변경"]["유실"] == rep["불필요 변경"]["그 밖의 셀 변경"]["분모"]
    assert rep["0건 조건"]["감사 통과"] is None  # 발행 실패 시도에는 감사 기록이 남지 않는다


def test_excluded_table_and_missing_upload_are_lost(env):
    """발행 제외 테이블·업로드 안 한 파일도 유실로 센다."""
    def drop(maps):
        maps[0]["columns"]["고객번호"] = None  # 필수 컬럼 없음 → customers 발행 제외
    data, d = do_score(env, service_run(env, ("customers.csv",), drop))
    assert d["N1"]["category"] == "유실" and d["N1"]["reason"] == "발행 제외 테이블"
    assert d["K2"]["category"] == "유실" and d["K2"]["reason"] == "업로드 안 됨"
    assert d["H1"]["final"] == "미완료" and d["H1"]["auto"] == "정답"


def test_mapping_error_follows_the_value(env):
    """잘못 매핑한 셀은 그 값이 간 컬럼에서 판정하고, 매핑 위치 차이를 따로 센다."""
    def swap(maps):
        cols = maps[0]["columns"]
        cols["이메일"], cols["휴대폰"] = "phone", "email"
    data, d = do_score(env, service_run(env, edit=swap))
    assert d["N1"]["mapped_elsewhere"] == "email"
    assert data["report"]["0건 조건"]["매핑이 정답 컬럼과 다른 사례"] >= 2


def test_unmapped_column_value_is_kept_in_extras(env):
    def unmap(maps):
        maps[0]["columns"]["이메일"] = None  # 선택 컬럼 → 추가 컬럼으로 남음
    _, d = do_score(env, service_run(env, edit=unmap))
    assert d["N4"]["category"] == "누락" and d["N4"]["place"]["column"] == "이메일"  # 원래 이름 컬럼의 원문


def test_same_pk_rows_matched_by_position(env):
    """PK가 같은 두 행은 키가 아니라 원본 위치로 맞춘다(④ 5-2)."""
    data, d = do_score(env, service_run(env))
    assert d["R3"]["place"]["record"] != d["R4"]["place"]["record"]
    assert d["R3"]["place"]["record"] == 5 and d["R4"]["place"]["record"] == 6


def test_merged_inputs_located_through_lineage(env):
    """두 업로드 항목이 한 테이블로 합쳐져도 역추적 표로 원본 위치를 찾는다(통합 입력)."""
    folder = service_run(env, ("customers.csv",))
    b = load_bundle(folder)
    second = dict(b.inputs[0], item_id=99, name="customers-2.csv")
    lineage = json.loads(json.dumps(b.lineage))
    rows = lineage["tables"]["customers"]["rows"]
    rows.append({"item_id": 99, "source_record": 1, "record": len(rows) + 1, "extras": {}})
    result = {t: {"header": v["header"], "rows": v["rows"] + [v["rows"][0][:]]} for t, v in b.result.items()}
    result["customers"]["rows"][-1][result["customers"]["header"].index("phone")] = "010-0000-0001"
    merged = Bundle(b.folder, b.meta, b.inputs + [second], b.mapping | {99: b.mapping[1] | {"item_id": 99}},
                    lineage, b.diagnosis, b.changes, b.held, b.not_approved, result)
    placer = Placer(merged)
    assert placer.place("customers-2.csv", 1, "휴대폰").value == "010-0000-0001"
    assert placer.place("customers-2.csv", 1, "휴대폰").record == len(rows)
    assert placer.place("customers.csv", 1, "휴대폰").value == "010-1234-5678"
    norm = load_cases_from(CASES)["N1"]
    assert judge_normalize(norm, placer.place("customers.csv", 1, "휴대폰"), Evidence.of(merged))["category"] == "정답"


def test_report_files_and_markdown(env):
    data, _ = do_score(env, service_run(env))
    head = data["header"]
    assert head["채점기"].startswith("judge-v1") and head["분할"] == "development"
    assert head["안내"].startswith("합성 벤치마크") and head["설정 스냅샷"]
    md = (env["tmp"] / "score" / "score.md").read_text(encoding="utf-8")
    assert "합성 벤치마크" in md and "셀 표준화 (normalize)" in md and "R02" in md
    assert "| 범주 | 건수 | 분모 | 유실 |" in md  # 그 밖의 셀 변경의 유실 열도 표에 나온다


def test_evaluation_split_needs_confirm_final(env, capsys):
    assert scorer.main([str(env["tmp"]), "--split", "evaluation"]) == 2
    assert "confirm-final" in capsys.readouterr().err


def test_score_dev_pipeline_runs_service_headless(env):
    """M2-4 도구: 화면 없이 서비스 흐름(자동 매핑 확정·미리 선택 승인)으로 처리한 실행을 채점한다."""
    from prep.export import export_run
    from prep.preview import Workspace
    from tools.score_dev import run_service
    ws = Workspace(env["tmp"] / "ws2")
    rid = run_service(ws, env["inputs"] / "development")
    assert ws.load(rid)["status"] == "completed_with_issues"
    data, d = do_score(env, export_run(ws, rid))
    assert d["N1"]["category"] == "정답" and data["header"]["실행"] == rid
