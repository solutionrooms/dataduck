"""Jobs API 2.2 subset: /api/2.2/jobs/... (2.1 paths are accepted as aliases)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from duckbricks.api.deps import control
from duckbricks.core import jobs, runs
from duckbricks.core.config import get_settings
from duckbricks.core.errors import InvalidParameter

router = APIRouter(tags=["Jobs"])
PREFIXES = ("/api/2.2/jobs", "/api/2.1/jobs")


def route(method: str, path: str):  # type: ignore[no-untyped-def]
    """Register a handler under every Jobs API version prefix."""

    def wrap(fn):  # type: ignore[no-untyped-def]
        for prefix in PREFIXES:
            router.add_api_route(prefix + path, fn, methods=[method])
        return fn

    return wrap


class JobSettings(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str | None = None
    tasks: list[dict[str, Any]] = Field(default_factory=list)


class JobId(BaseModel):
    model_config = ConfigDict(extra="allow")
    job_id: int


class ResetJob(JobId):
    new_settings: dict[str, Any]


class UpdateJob(JobId):
    new_settings: dict[str, Any] = Field(default_factory=dict)
    fields_to_remove: list[str] = Field(default_factory=list)


class RunNow(JobId):
    job_parameters: dict[str, Any] | None = None
    notebook_params: dict[str, Any] | None = None
    python_params: list[str] | None = None
    idempotency_token: str | None = None


class SubmitRun(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_name: str | None = None
    tasks: list[dict[str, Any]] = Field(default_factory=list)


class RunId(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: int


@route("POST", "/create")
def create_job(body: JobSettings) -> dict[str, Any]:
    with control() as conn:
        job_id, warnings = jobs.create_job(
            conn, body.model_dump(exclude_none=True), get_settings().user_name
        )
    out: dict[str, Any] = {"job_id": job_id}
    if warnings:
        out["warnings"] = warnings
    return out


@route("POST", "/reset")
def reset_job(body: ResetJob) -> dict[str, Any]:
    with control() as conn:
        warnings = jobs.reset_job(conn, body.job_id, body.new_settings)
    return {"warnings": warnings} if warnings else {}


@route("POST", "/update")
def update_job(body: UpdateJob) -> dict[str, Any]:
    with control() as conn:
        warnings = jobs.update_job(conn, body.job_id, body.new_settings, body.fields_to_remove)
    return {"warnings": warnings} if warnings else {}


@route("POST", "/delete")
def delete_job(body: JobId) -> dict[str, Any]:
    with control() as conn:
        jobs.delete_job(conn, body.job_id)
    return {}


@route("GET", "/get")
def get_job(job_id: int) -> dict[str, Any]:
    with control() as conn:
        return jobs.job_json(jobs.get_job_row(conn, job_id))


@route("GET", "/list")
def list_jobs(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    name: str | None = None,
    expand_tasks: bool = False,
) -> dict[str, Any]:
    with control() as conn:
        items, has_more = jobs.list_jobs(conn, limit, offset, name)
    if not expand_tasks:
        for item in items:
            item["settings"] = {k: v for k, v in item["settings"].items() if k != "tasks"}
    return {"jobs": items, "has_more": has_more}


@route("POST", "/run-now")
def run_now(body: RunNow) -> dict[str, Any]:
    params = dict(body.job_parameters or {})
    if body.notebook_params:
        params.update(body.notebook_params)
    with control() as conn:
        triggered = runs.trigger_job(conn, body.job_id, "ONE_TIME", params)
    return {"run_id": triggered.run_id, "number_in_job": triggered.run_id}


@route("POST", "/runs/submit")
def submit_run(body: SubmitRun) -> dict[str, Any]:
    raw = body.model_dump(exclude_none=True)
    raw.setdefault("name", body.run_name or "Untitled")
    settings, _ = jobs.validate_settings(raw)
    with control() as conn:
        run_id = runs.submit_run(conn, settings, body.run_name)
    return {"run_id": run_id}


@route("GET", "/runs/get")
def get_run(run_id: int, include_history: bool = False) -> dict[str, Any]:
    with control() as conn:
        return runs.run_json(conn, runs.get_run_row(conn, run_id))


@route("GET", "/runs/list")
def list_runs(
    job_id: int | None = None,
    active_only: bool = False,
    completed_only: bool = False,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    run_type: str | None = None,
    start_time_from: int | None = None,
    start_time_to: int | None = None,
    expand_tasks: bool = False,
) -> dict[str, Any]:
    with control() as conn:
        items, has_more = runs.list_runs(
            conn,
            job_id,
            active_only,
            completed_only,
            limit,
            offset,
            run_type,
            start_time_from,
            start_time_to,
            expand_tasks,
        )
    return {"runs": items, "has_more": has_more}


@route("POST", "/runs/cancel")
def cancel_run(body: RunId) -> dict[str, Any]:
    with control() as conn:
        runs.cancel_run(conn, body.run_id)
    return {}


@route("POST", "/runs/cancel-all")
def cancel_all(body: dict[str, Any]) -> dict[str, Any]:
    job_id = body.get("job_id")
    if job_id is None:
        raise InvalidParameter("job_id is required")
    with control() as conn:
        active, _ = runs.list_runs(conn, int(job_id), active_only=True, limit=100)
        for run in active:
            runs.cancel_run(conn, run["run_id"])
    return {}


@route("GET", "/runs/get-output")
def get_output(run_id: int) -> dict[str, Any]:
    with control() as conn:
        return runs.output_json(conn, run_id)
