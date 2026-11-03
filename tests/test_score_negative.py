"""M2-3 AC-14-2 음성 사례·AC-14-4 재현성. 합성 입력·합성 정답만 쓴다."""

import copy
import hashlib
import json
import os
import shutil

import pytest

from evaluation import score as scorer
from evaluation.answers import AnswerError
from evaluation.bundle import BundleError, Evidence, load_bundle
from evaluation.judge import Placer, judge_review
from tests.score_helpers import CASES, do_score, load_cases_from, make_env, service_run, write_answers


@pytest.fixture
def env(tmp_path):
    return make_env(tmp_path)


def rewrite(env, cases):
    shutil.rmtree(env["answers"])
    write_answers(env["answers"], cases)


def edited(cid, **changes):
    cases = copy.deepcopy(CASES)
    for c in cases:
        if c["case_id"] == cid:
            c.update(changes)
    return cases


# ---- 정답 변조 사본 ----

def test_tampered_observed_value_refused(env):
    folder = service_run(env)
    rewrite(env, edited("N1", observed_value="01099999999"))
    with pytest.raises(AnswerError, match="관측값이 입력과 다르다"):
        do_score(env, folder)


def test_tampered_case_count_refused(env):
    """사례를 빼고 manifest는 그대로 둔 사본(누락 변조)."""
    folder = service_run(env)
    manifest = (env["answers"] / "manifest.json").read_text(encoding="utf-8")
    rewrite(env, [c for c in CASES if c["case_id"] != "N8"])
    (env["answers"] / "manifest.json").write_text(manifest, encoding="utf-8")
    with pytest.raises(AnswerError, match="manifest"):
        do_score(env, folder)


@pytest.mark.parametrize("cid,changes,message", [
    ("N2", {"expected_value": "ACTIVE"}, "normalize 기대값이 관측값과 같다"),
    ("K2", {"expected_value": "CAT000001"}, "keep 기대값이 관측값과 다르다"),
    ("H1", {"expected_value": "고객번호_x"}, "표준 컬럼이 아니다"),
    ("N1", {"expected_action": "fix"}, "알 수 없는 행동"),
    ("N1", {"case_id": "N2"}, "사례 ID가 겹친다"),
])
def test_tampered_semantics_refused(env, cid, changes, message):
    folder = service_run(env)
    rewrite(env, edited(cid, **changes))
    with pytest.raises(AnswerError, match=message):
        do_score(env, folder)


def test_answer_hash_pinned(env, tmp_path):
    """고정 기록의 SHA256SUMS와 다르면(기대값만 바꾼 변조) 채점하지 않는다."""
    folder = service_run(env)
    sums = tmp_path / "SHA256SUMS"
    sha = hashlib.sha256((env["answers"] / "development/cases.jsonl").read_bytes()).hexdigest()
    sums.write_text(f"{sha}  development/cases.jsonl\n", encoding="utf-8")
    do_score(env, folder, answer_sums=sums)  # 원본은 통과
    rewrite(env, edited("N1", expected_value="010-1234-0000"))
    with pytest.raises(AnswerError, match="고정 기록과 다르다"):
        do_score(env, folder, "score2", answer_sums=sums)


def test_review_with_expected_value_refused(env):
    """review 사례에 복원값(기대값)을 넣은 정답 사본."""
    folder = service_run(env)
    rewrite(env, edited("R2", expected_value="010-8888-8888"))
    with pytest.raises(AnswerError, match="review 사례에 기대값"):
        do_score(env, folder)


# ---- review 복원값 주입 ----

def test_review_restore_in_result_is_not_preserved(env):
    """결과에 review 셀의 복원값을 넣으면 보존 실패이고, 복원했다고 점수를 주지 않는다."""
    b = load_bundle(service_run(env))
    phone = b.result["customers"]["header"].index("phone")
    b.result["customers"]["rows"][7][phone] = "010-8888-8888"  # R2(레코드 8)의 원값 '복원'
    case = load_cases_from(CASES)["R2"]
    placer = Placer(b)
    got = judge_review(case, placer.place(case.input_file, case.record, case.column), Evidence.of(b),
                       {"customers": ["customer_id"]})
    assert got == {"detected": True, "preserved": False}


def test_tampered_export_refused(env):
    folder = service_run(env)
    target = folder / "result" / "customers.csv"
    os.chmod(target, 0o644)
    target.write_bytes(target.read_bytes().replace(b",0,", b",010-0000-0000,", 1))
    with pytest.raises(BundleError, match="바뀌었다"):
        do_score(env, folder)


def test_extra_file_in_export_refused(env):
    folder = service_run(env)
    os.chmod(folder, 0o755)
    (folder / "result" / "몰래.csv").write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(BundleError, match="목록과 실제 파일이 다르다"):
        do_score(env, folder)


# ---- 정답의 입력 폴더 유출 ----

def test_answer_file_in_input_folder(env):
    folder = service_run(env)
    shutil.copy(env["answers"] / "development/cases.jsonl", env["inputs"] / "development/cases.jsonl")
    with pytest.raises(scorer.IsolationError):
        do_score(env, folder)


def test_answer_renamed_as_csv_in_input_folder(env):
    folder = service_run(env)
    shutil.copy(env["answers"] / "development/cases.jsonl", env["inputs"] / "development/orders.csv")
    with pytest.raises(scorer.IsolationError):
        do_score(env, folder)


def test_answer_uploaded_to_service(env):
    """정답 파일을 CSV 이름으로 바꿔 서비스에 올린 실행(서비스는 막지 못함, ③ 11-1 보장 범위)을 채점기가 막는다."""
    leak = env["tmp"] / "leak"
    leak.mkdir()
    shutil.copy(env["answers"] / "development/cases.jsonl", leak / "returns.csv")
    client = env["client"]
    files = [("files", (n, (env["inputs"] / "development" / n).read_bytes(), "text/csv"))
             for n in ("customers.csv",)] + [("files", ("returns.csv", (leak / "returns.csv").read_bytes(), "text/csv"))]
    run = client.post("/api/runs", files=files, data={"set": "shop", "source": "s"}).json()
    from prep.export import export_run
    from prep.preview import Workspace
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"] if not f["read_error"]]
    client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps})
    client.post(f"/api/runs/{run['id']}/diagnose")
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]})
    folder = export_run(Workspace(env["ws"]), run["id"])
    with pytest.raises(scorer.IsolationError, match="정답 파일이 섞였다"):
        do_score(env, folder)


def test_service_input_differs_from_scoring_input(env):
    folder = service_run(env)
    p = env["inputs"] / "development/customers.csv"
    p.write_text(p.read_text(encoding="utf-8") + "C10,가상고객010,c10@example.com,,active,false,,\n",
                 encoding="utf-8")
    with pytest.raises(BundleError, match="채점 입력이 다르다"):
        do_score(env, folder)


# ---- AC-14-4 재현성 ----

def test_same_result_same_table(env):
    folder = service_run(env)
    a, da = do_score(env, folder, "a")
    b, db = do_score(env, folder, "b")
    assert a["table_digest"] == b["table_digest"] and a["report"] == b["report"] and da == db
    assert json.dumps(a["header"]) != "" and a["header"]["채점 시각"]  # 머리말은 비교에서 뺀다
