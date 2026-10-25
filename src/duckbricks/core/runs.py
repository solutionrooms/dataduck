"""Job runs: the queue and state machine behind the Jobs API.

The queue holds task runs (ADR 0001 #10). Triggering a job creates the job run and one task run
per task: `BLOCKED` until its dependencies finish, then `PENDING` (claimable) or a terminal
skip state. Finishing a task advances its dependents in the same transaction.

Lock order is always job run, then task runs. Claiming locks only the task run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, and_, func, insert, select, text, update

from duckbricks.core import db
from duckbricks.core.config import get_settings
from duckbricks.core.errors import InvalidParameter, InvalidState, NotFound

ACTIVE_JOB_STATES = ("PENDING", "RUNNING", "TERMINATING", "QUEUED")
TERMINAL = ("TERMINATED", "SKIPPED", "INTERNAL_ERROR")
UNLIMITED_RETRIES_CAP = 100


@dataclass(frozen=True)
class Triggered:
    run_id: int
    skipped: bool = False
    message: str = ""


def _ms(value: datetime | None) -> int:
    return int(value.timestamp() * 1000) if value else 0


def now() -> datetime:
    return datetime.now(UTC)


# --- parameters ------------------------------------------------------------------------


def resolve_parameters(
    settings: dict[str, Any], overrides: dict[str, Any] | None
) -> dict[str, str]:
    defined = {p["name"]: p.get("default", "") for p in settings.get("parameters") or []}
    values = {k: str(v) for k, v in defined.items()}
    for key, value in (overrides or {}).items():
        if key not in defined:
            raise InvalidParameter(
                f"Job parameter '{key}' is not defined. Defined parameters: "
                f"{', '.join(defined) or 'none'}"
            )
        values[key] = str(value)
    return values


# --- triggering ------------------------------------------------------------------------


def trigger_job(
    conn: Connection,
    job_id: int,
    trigger: str = "ONE_TIME",
    job_parameters: dict[str, Any] | None = None,
    parent_run_id: int | None = None,
) -> Triggered:
    job = (
        conn.execute(select(db.jobs).where(db.jobs.c.job_id == job_id).with_for_update())
        .mappings()
        .first()
    )
    if job is None:
        raise NotFound(f"Job {job_id} does not exist.")
    settings = job["settings"]
    params = resolve_parameters(settings, job_parameters)
    active = conn.execute(
        select(func.count())
        .select_from(db.job_runs)
        .where(
            db.job_runs.c.job_id == job_id, db.job_runs.c.life_cycle_state.in_(ACTIVE_JOB_STATES)
        )
    ).scalar_one()
    limit = int(settings.get("max_concurrent_runs") or 1)
    run_type = "WORKFLOW_RUN" if trigger == "RUN_JOB_TASK" else "JOB_RUN"
    if active >= limit:
        message = (
            f"Skipping this run because the limit of {limit} maximum concurrent runs "
            "has been reached."
        )
        run_id = conn.execute(
            insert(db.job_runs)
            .values(
                job_id=job_id,
                run_name=settings["name"],
                run_type=run_type,
                trigger=trigger,
                settings=settings,
                job_parameters=params,
                life_cycle_state="SKIPPED",
                result_state="MAXIMUM_CONCURRENT_RUNS_REACHED",
                state_message=message,
                parent_run_id=parent_run_id,
                end_time=func.now(),
            )
            .returning(db.job_runs.c.run_id)
        ).scalar_one()
        return Triggered(int(run_id), skipped=True, message=message)
    run_id = _create_run(
        conn, job_id, settings, params, trigger, run_type, settings["name"], parent_run_id
    )
    return Triggered(run_id)


def submit_run(conn: Connection, settings: dict[str, Any], run_name: str | None = None) -> int:
    """One-time run (`runs/submit`): no job row; settings were validated by the caller."""
    name = run_name or settings.get("run_name") or "Untitled"
    return _create_run(conn, None, settings, {}, "ONE_TIME", "SUBMIT_RUN", name, None)


def _create_run(
    conn: Connection,
    job_id: int | None,
    settings: dict[str, Any],
    params: dict[str, str],
    trigger: str,
    run_type: str,
    run_name: str,
    parent_run_id: int | None,
) -> int:
    run_id = int(
        conn.execute(
            insert(db.job_runs)
            .values(
                job_id=job_id,
                run_name=run_name,
                run_type=run_type,
                trigger=trigger,
                settings=settings,
                job_parameters=params,
                life_cycle_state="PENDING",
                parent_run_id=parent_run_id,
            )
            .returning(db.job_runs.c.run_id)
        ).scalar_one()
    )
    for task in settings["tasks"]:
        values: dict[str, Any] = {
            "job_run_id": run_id,
            "task_key": task["task_key"],
            "task": task,
            "life_cycle_state": "BLOCKED",
        }
        if task.get("disabled"):
            values.update(
                life_cycle_state="SKIPPED",
                result_state="EXCLUDED",
                state_message="Task is disabled",
                start_time=func.now(),
                end_time=func.now(),
            )
        conn.execute(insert(db.task_runs).values(**values))
    advance(conn, run_id)
    return run_id


# --- dependency resolution -------------------------------------------------------------


def _latest_attempts(conn: Connection, job_run_id: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        select(db.task_runs)
        .where(db.task_runs.c.job_run_id == job_run_id)
        .order_by(db.task_runs.c.task_key, db.task_runs.c.attempt_number)
    ).mappings()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest[row["task_key"]] = dict(row)
    return latest


def dependency_outcome(row: dict[str, Any], dep: dict[str, Any]) -> str:
    """Classify a finished dependency as success, failed or excluded for its dependents."""
    result = row["result_state"]
    if result == "SUCCESS":
        wanted = dep.get("outcome")
        if wanted is not None and str(row["output"].get("condition_outcome")) != str(wanted):
            return "excluded"
        return "success"
    if result == "EXCLUDED":
        return "success" if row["task"].get("disabled") else "excluded"
    return "failed"


def decide(run_if: str, outcomes: list[str]) -> tuple[str, str] | None:
    """None means run; otherwise (life_cycle_state, result_state) for a task that won't run."""
    if not outcomes:
        return None
    succeeded = outcomes.count("success")
    failed = outcomes.count("failed")
    excluded = outcomes.count("excluded")
    upstream_failed = ("TERMINATED", "UPSTREAM_FAILED")
    skip = ("SKIPPED", "EXCLUDED")
    if run_if == "ALL_DONE":
        return None
    if run_if == "NONE_FAILED":
        return upstream_failed if failed else None
    if run_if == "AT_LEAST_ONE_SUCCESS":
        return None if succeeded else (upstream_failed if failed else skip)
    if run_if == "ALL_FAILED":
        return None if failed == len(outcomes) else skip
    if run_if == "AT_LEAST_ONE_FAILED":
        return None if failed else skip
    if failed:
        return upstream_failed
    return skip if excluded else None


def advance(conn: Connection, job_run_id: int) -> None:
    """Release or skip blocked tasks whose dependencies have finished; finish the run if done."""
    while True:
        latest = _latest_attempts(conn, job_run_id)
        changed = False
        for row in latest.values():
            if row["life_cycle_state"] != "BLOCKED":
                continue
            deps = row["task"].get("depends_on") or []
            dep_rows = [latest.get(d["task_key"]) for d in deps]
            if any(r is None or r["life_cycle_state"] not in TERMINAL for r in dep_rows):
                continue
            outcomes = [dependency_outcome(r, d) for r, d in zip(dep_rows, deps, strict=True) if r]
            decision = decide(row["task"].get("run_if", "ALL_SUCCESS"), outcomes)
            values: dict[str, Any]
            if decision is None:
                values = {"life_cycle_state": "PENDING", "queued_at": func.now()}
                values["not_before"] = func.now()
            else:
                life, result = decision
                message = (
                    "Upstream task failed."
                    if result == "UPSTREAM_FAILED"
                    else "Excluded: the run_if condition or branch was not met."
                )
                values = {
                    "life_cycle_state": life,
                    "result_state": result,
                    "state_message": message,
                    "start_time": func.now(),
                    "end_time": func.now(),
                }
            conn.execute(
                update(db.task_runs).where(db.task_runs.c.run_id == row["run_id"]).values(**values)
            )
            changed = True
        if not changed:
            break
    if latest and all(r["life_cycle_state"] in TERMINAL for r in latest.values()):
        _finalize(conn, job_run_id, latest)


def _finalize(conn: Connection, job_run_id: int, latest: dict[str, dict[str, Any]]) -> None:
    run = (
        conn.execute(select(db.job_runs).where(db.job_runs.c.run_id == job_run_id)).mappings().one()
    )
    if run["life_cycle_state"] in TERMINAL:
        return
    failed = [
        r
        for r in latest.values()
        if r["result_state"] not in ("SUCCESS", "EXCLUDED", "UPSTREAM_FAILED")
    ]
    if run["result_state"]:  # set by a job-level timeout
        result, message = run["result_state"], run["state_message"]
    elif run["cancel_requested"]:
        result, message = "CANCELED", "Run was canceled."
    elif failed:
        result = "FAILED"
        first = failed[0]
        detail = first["error"] or first["state_message"] or first["result_state"]
        if len(failed) == 1:
            message = f"Task {first['task_key']} failed with message: {detail}"
        else:
            keys = ", ".join(r["task_key"] for r in failed)
            message = f"This run failed because {len(failed)} tasks failed: {keys}. {detail}"
    else:
        result, message = "SUCCESS", ""
    conn.execute(
        update(db.job_runs)
        .where(db.job_runs.c.run_id == job_run_id)
        .values(
            life_cycle_state="TERMINATED",
            result_state=result,
            state_message=message,
            end_time=func.now(),
        )
    )


# --- worker side -----------------------------------------------------------------------


def log_path_for(job_run_id: int, task_key: str, attempt: int) -> str:
    suffix = f".attempt{attempt}" if attempt else ""
    return str(Path(get_settings().logs_root) / str(job_run_id) / f"{task_key}{suffix}.log")


def claim_task(conn: Connection, worker_id: str) -> dict[str, Any] | None:
    """Claim the next runnable task run. Commit promptly; then call `mark_run_started`."""
    tr = db.task_runs
    row = (
        conn.execute(
            select(tr)
            .where(tr.c.life_cycle_state == "PENDING", tr.c.not_before <= func.now())
            .order_by(tr.c.not_before, tr.c.run_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    log_path = log_path_for(row["job_run_id"], row["task_key"], row["attempt_number"])
    conn.execute(
        update(tr)
        .where(tr.c.run_id == row["run_id"])
        .values(
            life_cycle_state="RUNNING",
            worker_id=worker_id,
            start_time=func.now(),
            heartbeat_at=func.now(),
            log_path=log_path,
        )
    )
    claimed = dict(row)
    claimed.update(life_cycle_state="RUNNING", worker_id=worker_id, log_path=log_path)
    return claimed


def mark_run_started(conn: Connection, job_run_id: int) -> None:
    conn.execute(
        update(db.job_runs)
        .where(db.job_runs.c.run_id == job_run_id, db.job_runs.c.life_cycle_state == "PENDING")
        .values(life_cycle_state="RUNNING")
    )


def heartbeat(conn: Connection, worker_id: str, task_run_ids: list[int]) -> set[int]:
    """Refresh heartbeats; return the ids whose cancellation was requested."""
    conn.execute(
        update(db.workers)
        .where(db.workers.c.worker_id == worker_id)
        .values(heartbeat_at=func.now())
    )
    if not task_run_ids:
        return set()
    tr = db.task_runs
    conn.execute(
        update(tr)
        .where(tr.c.run_id.in_(task_run_ids), tr.c.life_cycle_state == "RUNNING")
        .values(heartbeat_at=func.now())
    )
    rows = conn.execute(
        select(tr.c.run_id).where(tr.c.run_id.in_(task_run_ids), tr.c.cancel_requested.is_(True))
    )
    return {int(r[0]) for r in rows}


def _should_retry(task: dict[str, Any], attempt: int, result_state: str) -> bool:
    retries = int(task.get("max_retries") or 0)
    if retries < 0:
        retries = UNLIMITED_RETRIES_CAP
    if attempt >= retries:
        return False
    if result_state == "TIMEDOUT":
        return bool(task.get("retry_on_timeout"))
    return result_state == "FAILED"


def finish_task(
    conn: Connection,
    task_run_id: int,
    result_state: str,
    message: str = "",
    error: str | None = None,
    error_trace: str | None = None,
    output: dict[str, Any] | None = None,
    life_cycle_state: str = "TERMINATED",
) -> None:
    tr = db.task_runs
    job_run_id = conn.execute(select(tr.c.job_run_id).where(tr.c.run_id == task_run_id)).scalar()
    if job_run_id is None:
        raise NotFound(f"Run {task_run_id} does not exist.")
    run = (
        conn.execute(
            select(db.job_runs).where(db.job_runs.c.run_id == job_run_id).with_for_update()
        )
        .mappings()
        .one()
    )
    row = (
        conn.execute(select(tr).where(tr.c.run_id == task_run_id).with_for_update())
        .mappings()
        .one()
    )
    if row["life_cycle_state"] in TERMINAL:
        return
    conn.execute(
        update(tr)
        .where(tr.c.run_id == task_run_id)
        .values(
            life_cycle_state=life_cycle_state,
            result_state=result_state,
            state_message=message,
            error=error,
            error_trace=error_trace,
            output=output or {},
            end_time=func.now(),
        )
    )
    if not run["cancel_requested"] and _should_retry(
        row["task"], row["attempt_number"], result_state
    ):
        delay_ms = int(row["task"].get("min_retry_interval_millis") or 0)
        conn.execute(
            insert(tr).values(
                job_run_id=job_run_id,
                task_key=row["task_key"],
                task=row["task"],
                attempt_number=row["attempt_number"] + 1,
                life_cycle_state="PENDING",
                not_before=func.now() + timedelta(milliseconds=delay_ms),
                state_message=f"Retrying after attempt {row['attempt_number']} {result_state}",
            )
        )
        return
    advance(conn, job_run_id)


def cancel_run(conn: Connection, run_id: int, result_state: str | None = None) -> None:
    run = (
        conn.execute(select(db.job_runs).where(db.job_runs.c.run_id == run_id).with_for_update())
        .mappings()
        .first()
    )
    if run is None:
        raise NotFound(f"Run {run_id} does not exist.")
    if run["life_cycle_state"] in TERMINAL:
        return
    values: dict[str, Any] = {"cancel_requested": True}
    if result_state:
        values.update(result_state=result_state, state_message=f"Run {result_state.lower()}.")
    tr = db.task_runs
    conn.execute(
        update(tr)
        .where(tr.c.job_run_id == run_id, tr.c.life_cycle_state.in_(("BLOCKED", "PENDING")))
        .values(
            life_cycle_state="TERMINATED",
            result_state="CANCELED",
            state_message="Run was canceled.",
            end_time=func.now(),
        )
    )
    running = conn.execute(
        update(tr)
        .where(tr.c.job_run_id == run_id, tr.c.life_cycle_state == "RUNNING")
        .values(cancel_requested=True)
    ).rowcount
    if running:
        values["life_cycle_state"] = "TERMINATING"
    conn.execute(update(db.job_runs).where(db.job_runs.c.run_id == run_id).values(**values))
    advance(conn, run_id)


def reap(conn: Connection, stale_seconds: int = 60) -> list[int]:
    """Fail task runs whose worker stopped heartbeating."""
    tr = db.task_runs
    stale = conn.execute(
        select(tr.c.run_id, tr.c.worker_id).where(
            tr.c.life_cycle_state == "RUNNING",
            tr.c.heartbeat_at < func.now() - timedelta(seconds=stale_seconds),
        )
    ).all()
    for run_id, worker_id in stale:
        finish_task(
            conn,
            int(run_id),
            "FAILED",
            message=f"Worker {worker_id} stopped heartbeating for over {stale_seconds}s",
            error="Worker heartbeat lost",
            life_cycle_state="INTERNAL_ERROR",
        )
    return [int(r[0]) for r in stale]


def enforce_job_timeouts(conn: Connection) -> list[int]:
    rows = conn.execute(
        select(db.job_runs.c.run_id, db.job_runs.c.settings, db.job_runs.c.start_time).where(
            db.job_runs.c.life_cycle_state.in_(("PENDING", "RUNNING"))
        )
    ).all()
    timed_out = []
    for run_id, settings, start_time in rows:
        timeout = int(settings.get("timeout_seconds") or 0)
        if timeout and start_time + timedelta(seconds=timeout) < now():
            cancel_run(conn, int(run_id), result_state="TIMEDOUT")
            timed_out.append(int(run_id))
    return timed_out


# --- task values -------------------------------------------------------------------------


def set_task_value(conn: Connection, task_run_id: int, key: str, value: Any) -> None:
    tr = db.task_runs
    row = conn.execute(
        select(tr.c.job_run_id, tr.c.task_key).where(tr.c.run_id == task_run_id)
    ).first()
    if row is None:
        raise NotFound(f"Run {task_run_id} does not exist.")
    conn.execute(
        text(
            "INSERT INTO task_values (job_run_id, task_key, key, value) "
            "VALUES (:r, :t, :k, CAST(:v AS jsonb)) "
            "ON CONFLICT (job_run_id, task_key, key) DO UPDATE SET value = EXCLUDED.value"
        ),
        {"r": row[0], "t": row[1], "k": key, "v": _json(value)},
    )


def get_task_value(conn: Connection, job_run_id: int, task_key: str, key: str) -> Any:
    tv = db.task_values
    row = conn.execute(
        select(tv.c.value).where(
            and_(tv.c.job_run_id == job_run_id, tv.c.task_key == task_key, tv.c.key == key)
        )
    ).first()
    if row is None:
        raise NotFound(f"Task value '{key}' of task '{task_key}' is not set.")
    return row[0]


def task_values_for_run(conn: Connection, job_run_id: int) -> dict[str, dict[str, Any]]:
    tv = db.task_values
    out: dict[str, dict[str, Any]] = {}
    for task_key, key, value in conn.execute(
        select(tv.c.task_key, tv.c.key, tv.c.value).where(tv.c.job_run_id == job_run_id)
    ):
        out.setdefault(task_key, {})[key] = value
    return out


def _json(value: Any) -> str:
    import json

    return json.dumps(value)


# --- rendering (Jobs API 2.2 shapes) ---------------------------------------------------

_STATUS = {
    "BLOCKED": "BLOCKED",
    "PENDING": "PENDING",
    "QUEUED": "QUEUED",
    "RUNNING": "RUNNING",
    "TERMINATING": "TERMINATING",
    "TERMINATED": "TERMINATED",
    "SKIPPED": "TERMINATED",
    "INTERNAL_ERROR": "TERMINATED",
}
_TERMINATION = {
    "SUCCESS": ("SUCCESS", "SUCCESS"),
    "FAILED": ("RUN_EXECUTION_ERROR", "CLIENT_ERROR"),
    "TIMEDOUT": ("TIMED_OUT", "CLIENT_ERROR"),
    "CANCELED": ("USER_CANCELED", "SUCCESS"),
    "UPSTREAM_FAILED": ("UPSTREAM_FAILED", "CLIENT_ERROR"),
    "EXCLUDED": ("EXCLUDED", "SUCCESS"),
    "MAXIMUM_CONCURRENT_RUNS_REACHED": ("MAX_CONCURRENT_RUNS_EXCEEDED", "CLIENT_ERROR"),
}


def state_json(life: str, result: str | None, message: str) -> dict[str, Any]:
    state: dict[str, Any] = {
        "life_cycle_state": life,
        "state_message": message,
        "user_cancelled_or_timedout": result in ("CANCELED", "TIMEDOUT"),
    }
    if result:
        state["result_state"] = result
    return state


def status_json(life: str, result: str | None, message: str) -> dict[str, Any]:
    status: dict[str, Any] = {"state": _STATUS.get(life, life)}
    if life in TERMINAL:
        if life == "INTERNAL_ERROR":
            code, kind = "INTERNAL_ERROR", "INTERNAL_ERROR"
        else:
            code, kind = _TERMINATION.get(result or "", ("SUCCESS", "SUCCESS"))
        status["termination_details"] = {"code": code, "type": kind, "message": message}
    return status


def _task_json(row: dict[str, Any], job_id: int | None, attempts: int) -> dict[str, Any]:
    task = row["task"]
    start, end = row["start_time"], row["end_time"]
    execution = _ms(end) - _ms(start) if start and end else 0
    out = {k: v for k, v in task.items()}
    out.update(
        run_id=row["run_id"],
        attempt_number=row["attempt_number"],
        state=state_json(row["life_cycle_state"], row["result_state"], row["state_message"]),
        status=status_json(row["life_cycle_state"], row["result_state"], row["state_message"]),
        start_time=_ms(start),
        end_time=_ms(end),
        setup_duration=0,
        execution_duration=execution,
        cleanup_duration=0,
        run_page_url=_run_page_url(job_id, row["job_run_id"], row["run_id"]),
    )
    if attempts > 1:
        out["attempts"] = attempts
    if row["output"]:
        out["duckbricks_output"] = row["output"]
    return out


def _run_page_url(job_id: int | None, run_id: int, task_run_id: int | None = None) -> str:
    base = get_settings().public_url.rstrip("/")
    url = f"{base}/jobs/{job_id}/runs/{run_id}" if job_id else f"{base}/runs/{run_id}"
    return f"{url}?task_run={task_run_id}" if task_run_id else url


def run_json(conn: Connection, run: dict[str, Any], expand_tasks: bool = True) -> dict[str, Any]:
    start, end = run["start_time"], run["end_time"]
    duration = (_ms(end) if end else _ms(now())) - _ms(start)
    settings = run["settings"]
    defaults = {p["name"]: p.get("default", "") for p in settings.get("parameters") or []}
    out: dict[str, Any] = {
        "job_id": run["job_id"],
        "run_id": run["run_id"],
        "number_in_job": run["run_id"],
        "original_attempt_run_id": run["run_id"],
        "creator_user_name": get_settings().user_name,
        "run_name": run["run_name"],
        "run_type": run["run_type"],
        "trigger": run["trigger"],
        "state": state_json(run["life_cycle_state"], run["result_state"], run["state_message"]),
        "status": status_json(run["life_cycle_state"], run["result_state"], run["state_message"]),
        "start_time": _ms(start),
        "end_time": _ms(end),
        "setup_duration": 0,
        "execution_duration": duration,
        "cleanup_duration": 0,
        "run_duration": duration,
        "run_page_url": _run_page_url(run["job_id"], run["run_id"]),
        "job_parameters": [
            {"name": k, "default": defaults.get(k, ""), "value": v}
            for k, v in (run["job_parameters"] or {}).items()
        ],
        "format": "MULTI_TASK",
    }
    if settings.get("job_clusters"):
        out["job_clusters"] = settings["job_clusters"]
    if run["parent_run_id"]:
        out["parent_run_id"] = run["parent_run_id"]
    if expand_tasks:
        rows = (
            conn.execute(
                select(db.task_runs)
                .where(db.task_runs.c.job_run_id == run["run_id"])
                .order_by(db.task_runs.c.run_id)
            )
            .mappings()
            .all()
        )
        counts: dict[str, int] = {}
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            counts[row["task_key"]] = counts.get(row["task_key"], 0) + 1
            latest[row["task_key"]] = dict(row)
        order = [t["task_key"] for t in settings["tasks"]]
        out["tasks"] = [
            _task_json(latest[k], run["job_id"], counts[k]) for k in order if k in latest
        ]
    return out


def get_run_row(conn: Connection, run_id: int) -> dict[str, Any]:
    row = conn.execute(select(db.job_runs).where(db.job_runs.c.run_id == run_id)).mappings().first()
    if row is None:
        raise NotFound(f"Run {run_id} does not exist.")
    return dict(row)


def list_runs(
    conn: Connection,
    job_id: int | None = None,
    active_only: bool = False,
    completed_only: bool = False,
    limit: int = 25,
    offset: int = 0,
    run_type: str | None = None,
    start_time_from: int | None = None,
    start_time_to: int | None = None,
    expand_tasks: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    if active_only and completed_only:
        raise InvalidParameter("active_only and completed_only cannot both be true")
    jr = db.job_runs
    query = select(jr).order_by(jr.c.run_id.desc())
    if job_id is not None:
        query = query.where(jr.c.job_id == job_id)
    if active_only:
        query = query.where(jr.c.life_cycle_state.in_(ACTIVE_JOB_STATES))
    if completed_only:
        query = query.where(jr.c.life_cycle_state.in_(TERMINAL))
    if run_type:
        query = query.where(jr.c.run_type == run_type)
    if start_time_from:
        query = query.where(jr.c.start_time >= datetime.fromtimestamp(start_time_from / 1000, UTC))
    if start_time_to:
        query = query.where(jr.c.start_time <= datetime.fromtimestamp(start_time_to / 1000, UTC))
    rows = conn.execute(query.limit(limit + 1).offset(offset)).mappings().all()
    return [run_json(conn, dict(r), expand_tasks) for r in rows[:limit]], len(rows) > limit


def task_run_row(conn: Connection, task_run_id: int) -> dict[str, Any]:
    row = (
        conn.execute(select(db.task_runs).where(db.task_runs.c.run_id == task_run_id))
        .mappings()
        .first()
    )
    if row is None:
        raise NotFound(
            f"Run {task_run_id} does not exist or is not a task run. "
            "runs/get-output takes the run_id of a task."
        )
    return dict(row)


def read_log_tail(path: str | None, max_bytes: int = 64 * 1024) -> tuple[str, bool]:
    if not path:
        return "", False
    p = Path(path)
    if not p.exists():
        return "", False
    size = p.stat().st_size
    with p.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
        data = fh.read()
    return data.decode("utf-8", errors="replace"), size > max_bytes


def output_json(conn: Connection, task_run_id: int) -> dict[str, Any]:
    row = task_run_row(conn, task_run_id)
    run = get_run_row(conn, row["job_run_id"])
    logs, truncated = read_log_tail(row["log_path"])
    task_out = _task_json(row, run["job_id"], 1)
    out: dict[str, Any] = {
        "metadata": task_out,
        "logs": logs,
        "logs_truncated": truncated,
    }
    if row["error"]:
        out["error"] = row["error"]
    if row["error_trace"]:
        out["error_trace"] = row["error_trace"]
    output = row["output"] or {}
    if "notebook_output" in output:
        out["notebook_output"] = output["notebook_output"]
    if "dbt_output" in output:
        out["dbt_output"] = output["dbt_output"]
    if "run_job_output" in output:
        out["run_job_output"] = output["run_job_output"]
    if "sql_output" in output:
        out["sql_output"] = output["sql_output"]
    return out


def ensure_not_terminal(run: dict[str, Any]) -> None:
    if run["life_cycle_state"] in TERMINAL:
        raise InvalidState(f"Run {run['run_id']} has already finished")
