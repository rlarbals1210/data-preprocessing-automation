"""재사용 규칙 저장·승인 (FS F09, TD 10-2, 결정 M6-D1, M6-5).

- 재사용 규칙은 비교·재사용 단위(양식)별·버전별로 저장한다. 종류 두 가지:
  매핑 규칙(`mapping`, 원래 헤더 → 논리 컬럼) · 표준화 규칙(`standardize`, 논리 컬럼·변환 ID·승인 형태 서명 집합).
- 승인은 최종 확인(실행 승인) 때 그 실행의 `approval(kind=reuse)` 한 행과 규칙 행들로 기록한다.
  **효력은 그 실행이 정제 결과를 발행했을 때만** 생긴다(M6-D1) — 실패한 실행의 체크는 다음 실행 후보가 되지 않는다.
- 표준화 규칙의 서명은 이번 실행에서 실제로 적용될 셀(승인 묶음·제외 셀 반영 후 허용 셀)의 원문에서 만든다.
  변환 ID = 진단 코드 + (형식 변환·형식 통일이면 그 컬럼의 표준화 정책). 승인 당시 설정 해시를 함께 남긴다.
- 기록은 덧붙이기만 한다. 규칙 행에서 바꿀 수 있는 것은 승인 취소 표시(한 번)뿐이다(M6-7).
- 승인 취소는 효력 있는 규칙에만 한다. 그 키는 다음 실행부터 후보가 아니며 이전 버전이 되살아나지 않는다
  (취소한 버전이 키의 최신 발행 버전으로 남으므로). 과거 결과 버전은 바뀌지 않는다(AC-09-4).
"""

from __future__ import annotations

import json
from collections import defaultdict

from prep.apply import group_id
from prep.profile import signature
from prep.store import Store

POLICY_CODES = ("type", "pattern")  # 컬럼 표준화 정책(변환 등록표)을 거치는 진단 코드


class ReuseError(ValueError):
    pass


def transform_id(code: str, rule: dict) -> str:
    policy = rule.get("standardization")
    return f"{code}:{policy}" if code in POLICY_CODES and policy else code


def standardize_rules(groups: list[str], allow: list[list], cells: dict, rules: dict,
                      templates_of_item: dict[int, int]) -> list[dict]:
    """승인 묶음 중 재사용으로 고른 것 → 양식별 표준화 규칙.

    groups: 재사용 체크한 묶음 ID. allow: 적용 계획의 허용 셀 [table, record, column].
    cells: 적용 후보(apply.candidates) — 키 (table, record, column), 값 before·codes·source.
    rules: 표준 테이블 → 논리 컬럼 → 논리 규칙(설정 스냅샷). templates_of_item: 업로드 항목 → 양식.
    """
    wanted = set(groups)
    sigs: dict[tuple[int, str], set[str]] = defaultdict(set)
    meta: dict[tuple[int, str], dict] = {}
    for table, record, column in allow:
        v = cells[(table, record, column)]
        item = (v.get("source") or {}).get("item_id")
        for code in v["codes"]:
            gid = group_id(table, column, code)
            if gid not in wanted:
                continue
            if item not in templates_of_item:
                raise ReuseError(f"양식이 정해지지 않은 셀이다: {gid}")
            key = (templates_of_item[item], gid)
            sigs[key].add(signature(v["before"] or ""))
            meta[key] = {"table": table, "column": column, "code": code,
                         "transform_id": transform_id(code, rules.get(table, {}).get(column, {}))}
    missing = wanted - {gid for _, gid in sigs}
    if missing:
        raise ReuseError(f"적용되는 셀이 없는 묶음은 재사용 규칙으로 저장할 수 없다: {sorted(missing)}")
    out = []
    for (template_id, gid), s in sorted(sigs.items()):
        m = meta[(template_id, gid)]
        out.append({"template_id": template_id, "kind": "standardize", "rule_key": gid,
                    "transform_id": m["transform_id"],
                    "spec": {"table": m["table"], "column": m["column"], "code": m["code"],
                             "transform_id": m["transform_id"], "signatures": sorted(s)}})
    return out


def mapping_rules(items: list[int], files: dict[int, dict], templates_of_item: dict[int, int]) -> list[dict]:
    """재사용으로 고른 항목의 확정 매핑 → 양식별 매핑 규칙. 같은 양식 항목이 여럿이면 매핑이 같아야 한다."""
    by_template: dict[int, dict] = {}
    for item in sorted(set(items)):
        f = files.get(item)
        if f is None or item not in templates_of_item or not f["mapping"].get("table"):
            raise ReuseError(f"실행 대상 파일이 아니다: {item}")
        spec = {"table": f["mapping"]["table"], "columns": dict(sorted(f["mapping"]["columns"].items()))}
        tid = templates_of_item[item]
        if tid in by_template and by_template[tid] != spec:
            raise ReuseError(f"같은 양식의 파일 매핑이 서로 다르다: 양식 {tid}")
        by_template[tid] = spec
    return [{"template_id": tid, "kind": "mapping", "rule_key": "mapping", "transform_id": None, "spec": spec}
            for tid, spec in sorted(by_template.items())]


def record(store: Store, run_id: int, rules: list[dict], hashes: dict[str, str | None]) -> int | None:
    """재사용 승인 한 행 + 규칙 행들(키마다 다음 버전). 규칙이 없으면 아무것도 남기지 않는다."""
    if not rules:
        return None
    with store.tx() as c:
        aid = c.execute("INSERT INTO approval (run_id, kind, input_hash, mapping_hash, config_hash) "
                        "VALUES (?, 'reuse', ?, ?, ?)",
                        (run_id, hashes.get("input_hash"), hashes.get("mapping_hash"), hashes["config_hash"])
                        ).lastrowid
        for r in rules:
            version = c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM reuse_rule WHERE template_id = ? AND "
                                "kind = ? AND rule_key = ?", (r["template_id"], r["kind"], r["rule_key"])).fetchone()[0]
            c.execute("INSERT INTO reuse_rule (template_id, kind, rule_key, version, spec, transform_id, config_hash,"
                      " approval_id, approved_in_run) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (r["template_id"], r["kind"], r["rule_key"], version,
                       json.dumps(r["spec"], ensure_ascii=False, sort_keys=True), r["transform_id"],
                       hashes["config_hash"], aid, run_id))
        return aid


_PUBLISHED = "EXISTS (SELECT 1 FROM result_version v WHERE v.run_id = r.approved_in_run AND v.kind = 'cleaned')"


def for_template(store: Store, template_id: int) -> list[dict]:
    """양식의 모든 규칙 버전과 상태. 상태: effective(효력) · superseded(새 버전에 밀림) · pending(그 실행이 아직
    발행 전이거나 실패 — 효력 없음) · revoked(승인 취소)."""
    rows = [dict(r) | {"spec": json.loads(r["spec"]), "published": bool(r["published"])} for r in store.conn.execute(
        f"SELECT r.*, {_PUBLISHED} AS published FROM reuse_rule r WHERE r.template_id = ? "
        "ORDER BY r.kind, r.rule_key, r.version", (template_id,))]
    latest: dict[tuple[str, str], int] = {}
    for r in rows:
        if r["published"]:
            latest[(r["kind"], r["rule_key"])] = max(latest.get((r["kind"], r["rule_key"]), 0), r["version"])
    for r in rows:
        if r["revoked_at"]:
            r["state"] = "revoked"
        elif not r["published"]:
            r["state"] = "pending"
        else:
            r["state"] = "effective" if latest[(r["kind"], r["rule_key"])] == r["version"] else "superseded"
    return rows


def effective(store: Store, template_id: int) -> list[dict]:
    """다음 실행에서 미리 선택에 쓸 수 있는 규칙(M6-6이 사용). 키마다 발행된 최신 버전이 취소되지 않았을 때만."""
    return [r for r in for_template(store, template_id) if r["state"] == "effective"]


def get(store: Store, rule_id: int) -> dict | None:
    row = store.conn.execute("SELECT template_id FROM reuse_rule WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        return None
    return next(r for r in for_template(store, row["template_id"]) if r["id"] == rule_id)


def revoke(store: Store, rule_id: int, reason: str) -> dict:
    """효력 있는 규칙의 승인 취소(한 번, 사유 필수). 취소 event는 DB 트리거가 남긴다."""
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason or len(reason) > 500:
        raise ReuseError("승인 취소 사유는 1~500자다")
    rule = get(store, rule_id)
    if rule is None:
        raise ReuseError("없는 재사용 규칙이다")
    if rule["state"] != "effective":
        raise ReuseError(f"효력 있는 규칙만 승인 취소할 수 있다: {rule['state']}")
    with store.tx() as c:
        c.execute("UPDATE reuse_rule SET revoked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), revoked_reason = ? "
                  "WHERE id = ?", (reason, rule_id))
    return get(store, rule_id)
