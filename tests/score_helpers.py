"""채점기 테스트용 합성 입력·합성 정답 (M2-2·M2-3). 개발·평가 정답을 읽지 않는다."""

import json
import tempfile
from collections import Counter
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from evaluation import score as scorer
from evaluation.answers import load_cases
from prep import export
from prep.preview import Workspace

ROOT = Path(__file__).resolve().parents[1]
T = "2026-06-01T09:00:00+09:00"
U = "2026-10-01T09:00:00+09:00"
HEAD = "고객번호,고객명,이메일,휴대폰,회원상태,마케팅동의,가입일시,수정일시"
CUSTOMERS = "\n".join([
    HEAD,
    f"C1,가상고객001,c1@example.com,01012345678,ACTIVE,false,{T},{U}",
    f"C2,,c2@example.com,010-1111-2222,active,true,2026/06/01 09:00:00+09:00,{U}",
    f"C3,가상고객003,NULL,010-3333-4444,active,false,{T},{U}",
    f"C4,가상고객004,c4@example.com,010-12,active,false,{T},{U}",
    f"C5,가상고객005,c5@example.com,,active,false,{T},{U}",
    f"C5,가상고객005,c5@example.com,,active,false,{T},{U}",
    f"C7, 가상고객007 ,c7@example.com,010-7777-7777,Active,false,{T},{U}",
    f"C8,가상고객008,c8@example.com,0,active,false,{T},{U}",
    f"C9,가상고객009,c9@example.com,010-9999-9999,ACTIVE,false,07/08/26 09:30,{U}",
]) + "\n"
CATEGORIES = "분류번호,상위분류번호,분류명,사용여부\nK1,,생활,TRUE\nK2,K1,주방,true\n"


def row(n):
    headers = HEAD.split(",")
    return dict(zip(headers, CUSTOMERS.splitlines()[n].split(","), strict=True))


def case(cid, action, file, rec, col, observed, expected=None, issue="x", group="seen", canonical=None):
    o = {"case_id": cid, "input_file": file, "record_number": rec, "column": col, "observed_value": observed,
         "clean_value": observed, "expected_action": action, "issue_type": issue, "pattern_group": group,
         "profile": "A", "reason": "", "canonical_column": canonical or col}
    if expected is not None:
        o["expected_value"] = expected
    return o


C = "customers.csv"
CASES = [
    case("H1", "map_column", C, 0, "고객번호", "고객번호", "customer_id", "header_alias", "unseen"),
    case("H2", "map_column", C, 0, "휴대폰", "휴대폰", "phone", "header_alias", "unseen"),
    case("H3", "map_column", C, 0, "회원상태", "회원상태", "customer_name", "header_alias", "unseen"),
    case("N1", "normalize", C, 1, "휴대폰", "01012345678", "010-1234-5678", "phone_format", canonical="phone"),
    case("N2", "normalize", C, 1, "회원상태", "ACTIVE", "active", "enum_case", canonical="customer_status"),
    case("N3", "normalize", C, 2, "가입일시", "2026/06/01 09:00:00+09:00", T, "datetime_format",
         canonical="joined_at"),
    case("N4", "normalize", C, 3, "이메일", "NULL", "", "null_token", canonical="email"),
    case("N5", "normalize", C, 7, "고객명", " 가상고객007 ", "가상고객007", "outer_whitespace",
         canonical="customer_name"),
    case("N6", "normalize", C, 7, "회원상태", "Active", "ACTIVE", "enum_case", "unseen", "customer_status"),
    case("N7", "normalize", C, 4, "휴대폰", "010-12", "010-1200-0000", "phone_format", canonical="phone"),
    case("N8", "normalize", C, 8, "마케팅동의", "false", "FALSE", "enum_case", canonical="marketing_consent"),
    case("R1", "review", C, 2, "고객명", "", issue="required_missing", canonical="customer_name"),
    case("R2", "review", C, 8, "휴대폰", "0", issue="truncated_phone", canonical="phone"),
    case("R3", "review", C, 5, "*", row(5), issue="duplicate_pk", canonical="*"),
    case("R4", "review", C, 6, "*", row(6), issue="duplicate_pk", canonical="*"),
    case("R5", "review", C, 9, "가입일시", "07/08/26 09:30", issue="ambiguous_date", canonical="joined_at"),
    case("R6", "review", C, 1, "마케팅동의", "false", issue="total_conflict", canonical="marketing_consent"),
    case("K1", "keep", C, 5, "휴대폰", "", "", "valid_optional_null", canonical="phone"),
    case("K2", "keep", "categories.csv", 1, "상위분류번호", "", "", "valid_root_null", canonical="parent_category_id"),
    case("K3", "keep", "categories.csv", 1, "사용여부", "TRUE", "TRUE", "valid_case", canonical="is_active"),
]


def write_answers(root: Path, cases: list[dict], split="development") -> None:
    (root / split).mkdir(parents=True)
    (root / split / "cases.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases),
                                              encoding="utf-8")
    manifest = {"splits": {split: {"cases": len(cases),
                                   "actions": dict(Counter(c["expected_action"] for c in cases)),
                                   "issue_types": dict(Counter(c["issue_type"] for c in cases))}}}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def make_env(tmp_path):
    inputs = tmp_path / "inputs" / "development"
    inputs.mkdir(parents=True)
    (inputs / "customers.csv").write_text(CUSTOMERS, encoding="utf-8")
    (inputs / "categories.csv").write_text(CATEGORIES, encoding="utf-8")
    write_answers(tmp_path / "answers", CASES)
    ws = tmp_path / "workspace"
    return {"tmp": tmp_path, "inputs": tmp_path / "inputs", "answers": tmp_path / "answers", "ws": ws,
            "client": TestClient(create_app(ws))}


def service_run(env, names=("customers.csv", "categories.csv"), edit=None):
    client = env["client"]
    files = [("files", (n, (env["inputs"] / "development" / n).read_bytes(), "text/csv")) for n in names]
    run = client.post("/api/runs", files=files, data={"set": "shop", "source": "s"}).json()
    maps = [{"item_id": f["item_id"], **f["mapping"]} for f in run["files"]]
    if edit:
        edit(maps)
    assert client.put(f"/api/runs/{run['id']}/mapping", json={"files": maps}).status_code == 200
    client.post(f"/api/runs/{run['id']}/diagnose")
    p = client.get(f"/api/runs/{run['id']}/preview").json()
    client.post(f"/api/runs/{run['id']}/execute", json={"approval": p["approval"]})
    return export.export_run(Workspace(env["ws"]), run["id"])


def do_score(env, folder, out_name="score", **kw):
    out = scorer.run(folder, "development", answers_root=env["answers"], inputs_root=env["inputs"],
                     out=env["tmp"] / out_name, condition="rules-only", **kw)
    data = json.loads((out / "score.json").read_text(encoding="utf-8"))
    details = {json.loads(line)["case_id"]: json.loads(line)
               for line in (out / "cases.jsonl").read_text(encoding="utf-8").splitlines()}
    return data, details


def load_cases_from(cases):
    path = Path(tempfile.mkdtemp()) / "c.jsonl"
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    return {c.case_id: c for c in load_cases(path)}
