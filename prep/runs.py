"""실행 수명주기와 승인 해시 (docs/technical-design.md 4-2·8-1절, FS F06·F07, M1-10a).

실행 승인은 해시 7종(설정·입력·매핑·실행 계획·뷰·후보·적용 계획)을 모두 저장한다. 하나라도 현재와 다르면
승인하지 않고, 이후 단계가 다시 만들어지면 이전 승인을 무효로 표시한다(AC-06-4, AC-07-6).
모든 기록은 덧붙이기만 한다(prep/store.py 트리거). 승인에서 바꿀 수 있는 것은 무효화 표시뿐이다.
"""

from __future__ import annotations

import hashlib
import json

from prep.store import Store

HASHES = ("config_hash", "input_hash", "mapping_hash", "plan_hash", "view_hash", "candidate_hash", "apply_hash")
LABELS = {"config_hash": "설정", "input_hash": "실행 대상", "mapping_hash": "매핑", "plan_hash": "실행 계획",
          "view_hash": "표준화 뷰", "candidate_hash": "표준화 후보", "apply_hash": "적용 선택"}


def _names(keys: list[str]) -> str:
    return ", ".join(LABELS.get(k, k) for k in keys)


class ApprovalError(ValueError):
    def __init__(self, message: str, mismatched: list[str] | None = None):
        super().__init__(message)
        self.mismatched = mismatched or []


def _h(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def mapping_hash(final: dict) -> str:
    """확정 매핑(항목 → 표준 테이블, 원래 컬럼 → 논리 컬럼/추가 컬럼)."""
    return _h({str(k): v for k, v in final.items()})


class RunLedger:
    def __init__(self, store: Store):
        self.store = store
        self.conn = store.conn

    # ---- 시작·대상 ----
    def start(self, batch_id: int, config_snapshot_id: int, retry_of: int | None = None) -> int:
        return self.store.create_run(batch_id, config_snapshot_id, retry_of)

    def set_targets(self, run_id: int, targets: list[dict]) -> None:
        with self.store.tx():
            before = self.input_hash(run_id)
            self.store.set_targets(run_id, targets)
            if self.input_hash(run_id) != before:  # 실제로 바뀐 경우만(같은 대상 재확정은 매핑 단계가 판단)
                self._invalidate(run_id, ("structure", "execution"), "targets_changed")

    def input_hash(self, run_id: int) -> str | None:
        """실행 대상(업로드 항목 ID, 원본 해시, 시트, 읽기 조건)을 순서대로."""
        rows = self.conn.execute(
            "SELECT t.order_no, t.included, t.exclusion_reason, u.id, u.blob_sha256, u.sheet, u.read_options "
            "FROM run_target t JOIN upload_item u ON u.id = t.upload_item_id WHERE t.run_id = ? AND t.version = "
            "(SELECT MAX(version) FROM run_target WHERE run_id = ?) ORDER BY t.order_no", (run_id, run_id)).fetchall()
        if not rows:
            return None
        return _h([[r["order_no"], r["included"], r["exclusion_reason"], r["id"], r["blob_sha256"], r["sheet"],
                    json.loads(r["read_options"])] for r in rows])

    # ---- 매핑(구조 승인) ----
    def record_mapping(self, run_id: int, auto_proposal: dict, final: dict) -> tuple[int, str]:
        digest = mapping_hash(final)
        with self.store.tx() as c:
            last = c.execute("SELECT hash FROM mapping_version WHERE run_id = ? ORDER BY version DESC LIMIT 1",
                             (run_id,)).fetchone()
            version = c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM mapping_version WHERE run_id = ?",
                                (run_id,)).fetchone()[0]
            mid = c.execute("INSERT INTO mapping_version (run_id, version, auto_proposal, final, hash) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (run_id, version, json.dumps(auto_proposal, ensure_ascii=False),
                             json.dumps(final, ensure_ascii=False), digest)).lastrowid
            if last is None or last["hash"] != digest:
                self._invalidate(run_id, ("structure", "execution"), "mapping_changed")
            else:
                self._invalidate(run_id, ("structure",), "mapping_reconfirmed")
            c.execute("INSERT INTO approval (run_id, kind, subject_id, input_hash, mapping_hash, config_hash) "
                      "VALUES (?, 'structure', ?, ?, ?, ?)",
                      (run_id, mid, self.input_hash(run_id), digest, self.config_hash(run_id)))
        return mid, digest

    def latest_mapping(self, run_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM mapping_version WHERE run_id = ? ORDER BY version DESC LIMIT 1",
                              (run_id,)).fetchone()
        return None if r is None else dict(r)

    # ---- 계획·뷰·적용 계획 ----
    def record_plan(self, run_id: int, plan: dict) -> int:
        m = self.latest_mapping(run_id)
        if m is None:
            raise ApprovalError("매핑을 먼저 확정해야 한다")
        with self.store.tx() as c:
            pid = c.execute("INSERT INTO run_plan (run_id, mapping_version_id, executed_rules, skipped_rules, tables,"
                            " hash) VALUES (?, ?, ?, ?, ?, ?)",
                            (run_id, m["id"], json.dumps(plan["executed"], ensure_ascii=False),
                             json.dumps(plan["skipped"], ensure_ascii=False),
                             json.dumps(plan["tables"], ensure_ascii=False), plan["hash"])).lastrowid
            self._invalidate(run_id, ("execution",), "plan_regenerated")
        return pid

    def record_view(self, run_id: int, path: str, view_hash: str) -> int:
        plan = self._latest("run_plan", run_id)
        if plan is None:
            raise ApprovalError("실행 계획이 먼저 필요하다")
        with self.store.tx() as c:
            vid = c.execute("INSERT INTO view_snapshot (run_id, run_plan_id, path, hash) VALUES (?, ?, ?, ?)",
                            (run_id, plan["id"], path, view_hash)).lastrowid
            self._invalidate(run_id, ("execution",), "view_regenerated")
        return vid

    def record_apply_plan(self, run_id: int, apply: dict) -> int:
        view = self._latest("view_snapshot", run_id)
        if view is None or view["hash"] != apply["view_hash"]:
            raise ApprovalError("적용 계획이 현재 뷰에 대한 것이 아니다", ["view_hash"])
        with self.store.tx() as c:
            aid = c.execute("INSERT INTO apply_plan (run_id, view_snapshot_id, candidates_hash, approved_groups,"
                            " excluded, allow, hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (run_id, view["id"], apply["candidates_hash"], json.dumps(apply["approved_groups"]),
                             json.dumps(apply["excluded_cells"]), json.dumps(apply["allow"]),
                             apply["hash"])).lastrowid
            self._invalidate(run_id, ("execution",), "selection_changed")
        return aid

    # ---- 해시·승인 ----
    def config_hash(self, run_id: int) -> str:
        return self.conn.execute("SELECT s.hash FROM run r JOIN config_snapshot s ON s.id = r.config_snapshot_id "
                                 "WHERE r.id = ?", (run_id,)).fetchone()[0]

    def current_hashes(self, run_id: int) -> dict[str, str | None]:
        m = self.latest_mapping(run_id)
        p, v, a = (self._latest(t, run_id) for t in ("run_plan", "view_snapshot", "apply_plan"))
        return {"config_hash": self.config_hash(run_id), "input_hash": self.input_hash(run_id),
                "mapping_hash": m and m["hash"], "plan_hash": p and p["hash"], "view_hash": v and v["hash"],
                "candidate_hash": a and a["candidates_hash"], "apply_hash": a and a["hash"]}

    def approve_execution(self, run_id: int, presented: dict) -> int:
        """화면이 보여 준 해시 7종으로 실행을 승인한다. 하나라도 현재와 다르면 거부(승인 행 없음)."""
        current = self.current_hashes(run_id)
        missing = [k for k in HASHES if current[k] is None]
        if missing:
            raise ApprovalError(f"아직 준비되지 않은 단계: {_names(missing)}", missing)
        mismatched = [k for k in HASHES if presented.get(k) != current[k]]
        if mismatched:
            raise ApprovalError(f"화면을 연 뒤 바뀌었다: {_names(mismatched)}. 최신 내용으로 다시 확인하세요", mismatched)
        structure = self.conn.execute(
            "SELECT 1 FROM approval WHERE run_id = ? AND kind = 'structure' AND invalidated_at IS NULL "
            "AND input_hash = ? AND mapping_hash = ? AND config_hash = ?",
            (run_id, current["input_hash"], current["mapping_hash"], current["config_hash"])).fetchone()
        if structure is None:
            raise ApprovalError("유효한 구조 승인(매핑 확정)이 없다", ["mapping_hash"])
        apply = self._latest("apply_plan", run_id)
        with self.store.tx() as c:
            self._invalidate(run_id, ("execution",), "superseded")
            return c.execute("INSERT INTO approval (run_id, kind, subject_id, " + ", ".join(HASHES) + ") VALUES "
                             "(?, 'execution', ?, " + ", ".join("?" * len(HASHES)) + ")",
                             (run_id, apply["id"], *(current[k] for k in HASHES))).lastrowid

    def check_execution(self, run_id: int, approval_id: int) -> dict:
        """실행 직전: 승인이 유효하고 현재 해시와 같은지 다시 본다(설정 파일 변경 포함, AC-07-6)."""
        a = self.conn.execute("SELECT * FROM approval WHERE id = ? AND run_id = ? AND kind = 'execution'",
                              (approval_id, run_id)).fetchone()
        if a is None or a["invalidated_at"] is not None:
            raise ApprovalError("유효한 실행 승인이 없다")
        current = self.current_hashes(run_id)
        mismatched = [k for k in HASHES if a[k] != current[k]]
        if mismatched:
            with self.store.tx():
                self._invalidate(run_id, ("execution",), "hash_mismatch:" + ",".join(mismatched))
            raise ApprovalError(f"승인 이후 바뀌었다: {_names(mismatched)}", mismatched)
        return dict(a)

    def approvals(self, run_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM approval WHERE run_id = ? ORDER BY id", (run_id,))]

    # ---- 내부 ----
    def _latest(self, table: str, run_id: int) -> dict | None:
        r = self.conn.execute(f"SELECT * FROM {table} WHERE run_id = ? ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
        return None if r is None else dict(r)

    def _invalidate(self, run_id: int, kinds: tuple[str, ...], reason: str) -> None:
        marks = ", ".join("?" * len(kinds))
        self.conn.execute(f"UPDATE approval SET invalidated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
                          f"invalidated_reason = ? WHERE run_id = ? AND invalidated_at IS NULL AND kind IN ({marks})",
                          (reason, run_id, *kinds))
