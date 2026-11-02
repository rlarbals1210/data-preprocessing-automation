"""웹 미리보기용 실행 흐름 (2026-11-02 규민님 요청으로 M3 화면을 먼저 확인하기 위한 임시 경로).

업로드 → 헤더 대응(legacy-header-map 정확 일치) → 실행 계획(간이) → 표준화 뷰 → 진단 → 변경 미리보기 → 정제.
상태는 workspace/runs/<id>/run.json에 둔다. M1의 매핑·실행 계획·승인 해시·SQLite·발행이 완성되면 이 모듈을 대체한다.
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

from diagnostics.cleaning import clean
from diagnostics.engine import diagnose, validate_config
from prep.guard import Guard
from prep.snapshot import create_snapshot
from prep.store import Store
from prep.upload import BlobStore, UploadError, ingest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETS = {"shop": "쇼핑몰 표준", "none": "설정 없음"}
RUN_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}")
FILE_NAME = re.compile(r"[A-Za-z0-9가-힣_.-]{1,100}\.csv")


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

    # ---- 저장 ----
    def run_dir(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id or ""):
            raise FlowError("run_not_found", "실행을 찾을 수 없다", 404)
        path = self.guard.safe_join(self.runs, run_id)
        if not (path / "run.json").is_file():
            raise FlowError("run_not_found", "실행을 찾을 수 없다", 404)
        return path

    def load(self, run_id: str) -> dict:
        return json.loads((self.run_dir(run_id) / "run.json").read_text(encoding="utf-8"))

    def save(self, run: dict) -> None:
        path = self.guard.safe_join(self.runs, run["id"], "run.json")
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def list_runs(self) -> list[dict]:
        items = []
        for p in sorted(self.runs.iterdir(), reverse=True):
            if RUN_ID.fullmatch(p.name) and (p / "run.json").is_file():
                r = json.loads((p / "run.json").read_text(encoding="utf-8"))
                items.append({k: r.get(k) for k in ("id", "created_at", "set", "source", "status", "rows", "result")}
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
        snapshot = create_snapshot(PROJECT_ROOT, "shop", self.root, self.guard) if set_name == "shop" else None
        if snapshot:
            self.store.add_config_snapshot(snapshot.hash, "shop", f"config-snapshots/{snapshot.hash}",
                                           list(snapshot.files))
        header_map = _load_header_map(snapshot.path) if snapshot else {}
        schema = json.loads((snapshot.path / "schema.json").read_text(encoding="utf-8")) if snapshot else None
        entries = [_inspect(item, header_map, schema) for item in uploaded["items"]]
        run = {"id": run_id, "created_at": now.isoformat(), "set": set_name, "source": source.strip(),
               "batch_id": uploaded["batch_id"], "status": "uploaded",
               "snapshot": snapshot.hash if snapshot else None, "files": entries,
               "rows": sum(f.get("rows", 0) for f in entries), "plan": None, "result": None}
        self.save(run)
        return run

    # ---- 진단 ----
    def diagnose(self, run_id: str) -> dict:
        run = self.load(run_id)
        if "batch_id" not in run:
            raise FlowError("legacy_run", "원본 보관(M1-4) 이전에 만든 실행이라 다시 진단할 수 없다. 새로 업로드하세요", 409)
        base = self.run_dir(run_id)
        readable = [f for f in run["files"] if f["read_error"] is None]
        if not readable:
            raise FlowError("nothing_readable", "읽을 수 있는 파일이 없다")
        tables = Counter(f["table"] for f in readable)
        dup = [t for t, n in tables.items() if n > 1]
        if dup:
            raise FlowError("duplicate_table", f"같은 표준 테이블로 가는 파일이 여럿이다: {dup}")
        view = self.guard.safe_join(base, "view")
        if view.exists():
            for p in view.iterdir():
                p.unlink()
        view.mkdir(exist_ok=True)
        for f in readable:
            data = self.blobs.get(f["sha256"])  # 보관 원본(해시 대조)
            _write_view(data, f, self.guard.safe_join(view, f["table"] + ".csv"))
        config, skipped = {}, []
        if run["snapshot"]:
            snap = self.guard.safe_join(self.root, "config-snapshots", run["snapshot"])
            logical = json.loads((snap / "config/sets/shop/logical-diagnostics.json").read_text(encoding="utf-8"))
            present = {f["table"]: {c["logical"] for c in f["headers"] if c["logical"]} for f in readable}
            config, skipped = plan(logical, present)
        report = diagnose(view, config)
        (base / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        run.update(status="diagnosed", plan={"skipped_rules": skipped}, result=None)
        (base / "engine-config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        result = self.guard.safe_join(base, "result")
        if result.exists():
            _remove_tree(result)
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
            "skipped_rules": (run["plan"] or {}).get("skipped_rules", []),
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
                "category_reason")
        return {"total": len(items), "items": [{k: i.get(k) for k in keep} for i in page]}

    def changes_preview(self, run_id: str) -> dict:
        report = self._report(run_id)
        groups: dict[tuple, dict] = {}
        for i in report["issues"]:
            if i["category"] != "standardization_candidate":
                continue
            key = (i["table"], i["columns"][0], i["code"])
            g = groups.setdefault(key, {"table": key[0], "column": key[1], "code": key[2], "cells": set(),
                                        "examples": []})
            g["cells"].add(i["record"])
            before = i["values"][key[1]]
            after = "" if i.get("suggested_value") is None else i["suggested_value"]
            if len(g["examples"]) < 3 and all(e["before"] != before for e in g["examples"]):
                g["examples"].append({"before": before, "after": after})
        out = [g | {"cells": len(g["cells"])} for g in groups.values()]
        out.sort(key=lambda g: (-g["cells"], g["table"], g["column"]))
        return {"groups": out, "total_cells": sum(g["cells"] for g in out)}

    # ---- 실행 ----
    def execute(self, run_id: str) -> dict:
        run = self.load(run_id)
        base = self.run_dir(run_id)
        self._report(run_id)
        config = json.loads((base / "engine-config.json").read_text(encoding="utf-8"))
        result = self.guard.safe_join(base, "result")
        if result.exists():
            _remove_tree(result)
        try:
            out = clean(self.guard.safe_join(base, "view"), config, result)
        except Exception as exc:  # 정제 실패는 실행 결과로 보여 준다
            run.update(status="failed", result={"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            self.save(run)
            return run["result"]
        remaining = out["after_categories"]
        status = "completed_with_issues" if remaining.get("business_rule_violation") or remaining.get(
            "human_review") else "completed"
        run.update(status="executed", result={"status": status, "changed_cells": out["changed_cells"],
                                              "rows": out["rows"], "before": out["before_categories"],
                                              "after": remaining})
        self.save(run)
        return run["result"]

    def result(self, run_id: str) -> dict:
        run = self.load(run_id)
        if not run["result"]:
            raise FlowError("not_executed", "아직 실행하지 않았다", 409)
        files = []
        data = self.run_dir(run_id) / "result" / "data"
        if data.is_dir():
            files = [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(data.glob("*.csv"))]
        return run["result"] | {"files": files}

    def changes(self, run_id: str, offset: int, limit: int) -> dict:
        path = self.run_dir(run_id) / "result" / "changes.json"
        if not path.is_file():
            raise FlowError("not_executed", "아직 실행하지 않았다", 409)
        items = json.loads(path.read_text(encoding="utf-8"))
        keep = ("table", "record", "column", "before", "after", "codes")
        return {"total": len(items), "items": [{k: i.get(k) for k in keep} for i in items[offset: offset + limit]]}

    def download(self, run_id: str, name: str) -> Path:
        base = self.run_dir(run_id)
        if name == "changes.csv":
            path = self.guard.safe_join(base, "result", "changes.csv")
        elif FILE_NAME.fullmatch(name):
            path = self.guard.safe_join(base, "result", "data", name)
        else:
            raise FlowError("file_not_found", "파일이 없다", 404)
        if not path.is_file():
            raise FlowError("file_not_found", "파일이 없다", 404)
        return path


def _remove_tree(path: Path) -> None:
    for p in sorted(path.rglob("*"), reverse=True):
        p.unlink() if p.is_file() or p.is_symlink() else p.rmdir()
    path.rmdir()


def _load_header_map(snapshot_path: Path) -> dict:
    data = json.loads((snapshot_path / "config/sets/shop/legacy-header-map.json").read_text(encoding="utf-8"))
    return data["tables"]


def _inspect(item: dict, header_map: dict, schema: dict | None) -> dict:
    entry = {"item_id": item["id"], "name": item["filename"], "sha256": item["sha256"], "bytes": item["bytes"],
             "read_error": None, "table": item["filename"][:-4]}
    if item["read_status"] != "ok":
        e = item["read_errors"][0]
        where = f"{e['record']}번째 레코드: " if e.get("record") else ""
        return entry | {"read_error": where + e["reason"], "headers": []}
    table = entry["table"]
    cmap = header_map.get(table, {}).get("columns", {})
    known = bool(schema) and table in schema["tables"]
    logical_names = {f["name"] for f in schema["tables"][table]["fields"]} if known else set()
    headers = []
    for h in item["headers"]:
        if h in cmap:
            headers.append({"original": h, "logical": cmap[h], "basis": "legacy_map"})
        elif h in logical_names:
            headers.append({"original": h, "logical": h, "basis": "exact"})
        else:
            headers.append({"original": h, "logical": None, "basis": None})
    return entry | {"known_table": known, "rows": item["rows"], "columns": len(item["headers"]), "headers": headers}


def _write_view(data: bytes, entry: dict, target: Path) -> None:
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True))
    rows[0] = [h["logical"] or h["original"] for h in entry["headers"]]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    target.write_text(buf.getvalue(), encoding="utf-8")


def plan(logical: dict, present: dict[str, set[str]]) -> tuple[dict, list[dict]]:
    """간이 실행 계획(TD 5-2): 없는 컬럼·대상 테이블에 기대는 규칙은 사유와 함께 뺀다."""
    skipped, tables = [], {}
    for name, spec in logical["tables"].items():
        if name not in present:
            continue
        cols = present[name]
        columns = {}
        for col, rule in spec["columns"].items():
            if col not in cols:
                skipped.append({"table": name, "rule": "column", "columns": [col], "reason": "missing_column"})
                continue
            rule = dict(rule)
            if "required_if" in rule and rule["required_if"]["column"] not in cols:
                rule.pop("required_if")
                skipped.append({"table": name, "rule": "required_if", "columns": [col],
                                "reason": "dependency_skipped"})
            columns[col] = rule
        new = {"columns": columns}
        unique = [g for g in spec.get("unique", []) if all(c in columns for c in g)]
        skipped += [{"table": name, "rule": "unique", "columns": g, "reason": "dependency_skipped"}
                    for g in spec.get("unique", []) if g not in unique]
        if unique:
            new["unique"] = unique
        refs = []
        for ref in spec.get("references", []):
            if ref["table"] not in present:
                skipped.append({"table": name, "rule": "reference", "columns": ref["columns"],
                                "target": ref["table"], "reason": "excluded_target"})
            elif not all(c in columns for c in ref["columns"]) or not all(
                    c in present[ref["table"]] and c in logical["tables"][ref["table"]]["columns"]
                    for c in ref["target_columns"]):
                skipped.append({"table": name, "rule": "reference", "columns": ref["columns"],
                                "target": ref["table"], "reason": "dependency_skipped"})
            else:
                refs.append(ref)
        if refs:
            new["references"] = refs
        tables[name] = new
    # 참조 대상 테이블의 대상 컬럼 규칙이 빠졌으면 참조도 뺀다(엔진 검증기 조건)
    for name, spec in tables.items():
        kept = []
        for ref in spec.get("references", []):
            if all(c in tables[ref["table"]]["columns"] for c in ref["target_columns"]):
                kept.append(ref)
            else:
                skipped.append({"table": name, "rule": "reference", "columns": ref["columns"],
                                "target": ref["table"], "reason": "dependency_skipped"})
        if kept:
            spec["references"] = kept
        else:
            spec.pop("references", None)
    config = {"version": logical.get("version", 1), "tables": tables} if tables else {}
    if config:
        validate_config(json.loads(json.dumps(config)))
    return config, skipped
