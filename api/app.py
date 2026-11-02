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
        return JSONResponse(status_code=exc.status,
                            content={"code": exc.code, "message": exc.message, "details": None, "location": None})

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
        return [{"id": s["id"], "name": s["name"]} for s in ws.store.sources()]

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        return ws.view(run_id)

    @app.post("/api/runs/{run_id}/propose")
    def propose(run_id: str, body: Annotated[dict, Body()]):
        return ws.propose_table(run_id, int(body.get("item_id", 0)), str(body.get("table", "")))

    @app.put("/api/runs/{run_id}/mapping")
    def confirm_mapping(run_id: str, body: Annotated[dict, Body()]):
        files = body.get("files")
        if not isinstance(files, list) or not all(isinstance(x, dict) for x in files):
            raise FlowError("bad_mapping", "files 목록이 필요하다")
        return ws.confirm_mapping(run_id, files)

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

    @app.get("/api/runs/{run_id}/preview")
    def preview(run_id: str):
        return ws.changes_preview(run_id)

    @app.put("/api/runs/{run_id}/selection")
    def select(run_id: str, body: Annotated[dict, Body()]):
        return ws.select(run_id, body)

    @app.post("/api/runs/{run_id}/execute")
    def execute(run_id: str):
        return ws.execute(run_id)

    @app.get("/api/runs/{run_id}/result")
    def result(run_id: str):
        return ws.result(run_id)

    @app.get("/api/runs/{run_id}/changes")
    def changes(run_id: str, offset: int = 0, limit: int = 50):
        return ws.changes(run_id, max(offset, 0), min(max(limit, 1), 500))

    @app.get("/api/runs/{run_id}/files/{name}")
    def download(run_id: str, name: str):
        data = ws.download(run_id, name)
        return Response(data, media_type="text/csv; charset=utf-8",
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
