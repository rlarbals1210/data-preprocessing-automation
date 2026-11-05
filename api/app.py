"""FastAPI 앱 — 웹 미리보기 (docs/technical-design.md 12절). 127.0.0.1 전용.

    uv run uvicorn api.app:app --host 127.0.0.1 --port 8000
web/dist가 있으면 / 에서 화면을 함께 제공한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Body, FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from prep.preview import PROJECT_ROOT, SETS, FlowError, Workspace

WORKSPACE = Path(os.environ.get("DPA_WORKSPACE", PROJECT_ROOT / "workspace"))


def create_app(workspace: Path = WORKSPACE) -> FastAPI:
    app = FastAPI(title="데이터 전처리 자동화")
    ws = Workspace(workspace)
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(FlowError)
    async def flow_error(_: Request, exc: FlowError):
        mismatched = getattr(exc.__cause__, "mismatched", None)
        details = getattr(exc, "details", None) or ({"mismatched": mismatched} if mismatched else None)
        return JSONResponse(status_code=exc.status,
                            content={"code": exc.code, "message": exc.message, "details": details, "location": None})

    @app.exception_handler(PermissionError)
    async def denied(_: Request, exc: PermissionError):
        return JSONResponse(status_code=403,
                            content={"code": "path_not_allowed", "message": str(exc), "details": None,
                                     "location": None})

    @app.get("/api/sets")
    def sets():
        return [{"id": k, "label": v} for k, v in SETS.items()]

    @app.get("/api/runs")
    def runs():
        return ws.list_runs()

    @app.post("/api/runs")
    async def create_run(files: Annotated[list[UploadFile], File()],
                         set_name: Annotated[str, Form(alias="set")] = "shop",
                         source: Annotated[str, Form()] = ""):
        payload = [(os.path.basename(f.filename or ""), await f.read()) for f in files]
        return ws.create_run(set_name, payload, source)

    @app.get("/api/sources")
    def sources():
        return ws.sources_summary()

    @app.get("/api/sources/{source_id}/versions")
    def source_versions(source_id: int):
        return ws.source_versions(source_id)

    @app.get("/api/versions/{version_id}")
    def version(version_id: int):
        return ws.version_detail(version_id)

    @app.post("/api/versions/{version_id}/restore")
    def restore(version_id: int):
        return ws.restore_version(version_id)

    @app.get("/api/versions/{version_id}/files/{name}")
    def version_file(version_id: int, name: str):
        data = ws.version_file(version_id, name)
        kind = "application/json" if name.endswith(".json") else "text/csv; charset=utf-8"
        return Response(data, media_type=kind,
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        return ws.view(run_id)

    @app.post("/api/runs/{run_id}/retry")
    def retry(run_id: str):
        return ws.retry(run_id)

    @app.get("/api/runs/{run_id}/drift")
    def drift(run_id: str):
        return ws.drift_view(run_id)

    @app.post("/api/runs/{run_id}/drift/select")
    def drift_select(run_id: str, body: Annotated[dict, Body()]):
        template = body.get("template_id")
        if template is not None and not isinstance(template, int):
            raise FlowError("bad_template", "template_id는 정수 또는 null이다")
        return ws.drift_select(run_id, int(body.get("item_id", 0)), template)

    @app.post("/api/runs/{run_id}/drift/confirm")
    def drift_confirm(run_id: str):
        return ws.drift_confirm(run_id)

    @app.post("/api/runs/{run_id}/propose")
    def propose(run_id: str, body: Annotated[dict, Body()]):
        return ws.propose_table(run_id, int(body.get("item_id", 0)), str(body.get("table", "")))

    @app.put("/api/runs/{run_id}/mapping")
    def confirm_mapping(run_id: str, body: Annotated[dict, Body()]):
        files = body.get("files")
        if not isinstance(files, list) or not all(isinstance(x, dict) for x in files):
            raise FlowError("bad_mapping", "files 목록이 필요하다")
        impact = body.get("reuse_impact")
        if impact is not None and not isinstance(impact, dict):
            raise FlowError("bad_mapping", "reuse_impact는 {파일 ID: 'this_run'|'new_version'} 형식이다")
        return ws.confirm_mapping(run_id, files, impact)

    @app.post("/api/runs/{run_id}/diagnose")
    def diagnose(run_id: str):
        return ws.diagnose(run_id)

    @app.get("/api/runs/{run_id}/diagnosis")
    def diagnosis(run_id: str):
        return ws.diagnosis(run_id)

    @app.get("/api/runs/{run_id}/issues")
    def issues(run_id: str, category: str | None = None, table: str | None = None, offset: int = 0,
               limit: int = 50):
        return ws.issues(run_id, category, table, max(offset, 0), min(max(limit, 1), 500))

    @app.get("/api/runs/{run_id}/integrity")
    def integrity(run_id: str, basis: str = "raw", offset: int = 0, limit: int = 50):
        return ws.integrity(run_id, basis, max(offset, 0), min(max(limit, 1), 500))

    @app.get("/api/runs/{run_id}/duplicates")
    def duplicates(run_id: str, offset: int = 0, limit: int = 50):
        return ws.duplicates(run_id, max(offset, 0), min(max(limit, 1), 500))

    @app.get("/api/runs/{run_id}/source")
    def source_row(run_id: str, item_id: int, record: int):
        return ws.source_row(run_id, item_id, record)

    @app.get("/api/reuse-rules")
    def reuse_rules():
        return ws.reuse_units()

    @app.post("/api/reuse-rules/{rule_id}/revoke")
    def revoke_reuse_rule(rule_id: int, body: Annotated[dict | None, Body()] = None):
        return ws.revoke_reuse_rule(rule_id, (body or {}).get("reason"))

    @app.get("/api/runs/{run_id}/preview")
    def preview(run_id: str):
        return ws.changes_preview(run_id)

    @app.get("/api/runs/{run_id}/preview/groups/{gid}")
    def preview_cells(run_id: str, gid: str, offset: int = 0, limit: int = 50):
        return ws.preview_cells(run_id, gid, max(offset, 0), min(max(limit, 1), 500))

    @app.put("/api/runs/{run_id}/selection")
    def select(run_id: str, body: Annotated[dict, Body()]):
        return ws.select(run_id, body)

    @app.post("/api/runs/{run_id}/execute")
    def execute(run_id: str, body: Annotated[dict | None, Body()] = None):
        return ws.execute(run_id, (body or {}).get("approval") or {}, (body or {}).get("reuse"))

    @app.get("/api/runs/{run_id}/result")
    def result(run_id: str):
        return ws.result(run_id)

    @app.get("/api/runs/{run_id}/changes")
    def changes(run_id: str, offset: int = 0, limit: int = 50):
        return ws.changes(run_id, max(offset, 0), min(max(limit, 1), 500))

    @app.get("/api/runs/{run_id}/remaining")
    def remaining(run_id: str, category: str | None = None, offset: int = 0, limit: int = 50):
        return ws.remaining(run_id, category, max(offset, 0), min(max(limit, 1), 500))

    @app.get("/api/runs/{run_id}/failure-report")
    def failure_report(run_id: str):
        return JSONResponse(ws.failure_report(run_id), headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'failure-report-{run_id}.json')}"})

    @app.get("/api/runs/{run_id}/files/{name}")
    def download(run_id: str, name: str):
        data = ws.download(run_id, name)
        kind = "application/json" if name.endswith(".json") else "text/csv; charset=utf-8"
        return Response(data, media_type=kind,
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    dist = PROJECT_ROOT / "web" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            if path == "api" or path.startswith("api/"):
                raise FlowError("not_found", "없는 API 경로", 404)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
