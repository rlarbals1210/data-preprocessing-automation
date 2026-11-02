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
from diagnostics.engine import diagnose, read_table
from prep.guard import Guard
from prep.mapping import Catalog, propose, validate_final
from prep.plan import build_plan
from prep.snapshot import create_snapshot
from prep.store import Store
from prep.upload import BlobStore, UploadError, ingest
from prep.view import Locator, ViewInput, build_view, strip_extras

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
        run = json.loads((self.run_dir(run_id) / "run.json").read_text(encoding="utf-8"))
        if any("mapping" not in f for f in run["files"]):
            raise FlowError("legacy_run", "매핑(M1-5) 이전에 만든 실행이다. 새로 업로드하세요", 410)
        return run

    def view(self, run_id: str) -> dict:
        """화면용: 실행 + 표준 목록(테이블 → 컬럼, 필수 컬럼)."""
        run = self.load(run_id)
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
        tmp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def list_runs(self) -> list[dict]:
        items = []
        for p in sorted(self.runs.iterdir(), reverse=True):
            if RUN_ID.fullmatch(p.name) and (p / "run.json").is_file():
                r = json.loads((p / "run.json").read_text(encoding="utf-8"))
                if any("mapping" not in f for f in r["files"]):
                    continue  # 매핑 이전 실행은 목록에서 뺀다
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
        run = {"id": run_id, "created_at": now.isoformat(), "set": set_name, "source": source.strip(),
               "batch_id": uploaded["batch_id"], "status": "uploaded",
               "snapshot": snapshot.hash if snapshot else None, "files": entries,
               "rows": sum(f.get("rows", 0) for f in entries), "plan": None, "result": None}
        self.save(run)
        return run

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
        if changed and run["status"] != "uploaded":  # AC-03-5: 매핑이 바뀌면 이전 진단·결과 무효
            base = self.run_dir(run_id)
            for name in ("report.json", "engine-config.json"):
                (base / name).unlink(missing_ok=True)
            for sub in ("result", "view"):
                if (base / sub).exists():
                    _remove_tree(base / sub)
            run.update(status="uploaded", plan=None, result=None)
        self.store.record_event("batch", run["batch_id"], "mapping_confirmed",
                                {"run": run_id, "changed": changed,
                                 "auto_unchanged": all(f["mapping"] == _mapping_of(f["proposal"])
                                                       for f in run["files"] if f["proposal"])})
        self.save(run)
        return self.view(run_id)

    # ---- 진단 ----
    def diagnose(self, run_id: str) -> dict:
        run = self.load(run_id)
        if "batch_id" not in run:
            raise FlowError("legacy_run", "원본 보관(M1-4) 이전에 만든 실행이라 다시 진단할 수 없다. 새로 업로드하세요", 409)
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
        report = Locator(built.lineage).translate_report(diagnose(view, config))
        (base / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        run.update(status="diagnosed", result=None, view_hash=built.hash,
                   plan=None if plan is None else {k: plan[k] for k in ("skipped", "tables", "completeness", "hash")})
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
        base = self.run_dir(run_id)
        if name == "changes.csv":
            path = self.guard.safe_join(base, "result", "changes.csv")
        elif FILE_NAME.fullmatch(name):
            path = self.guard.safe_join(base, "result", "data", name)
        else:
            raise FlowError("file_not_found", "파일이 없다", 404)
        if not path.is_file():
            raise FlowError("file_not_found", "파일이 없다", 404)
        if name == "changes.csv":
            return path.read_bytes()
        return strip_extras(path.read_text(encoding="utf-8")).encode("utf-8")  # 보조 컬럼은 출력하지 않는다


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
