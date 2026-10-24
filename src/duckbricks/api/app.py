"""FastAPI control plane."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import duckdb
from fastapi import Depends, FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import text

from duckbricks.api.auth import require_token
from duckbricks.core import db
from duckbricks.core.bootstrap import bootstrap
from duckbricks.core.catalogs import list_catalogs
from duckbricks.core.config import get_settings

log = logging.getLogger("duckbricks.api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    created = await run_in_threadpool(bootstrap)
    log.warning("bootstrap complete; created catalogs: %s", created or "none")
    yield


app = FastAPI(title="Duckbricks", version="0.0.1", lifespan=lifespan)


@app.get("/health")
def health() -> JSONResponse:
    """Liveness plus a control-database round trip."""
    try:
        with db.get_engine(get_settings().control_url).connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        return JSONResponse({"status": "unhealthy", "control_db": str(exc)[:200]}, status_code=503)
    return JSONResponse({"status": "ok"})


@app.get("/api/duckbricks/status", dependencies=[Depends(require_token)])
def platform_status() -> dict[str, Any]:
    return {
        "versions": {"duckdb": duckdb.__version__},
        "catalogs": [
            {"name": c.name, "comment": c.comment, "data_path": c.data_path}
            for c in list_catalogs()
        ],
    }
