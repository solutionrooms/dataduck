"""FastAPI control plane."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from duckbricks.api.auth import require_token
from duckbricks.api.routes import (
    bundles,
    federation,
    jobs,
    platform,
    statements,
    unity_catalog,
    warehouses,
    workspace,
)
from duckbricks.core import db
from duckbricks.core.bootstrap import bootstrap
from duckbricks.core.config import REPO_ROOT, get_settings
from duckbricks.core.errors import ApiError
from duckbricks.scheduler.loop import SchedulerLoop

log = logging.getLogger("duckbricks.api")
STARTER_WAREHOUSE = "Starter Warehouse"
UI_DIST = REPO_ROOT / "ui" / "dist"


def ensure_starter_warehouse() -> None:
    """Databricks workspaces come with a starter warehouse; so does ours (stopped)."""
    from duckbricks.warehouses import lifecycle

    with db.get_engine(get_settings().control_url).begin() as conn:
        if not lifecycle.list_rows(conn):
            lifecycle.create(
                conn,
                {"name": STARTER_WAREHOUSE, "cluster_size": "Small", "auto_stop_mins": 10},
                get_settings().user_name,
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    created = await run_in_threadpool(bootstrap)
    log.warning("bootstrap complete; created catalogs: %s", created or "none")
    from duckbricks.api.deps import workspace as get_workspace

    get_workspace().ensure_home()
    await run_in_threadpool(ensure_starter_warehouse)
    scheduler = SchedulerLoop()
    from duckbricks.api.background import register_steps

    register_steps(scheduler)
    scheduler.start()
    app.state.scheduler = scheduler
    yield
    scheduler.stop()


app = FastAPI(title="Duckbricks", version="0.1.0", lifespan=lifespan)


@app.exception_handler(ApiError)
async def api_error(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.status_code)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(exc.detail, status_code=exc.status_code)
    code = {404: "ENDPOINT_NOT_FOUND", 405: "BAD_REQUEST"}.get(exc.status_code, "BAD_REQUEST")
    return JSONResponse({"error_code": code, "message": str(exc.detail)}, exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    problems = "; ".join(
        f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors()
    )
    return JSONResponse(
        {"error_code": "INVALID_PARAMETER_VALUE", "message": problems}, status_code=400
    )


@app.get("/health")
def health() -> JSONResponse:
    """Liveness plus a control-database round trip."""
    try:
        with db.get_engine(get_settings().control_url).connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        return JSONResponse({"status": "unhealthy", "control_db": str(exc)[:200]}, status_code=503)
    return JSONResponse({"status": "ok"})


for module in (
    unity_catalog,
    statements,
    jobs,
    workspace,
    platform,
    bundles,
    warehouses,
    federation,
):
    app.include_router(module.router, dependencies=[Depends(require_token)])


# The web UI: a single-page app served from ui/dist when it has been built.
if UI_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, f"No API endpoint at /{path}")
        candidate = UI_DIST / path
        if path and candidate.is_file() and UI_DIST in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(UI_DIST / "index.html")
