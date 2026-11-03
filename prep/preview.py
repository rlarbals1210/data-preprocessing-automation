"""웹 실행 흐름 (2026-11-02 규민님 요청으로 M3 화면을 먼저 만들고 M1 모듈로 하나씩 바꿔 끼우는 중).

업로드(M1-4) → 매핑 제안·구조 승인(M1-5) → 실행 계획(M1-6) → 표준화 뷰(M1-7) → 진단 → 적용 계획(M1-9)
→ 실행 승인(해시 7종, M1-10a) → 정제. 실행 상태·승인은 DB(prep/runs.py)가 정본이고,
run.json에는 화면용 파일 목록·매핑 초안·결과 요약만 둔다. 결과 발행(result_version)은 M1-10c에서 바꾼다.
모든 경로는 guard를 거치고, 설정은 스냅샷에서만 읽는다.
"""

from __future__ import annotations

import csv
import io
import json
import re
import secrets
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from diagnostics.engine import diagnose, read_table
from prep import attempt as attempts
from prep import publish
from prep.apply import ApplyError, build_apply_plan, group_id, verify_plan
from prep.apply import candidates as apply_candidates
from prep.apply import groups as apply_groups
from prep.guard import Guard
from prep.mapping import Catalog, propose, validate_final
from prep.plan import build_plan
from prep.runs import ApprovalError, RunLedger
from prep.snapshot import SnapshotError, create_snapshot, load_snapshot
from prep.store import Store
from prep.upload import BlobStore, UploadError, ingest
from prep.view import Locator, ViewInput, build_view

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETS = {"shop": "쇼핑몰 표준", "none": "설정 없음"}
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")
FILE_NAME = re.compile(r"[A-Za-z0-9가-힣_.-]{1,100}\.csv")
FINISHED = {"completed", "completed_with_issues", "failed"}
EVIDENCE_FILES = ("changes.json", "diagnosis-before.json", "diagnosis-after.json", "held.json", "absent-cells.json",
                  "manifest.json")  # 내부 검토용 근거 파일(FS F07 출력)


class FlowError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class Workspace:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.guard = Guard([self.root], project_root=PROJECT_ROOT)
        self.runs = self.guard.safe_join(self.root, "runs")
        self.runs.mkdir(exist_ok=True)
        self.store = Store(self.root, self.guard)
        self.blobs = BlobStore(self.root, self.guard)
        self.ledger = RunLedger(self.store)

    # ---- 저장 ----
    def run_dir(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id or ""):
            raise FlowError("run_not_found", "실행을 찾을 수 없다", 404)
        path = self.guard.safe_join(self.runs, run_id)
        if not (path / "run.json").is_file():
            raise FlowError("run_not_found", "실행을 찾을 수 없다", 404)
        return path

    def load(self, run_id: str) -> dict:
        run = json.loads((self.run_dir(run_id) / "run.json").read_text(encoding="utf-8"))
        if "db_run" not in run:
            raise FlowError("legacy_run", "실행 기록(M1-10a) 이전에 만든 실행이다. 새로 업로드하세요", 410)
        db = self.store.run(run["db_run"])
        run["status"], run["status_reason"] = db["status"], db["status_reason"]  # 상태 정본은 DB
        return run

    def _transition(self, run: dict, to: str, reason: str | None = None) -> None:
        if run["status"] != to:
            self.store.transition(run["db_run"], to, reason)
            run["status"] = to

    def _open(self, run: dict) -> None:
        if run["status"] in FINISHED:
            raise FlowError("finished_run", "끝난 실행이다. 새로 업로드하거나 다시 시도하세요", 409)

    def view(self, run_id: str) -> dict:
        """화면용: 실행 + 표준 목록(테이블 → 컬럼, 필수 컬럼) + 승인 기록."""
        run = self.load(run_id)
        run["approvals"] = [{k: a[k] for k in ("id", "kind", "created_at", "invalidated_at", "invalidated_reason")}
                            for a in self.ledger.approvals(run["db_run"])]
        cat = self._catalog(run)
        run["catalog"] = None if cat is None else {
            t: {"columns": cols, "required": cat.required.get(t, [])} for t, cols in cat.columns.items()}
        return run

    def _catalog(self, run: dict) -> Catalog | None:
        if not run["snapshot"]:
            return None
        snap = self.guard.safe_join(self.root, "config-snapshots", run["snapshot"])
        read = lambda rel: json.loads((snap / rel).read_text(encoding="utf-8"))  # noqa: E731
        return Catalog(read("schema.json"), read("config/sets/shop/logical-diagnostics.json"),
                       read("config/sets/shop/aliases.json"))

    def _rows(self, f: dict) -> list[dict]:
        self.blobs.get(f["sha256"])
        table = read_table(self.blobs.path(f["sha256"]), f["read_options"]["encoding"],
                           f["read_options"]["delimiter"])
        return [r["values"] for r in table["rows"]]

    def save(self, run: dict) -> None:
        path = self.guard.safe_join(self.runs, run["id"], "run.json")
        tmp = path.with_suffix(".tmp")
        body = {k: v for k, v in run.items() if k not in ("status", "status_reason", "catalog", "approvals")}
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def list_runs(self) -> list[dict]:
        items = []
        for p in sorted(self.runs.iterdir(), reverse=True):
            if RUN_ID.fullmatch(p.name) and (p / "run.json").is_file():
                r = json.loads((p / "run.json").read_text(encoding="utf-8"))
                if "db_run" not in r:
                    continue  # 실행 기록 이전 실행은 목록에서 뺀다
                r["status"] = self.store.run(r["db_run"])["status"]
                items.append({k: r.get(k) for k in ("id", "created_at", "set", "source", "status", "rows", "result",
                                                    "retry_of", "retried_by")}
                             | {"file_count": len(r["files"])})
        return items

    # ---- 업로드 ----
    def create_run(self, set_name: str, files: list[tuple[str, bytes]], source: str = "기본 출처") -> dict:
        if set_name not in SETS:
            raise FlowError("bad_set", f"알 수 없는 설정: {set_name}")
        try:
            uploaded = ingest(self.store, self.blobs, source, files)  # M1-4: 원본 보관·업로드 항목 기록
        except UploadError as exc:
            raise FlowError("bad_upload", str(exc)) from exc
        now = datetime.now(UTC)
        run_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
        self.guard.safe_join(self.runs, run_id).mkdir()
        shop = create_snapshot(PROJECT_ROOT, "shop", self.root, self.guard)
        snap_id = self.store.add_config_snapshot(shop.hash, "shop", f"config-snapshots/{shop.hash}", list(shop.files))
        snapshot = shop if set_name == "shop" else None
        cat = self._catalog({"snapshot": snapshot.hash}) if snapshot else None
        entries = []
        for item in uploaded["items"]:
            f = {"item_id": item["id"], "name": item["filename"], "sha256": item["sha256"], "bytes": item["bytes"],
                 "read_options": {"encoding": "utf-8-sig", "delimiter": ","}, "rows": item["rows"],
                 "headers": item["headers"], "read_error": None, "proposal": None, "mapping": None,
                 "confirmed": False}
            if item["read_status"] != "ok":
                e = item["read_errors"][0]
                f["read_error"] = (f"{e['record']}번째 레코드: " if e.get("record") else "") + e["reason"]
                f["mapping"] = {"table": None, "columns": {}}
                f["confirmed"] = True  # 읽기 실패 파일은 실행 대상에서 빠진다(AC-01-3)
            elif cat is None:
                f["mapping"] = {"table": item["filename"][:-4], "columns": {h: h for h in item["headers"]}}
                f["confirmed"] = True  # 설정 없음: 프로파일만
            else:
                f["proposal"] = propose(item["filename"], item["headers"], self._rows(f), cat)
                f["mapping"] = _mapping_of(f["proposal"])
                f["confirmed"] = False  # 구조 승인은 매번 사용자가 한다
            entries.append(f)
        db_run = self.ledger.start(uploaded["batch_id"], snap_id)
        run = {"id": run_id, "created_at": now.isoformat(), "set": set_name, "source": source.strip(),
               "batch_id": uploaded["batch_id"], "db_run": db_run, "status": "uploaded",
               "snapshot": snapshot.hash if snapshot else None, "config_hash": shop.hash, "files": entries,
               "rows": sum(f.get("rows", 0) for f in entries), "plan": None, "result": None}
        if cat is None:  # 설정 없음: 고를 매핑이 없으므로 구조를 그대로 확정한다
            self._record_structure(run)
        else:
            self._transition(run, "needs_confirmation", "mapping")
        self.save(run)
        return run

    def _record_structure(self, run: dict) -> None:
        """실행 대상(순서·제외 사유)과 매핑 버전·구조 승인을 DB에 남긴다."""
        targets = [{"upload_item_id": f["item_id"], "included": bool(not f["read_error"] and f["mapping"]["table"]),
                    "exclusion_reason": None if not f["read_error"] and f["mapping"]["table"]
                    else ("read_error" if f["read_error"] else "user_excluded")} for f in run["files"]]
        self.ledger.set_targets(run["db_run"], targets)
        self.ledger.record_mapping(run["db_run"],
                                   {str(f["item_id"]): f["proposal"] for f in run["files"] if f["proposal"]},
                                   {str(f["item_id"]): f["mapping"] for f in run["files"]})

    # ---- 재시도 (M1-10d) ----
    def retry(self, run_id: str) -> dict:
        """실패한 실행을 같은 업로드 묶음으로 새 실행으로 다시 시도한다(FS 6절). 실패 기록은 그대로 둔다.
        설정은 지금의 스냅샷을 새로 쓰고, 매핑은 이전 확정 매핑을 초안으로 가져와 다시 확정받는다."""
        old = self.load(run_id)
        if old["status"] != "failed":
            raise FlowError("not_failed", "실패한 실행만 다시 시도할 수 있다", 409)
        shop = create_snapshot(PROJECT_ROOT, "shop", self.root, self.guard)
        snap_id = self.store.add_config_snapshot(shop.hash, "shop", f"config-snapshots/{shop.hash}", list(shop.files))
        db_run = self.ledger.start(old["batch_id"], snap_id, retry_of=old["db_run"])
        now = datetime.now(UTC)
        new_id = now.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
        self.guard.safe_join(self.runs, new_id).mkdir()
        files = []
        for f in old["files"]:
            g = json.loads(json.dumps(f))
            g["confirmed"] = bool(f["read_error"]) or old["snapshot"] is None
            files.append(g)
        run = {"id": new_id, "created_at": now.isoformat(), "set": old["set"], "source": old["source"],
               "batch_id": old["batch_id"], "db_run": db_run, "status": "uploaded",
               "snapshot": shop.hash if old["snapshot"] else None, "config_hash": shop.hash, "files": files,
               "rows": old["rows"], "plan": None, "result": None, "retry_of": old["id"]}
        if run["snapshot"] is None:
            self._record_structure(run)
        else:
            self._transition(run, "needs_confirmation", "retry")
        self.store.record_event("run", old["db_run"], "retried", {"new_run": db_run})
        self.save(run)
        old["retried_by"] = new_id
        self.save(old)
        return self.view(new_id)

    # ---- 매핑 (M1-5) ----
    def propose_table(self, run_id: str, item_id: int, table: str) -> dict:
        run = self.load(run_id)
        cat = self._catalog(run)
        f = next((x for x in run["files"] if x["item_id"] == item_id), None)
        if cat is None or f is None or f["read_error"] or table not in cat.columns:
            raise FlowError("bad_mapping", "제안할 수 없는 파일 또는 테이블")
        return propose(f["name"], f["headers"], self._rows(f), cat, table=table)

    def confirm_mapping(self, run_id: str, files: list[dict]) -> dict:
        """구조 승인: 사용자가 확인·수정한 매핑을 확정한다. 자동 제안(proposal)은 그대로 둔다(AC-03-2)."""
        run = self.load(run_id)
        self._open(run)
        cat = self._catalog(run)
        if cat is None:
            raise FlowError("bad_mapping", "설정 없음 실행은 매핑이 없다")
        by_id = {f["item_id"]: f for f in run["files"]}
        if {x.get("item_id") for x in files} != {f["item_id"] for f in run["files"] if not f["read_error"]}:
            raise FlowError("bad_mapping", "읽기에 성공한 모든 파일의 매핑이 필요하다")
        errors, targets = [], []
        for x in files:
            f = by_id[x["item_id"]]
            table, columns = x.get("table"), x.get("columns") or {}
            if table is None:
                continue  # 실행 대상에서 제외
            errs = validate_final(table, columns, f["headers"], cat)
            errors += [f"{f['name']}: {e}" for e in errs]
            targets.append(table)
        dup = sorted(t for t, n in Counter(targets).items() if n > 1)
        if dup:
            errors.append(f"같은 표준 테이블로 가는 파일이 여럿이다: {dup}")
        if not targets:
            errors.append("실행 대상 파일이 없다")
        if errors:
            raise FlowError("bad_mapping", "; ".join(errors))
        changed = False
        for x in files:
            f = by_id[x["item_id"]]
            new = {"table": x.get("table"), "columns": dict(x.get("columns") or {}) if x.get("table") else {}}
            changed = changed or new != f["mapping"]
            f.update(mapping=new, confirmed=True)
        if changed and run["status"] in ("diagnosed", "awaiting_approval"):  # AC-03-5: 이전 진단·승인 무효
            base = self.run_dir(run_id)
            for name in ("report.json", "engine-config.json", "apply-plan.json"):
                (base / name).unlink(missing_ok=True)
            if (base / "view").exists():
                _remove_tree(base / "view")
            run.update(plan=None, result=None)
            self._transition(run, "needs_confirmation", "mapping_changed")
        self._record_structure(run)  # 실행 대상·매핑 버전·구조 승인(이전 실행 승인은 무효화)
        self.store.record_event("batch", run["batch_id"], "mapping_confirmed",
                                {"run": run_id, "changed": changed,
                                 "auto_unchanged": all(f["mapping"] == _mapping_of(f["proposal"])
                                                       for f in run["files"] if f["proposal"])})
        self.save(run)
        return self.view(run_id)

    # ---- 진단 ----
    def diagnose(self, run_id: str) -> dict:
        run = self.load(run_id)
        self._open(run)
        base = self.run_dir(run_id)
        if not all(f["confirmed"] for f in run["files"]):
            raise FlowError("mapping_unconfirmed", "매핑을 확정해야 진단할 수 있다", 409)  # AC-03-3
        readable = [f for f in run["files"] if f["read_error"] is None and f["mapping"]["table"]]
        if not readable:
            raise FlowError("nothing_readable", "진단할 파일이 없다")
        tables = Counter(f["mapping"]["table"] for f in readable)
        dup = [t for t, n in tables.items() if n > 1]
        if dup:
            raise FlowError("duplicate_table", f"같은 표준 테이블로 가는 파일이 여럿이다: {dup}")
        view_dir = self.guard.safe_join(base, "view")
        if view_dir.exists():
            _remove_tree(view_dir)
        cat = self._catalog(run)
        inputs = [ViewInput(f["item_id"], f["mapping"]["table"], self.blobs.path(f["sha256"]), f["mapping"]["columns"],
                            order, f["read_options"]["encoding"], f["read_options"]["delimiter"], f["name"])
                  for order, f in enumerate(readable, start=1)]
        for f in readable:
            self.blobs.get(f["sha256"])  # 보관 원본 해시 대조
        try:
            built = build_view(inputs, view_dir, self.guard,  # M1-7 표준화 뷰·역추적
                               required=cat.required if cat else None, column_order=cat.columns if cat else None)
        except ValueError as exc:
            raise FlowError("view_failed", str(exc)) from exc
        view = built.folder
        plan = None
        config = {}
        if run["snapshot"]:
            snap = self.guard.safe_join(self.root, "config-snapshots", run["snapshot"])
            logical = json.loads((snap / "config/sets/shop/logical-diagnostics.json").read_text(encoding="utf-8"))
            present = {f["mapping"]["table"]: {v for v in f["mapping"]["columns"].values() if v} for f in readable}
            plan = build_plan(logical, present)  # M1-6
            config = plan["config"]
        (base / "apply-plan.json").unlink(missing_ok=True)  # 새 진단 → 이전 선택 무효
        plan_record = plan or {"executed": [], "skipped": [], "tables": {}, "hash": _empty_plan_hash()}
        self.ledger.record_plan(run["db_run"], plan_record)  # 이전 실행 승인 무효화
        self.ledger.record_view(run["db_run"], f"runs/{run_id}/view", built.hash)
        report = Locator(built.lineage).translate_report(diagnose(view, config))
        (base / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        self._transition(run, "diagnosed")
        run.update(result=None, view_hash=built.hash,
                   plan=None if plan is None else {k: plan[k] for k in ("skipped", "tables", "completeness", "hash")})
        (base / "engine-config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        self.save(run)
        return self.diagnosis(run_id)

    def _report(self, run_id: str) -> dict:
        path = self.run_dir(run_id) / "report.json"
        if not path.is_file():
            raise FlowError("not_diagnosed", "아직 진단하지 않았다", 409)
        return json.loads(path.read_text(encoding="utf-8"))

    def diagnosis(self, run_id: str) -> dict:
        run, report = self.load(run_id), self._report(run_id)
        per_table = defaultdict(Counter)
        for i in report["issues"]:
            per_table[i["table"]][i["category"]] += 1
        executed = Counter(r["executed"] for r in report["rule_execution"])
        return {
            "engine_status": report["status"],
            "mode": report["mode"],
            "rows_read": report["summary"]["rows_read"],
            "tables_read": report["summary"]["tables_read"],
            "category_counts": report["summary"]["category_counts"],
            "tables": [{"table": t, "rows": s["row_count"], "columns": s["column_count"],
                        "counts": dict(per_table[t])} for t, s in report["tables"].items()],
            "duplicate_groups": len(report["duplicate_groups"]),
            "rules": {"executed": executed[True], "not_executed": executed[False]},
            "skipped_rules": (run["plan"] or {}).get("skipped", []),
            "plan_tables": (run["plan"] or {}).get("tables", {}),
            "completeness": (run["plan"] or {}).get("completeness", "full"),
            "errors": report["errors"],
        }

    def issues(self, run_id: str, category: str | None, table: str | None, offset: int, limit: int) -> dict:
        items = self._report(run_id)["issues"]
        if category:
            items = [i for i in items if i["category"] == category]
        if table:
            items = [i for i in items if i["table"] == table]
        page = items[offset: offset + limit]
        keep = ("table", "record", "columns", "values", "code", "severity", "category", "suggested_value",
                "category_reason", "source", "detail")
        names = {f["item_id"]: f["name"] for f in self.load(run_id)["files"]}
        out = []
        for i in page:
            item = {k: i.get(k) for k in keep}
            if item["source"]:
                item["source"] = item["source"] | {"file": names.get(item["source"]["item_id"])}
            out.append(item)
        return {"total": len(items), "items": out}

    def duplicates(self, run_id: str, offset: int, limit: int) -> dict:
        """중복 그룹(SC05): 동일 행·키 충돌과 참여 행의 원본 위치."""
        groups = self._report(run_id)["duplicate_groups"]
        names = {f["item_id"]: f["name"] for f in self.load(run_id)["files"]}
        out = []
        for g in groups[offset: offset + limit]:
            out.append({k: g.get(k) for k in ("id", "table", "code", "kind", "columns", "differing_columns",
                                              "records", "participant_count")}
                       | {"sources": [s | {"file": names.get(s["item_id"])} for s in g.get("sources", [])]})
        return {"total": len(groups), "items": out}

    def source_row(self, run_id: str, item_id: int, record: int) -> dict:
        """원본 위치의 원문 행(AC-04-4): 보관 원본(해시 대조)을 다시 읽어 원래 헤더·값·물리 줄을 돌려준다."""
        run = self.load(run_id)
        f = next((f for f in run["files"] if f["item_id"] == item_id), None)
        if f is None or f["read_error"]:
            raise FlowError("source_not_found", "원본 파일이 없다", 404)
        self.blobs.get(f["sha256"])
        table = read_table(self.blobs.path(f["sha256"]), f["read_options"]["encoding"],
                           f["read_options"]["delimiter"])
        if not 1 <= record <= len(table["rows"]):
            raise FlowError("source_not_found", "원본에 없는 레코드다", 404)
        row = table["rows"][record - 1]
        mapping = (f.get("mapping") or {}).get("columns") or {}
        return {"file": f["name"], "item_id": item_id, "record": record, "line_start": row["line_start"],
                "line_end": row["line_end"],
                "cells": [{"original": h, "logical": mapping.get(h), "value": row["values"][h]}
                          for h in table["headers"]]}

    def _plan(self, run_id: str) -> dict:
        """저장된 적용 계획(없으면 전체 승인 기본값을 만들어 DB에 기록). M1-9·M1-10a."""
        run, report = self.load(run_id), self._report(run_id)
        path = self.run_dir(run_id) / "apply-plan.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        plan = build_apply_plan(report, run["view_hash"])
        if run["status"] not in FINISHED:
            self._store_plan(run, plan)
        return plan

    def _store_plan(self, run: dict, plan: dict) -> None:
        self.ledger.record_apply_plan(run["db_run"], plan)  # 이전 실행 승인 무효화
        path = self.guard.safe_join(self.run_dir(run["id"]), "apply-plan.json")
        path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    def changes_preview(self, run_id: str) -> dict:
        report = self._report(run_id)
        cells, held = apply_candidates(report)
        plan = self._plan(run_id)
        run = self.load(run_id)
        if run["status"] == "diagnosed":
            self._transition(run, "awaiting_approval")
        approved = set(plan["approved_groups"])
        excluded = {tuple(k) for k in plan["excluded_cells"]}
        out = []
        for g in apply_groups(cells, held):
            n = sum(1 for k in excluded if k in cells and g["code"] in cells[k]["codes"]
                    and group_id(k[0], k[2], g["code"]) == g["id"])
            out.append(g | {"approved": g["id"] in approved, "excluded": n})
        tables = (run.get("plan") or {}).get("tables", {})
        return {"groups": out, "total_cells": plan["counts"]["allowed"], "candidates": plan["counts"]["candidates"],
                "held": plan["counts"]["held"], "not_approved": plan["counts"]["not_approved"],
                "excluded_cells": plan["excluded_cells"], "plan_hash": plan["hash"], "view_hash": plan["view_hash"],
                "not_publishable": {t: v["reason"] for t, v in sorted(tables.items()) if not v["publishable"]},
                "approval": self.ledger.current_hashes(run["db_run"])}

    def preview_cells(self, run_id: str, gid: str, offset: int, limit: int) -> dict:
        """묶음 하나의 셀 목록(SC07): 원본 위치·전후 값·제외 여부, 보류 셀은 막는 위반·검토(AC-06-2)."""
        report = self._report(run_id)
        cells, held = apply_candidates(report)
        plan = self._plan(run_id)
        excluded = {tuple(k) for k in plan["excluded_cells"]}
        names = {f["item_id"]: f["name"] for f in self.load(run_id)["files"]}
        rows = []
        for bucket, is_held in ((cells, False), (held, True)):
            for key, v in bucket.items():
                if any(group_id(key[0], key[2], c) == gid for c in v["codes"]):
                    rows.append((key, v, is_held))
        if not rows:
            raise FlowError("group_not_found", "현재 후보에 없는 규칙 묶음이다", 404)
        rows.sort(key=lambda r: (r[2], r[0][1]))
        blocking = defaultdict(list)
        if any(r[2] for r in rows):
            for i in report["issues"]:
                if i["category"] in ("business_rule_violation", "human_review") and i["code"] != "duplicate_row":
                    for c in i["columns"]:
                        blocking[(i["table"], i["record"], c)].append({"category": i["category"], "code": i["code"]})
        items = []
        for key, v, is_held in rows[offset: offset + limit]:
            source = v.get("source") or {}
            items.append({"cell": list(key), "before": v["before"], "after": "" if v["after"] is None else v["after"],
                          "null": v["after"] is None, "codes": v["codes"],
                          "source": source | {"file": names.get(source.get("item_id"))} if source else None,
                          "held": is_held, "excluded": key in excluded,
                          "reasons": blocking.get(key, []) if is_held else []})
        return {"total": len(rows), "held": sum(1 for r in rows if r[2]), "items": items}

    def select(self, run_id: str, selection: dict) -> dict:
        """사용자 선택 → 서버가 검증한 적용 계획(T-08)."""
        run, report = self.load(run_id), self._report(run_id)
        self._open(run)
        try:
            plan = build_apply_plan(report, run["view_hash"], selection)
        except (ApplyError, TypeError, ValueError) as exc:
            raise FlowError("bad_selection", str(exc)) from exc
        self._store_plan(run, plan)
        return self.changes_preview(run_id)

    # ---- 실행 ----
    def execute(self, run_id: str, presented: dict) -> dict:
        """실행 승인(화면이 보여 준 해시 7종) → 재확인 → 정제. AC-06-4·AC-07-6."""
        run = self.load(run_id)
        self._open(run)
        if run["status"] != "awaiting_approval":
            raise FlowError("not_ready", "미리보기를 먼저 확인해야 한다", 409)
        base = self.run_dir(run_id)
        report = self._report(run_id)
        plan = self._plan(run_id)
        try:
            approval = self.ledger.approve_execution(run["db_run"], presented or {})
            load_snapshot(self.root, run["config_hash"], self.guard)  # 승인 뒤 설정 스냅샷 변조 확인
            self.ledger.check_execution(run["db_run"], approval)
            verify_plan(plan, report, run["view_hash"])  # 승인 때와 뷰·후보가 같은지
        except ApprovalError as exc:
            raise FlowError("approval_mismatch", str(exc), 409) from exc
        except (SnapshotError, ApplyError) as exc:
            raise FlowError("stale_plan", str(exc), 409) from exc
        config = json.loads((base / "engine-config.json").read_text(encoding="utf-8"))
        view = self.guard.safe_join(base, "view")
        attempt = attempts.start(self.store, self.guard, run["db_run"], base, approval)  # M1-10b 새 시도 폴더
        stage = "engine_failed"
        try:
            out = attempts.run_engine(attempt, view, config, plan["allow"])  # 엔진 확장 ③: 허용 셀만
            if out["changed_cells"] != plan["counts"]["allowed"]:
                raise ValueError("실행 변경 건수가 적용 계획과 다르다")  # AC-06-5
            stage = "audit_failed"
            for f in run["files"]:
                if not f["read_error"]:
                    self.blobs.get(f["sha256"])  # 보관 원본 해시 대조
            audit = attempts.audit(attempt, view, self.guard, plan["allow"], run["view_hash"])  # AC-07-1
            remaining = out["after_categories"]
            status = "completed_with_issues" if remaining.get("business_rule_violation") or remaining.get(
                "human_review") else "completed"
            stage = "publish_failed"  # M1-10c: 결과 파일 → DB 트랜잭션 하나
            lineage = json.loads((view / "lineage.json").read_text(encoding="utf-8"))
            manifest = publish.write_files(attempt, lineage, (run.get("plan") or {}).get("tables", {}),
                                           {f["item_id"]: f["name"] for f in run["files"]}, self.guard)
            version = publish.record(self.store, run["db_run"], attempt, manifest, status, audit, self.guard)
        except Exception as exc:  # 정제·감사·발행 실패는 실행 결과로 보여 준다(결과 미발행)
            message = f"{type(exc).__name__}: {exc}"
            attempts.finish(self.store, attempt, stage, message, getattr(exc, "audit", None))
            run.update(result={"status": "failed", "error": message, "attempt": attempt.number, "stage": stage})
            self._transition(run, "failed", stage)
            self.save(run)
            return run["result"]
        run["status"] = status
        run.update(result={"status": status, "changed_cells": out["changed_cells"],
                                              "rows": out["rows"], "before": out["before_categories"],
                                              "after": remaining, "not_approved": out["not_approved_cells"],
                                              "plan_hash": plan["hash"], "approval": approval,
                                              "attempt": attempt.number, "audit": audit,
                                              "result_version": version,
                                              "excluded_tables": manifest["excluded_tables"]})
        self.save(run)
        return run["result"]

    def result(self, run_id: str) -> dict:
        run = self.load(run_id)
        if not run["result"]:
            raise FlowError("not_executed", "아직 실행하지 않았다", 409)
        version = publish.latest(self.store, run["db_run"])  # 완료 판정은 result_version 행
        files = []
        if version:
            manifest = json.loads(self.guard.safe_join(self.root, version["manifest_path"]).read_text(encoding="utf-8"))
            files = [{"name": n, "bytes": None, "rows": m["rows"]} for n, m in manifest["files"].items()
                     if n.endswith(".csv") and n != "changes.csv"]
            evidence = ["changes.csv"] + [n for n in EVIDENCE_FILES if n in manifest["files"] or n == "manifest.json"]
        else:
            evidence = []
        return run["result"] | {"files": files, "evidence": evidence, "published": version is not None}

    def remaining(self, run_id: str, category: str | None, offset: int, limit: int) -> dict:
        """남은 문제(SC08, 완료(문제 남음)): 발행된 결과의 정제 후 진단 중 위반·사람 검토, 원본 위치 포함."""
        run = self.load(run_id)
        engine = self._engine_dir(run)
        lineage = json.loads((self.run_dir(run_id) / "view" / "lineage.json").read_text(encoding="utf-8"))
        after = Locator(lineage).translate_report(json.loads((engine / "after.json").read_text(encoding="utf-8")))
        wanted = {category} if category else {"business_rule_violation", "human_review"}
        items = [i for i in after["issues"] if i["category"] in wanted]
        names = {f["item_id"]: f["name"] for f in run["files"]}
        keep = ("table", "record", "columns", "values", "code", "category", "source", "detail")
        out = []
        for i in items[offset: offset + limit]:
            item = {k: i.get(k) for k in keep}
            if item["source"]:
                item["source"] = item["source"] | {"file": names.get(item["source"]["item_id"])}
            out.append(item)
        return {"total": len(items), "items": out}

    def failure_report(self, run_id: str) -> dict:
        """실패 진단 리포트(AC-07-2): 실패한 실행에서 내려받을 수 있는 유일한 산출물. 결과 데이터는 넣지 않는다."""
        run = self.load(run_id)
        if run["status"] != "failed":
            raise FlowError("not_failed", "실패한 실행만 실패 진단 리포트가 있다", 404)
        attempt = self.store.conn.execute("SELECT * FROM attempt WHERE run_id = ? ORDER BY number DESC LIMIT 1",
                                          (run["db_run"],)).fetchone()
        report = self._report(run_id)
        out = {"run_id": run_id, "status": "failed", "source": run["source"], "config_hash": run["config_hash"],
               "view_hash": run.get("view_hash"), "result": run["result"],
               "files": [{k: f[k] for k in ("item_id", "name", "sha256", "rows")} for f in run["files"]],
               "diagnosis_before": report["summary"]["category_counts"], "diagnosis_after": None,
               "attempt": None if attempt is None else {
                   "number": attempt["number"], "outcome": attempt["outcome"], "error": attempt["error"],
                   "audit": None if attempt["audit"] is None else json.loads(attempt["audit"])}}
        if attempt is not None:
            after = self.guard.safe_join(self.root, attempt["path"], "engine", "after.json")
            if after.is_file():
                out["diagnosis_after"] = json.loads(after.read_text(encoding="utf-8"))["summary"]["category_counts"]
        return out

    def _engine_dir(self, run: dict) -> Path:
        """발행된 결과 버전의 시도 폴더(엔진 산출물). 결과 버전이 없으면 결과 없음."""
        version = publish.latest(self.store, run["db_run"])
        if version is None:
            raise FlowError("not_executed", "발행된 결과가 없다", 409)
        path = self.store.conn.execute("SELECT path FROM attempt WHERE id = ?", (version["attempt_id"],)).fetchone()[0]
        return self.guard.safe_join(self.root, path, "engine")

    def changes(self, run_id: str, offset: int, limit: int) -> dict:
        path = self._engine_dir(self.load(run_id)) / "changes.json"
        if not path.is_file():
            raise FlowError("not_executed", "아직 실행하지 않았다", 409)
        items = json.loads(path.read_text(encoding="utf-8"))
        lineage = json.loads((self.run_dir(run_id) / "view" / "lineage.json").read_text(encoding="utf-8"))
        names = {f["item_id"]: f["name"] for f in self.load(run_id)["files"]}
        page = Locator(lineage).translate_changes(items[offset: offset + limit])
        keep = ("table", "record", "column", "before", "after", "codes", "source")
        out = []
        for i in page:
            item = {k: i.get(k) for k in keep}
            item["source"] = item["source"] | {"file": names.get(item["source"]["item_id"])}
            out.append(item)
        return {"total": len(items), "items": out}

    def download(self, run_id: str, name: str) -> bytes:
        run = self.load(run_id)
        version = publish.latest(self.store, run["db_run"])
        if version is None or not (FILE_NAME.fullmatch(name) or name in EVIDENCE_FILES):
            raise FlowError("file_not_found", "파일이 없다", 404)
        try:
            return publish.read_verified(self.guard, self.root, version, name)  # 해시 재대조
        except FileNotFoundError as exc:
            raise FlowError("file_not_found", "파일이 없다", 404) from exc
        except publish.PublishError as exc:
            raise FlowError("result_tampered", str(exc), 409) from exc


def _empty_plan_hash() -> str:
    import hashlib

    return hashlib.sha256(b"profile-only").hexdigest()


def _remove_tree(path: Path) -> None:
    for p in sorted(path.rglob("*"), reverse=True):
        p.unlink() if p.is_file() or p.is_symlink() else p.rmdir()
    path.rmdir()


def _mapping_of(proposal: dict) -> dict:
    return {"table": proposal["table"]["table"],
            "columns": {c["original"]: c["logical"] for c in proposal["columns"]}}


def _write_view(data: bytes, entry: dict, target: Path) -> None:
    """표준화 뷰(간이): 매핑된 컬럼만 논리 이름으로. 추가 컬럼은 뷰에 넣지 않는다(TD 5-3)."""
    rows = list(csv.reader(io.StringIO(data.decode(entry["read_options"]["encoding"]), newline=""),
                           delimiter=entry["read_options"]["delimiter"], strict=True))
    cols = entry["mapping"]["columns"]
    keep = [i for i, h in enumerate(rows[0]) if cols.get(h)]
    out = [[cols[rows[0][i]] for i in keep]] + [[r[i] for i in keep] for r in rows[1:]]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(out)
    target.write_text(buf.getvalue(), encoding="utf-8")
