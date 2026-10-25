"""Task executors: turn one claimed task run into an outcome.

Code-running task types (`sql_task`, `spark_python_task`, `notebook_task`, `dbt_task`) run in
a fresh subprocess with their own DuckDB session. `condition_task`, `run_job_task` and
`pipeline_task` coordinate other work and run inside the worker.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from duckbricks.core import db, dynamic_values, jobs, runs
from duckbricks.core.config import Settings, get_settings
from duckbricks.core.errors import ApiError
from duckbricks.core.workspace import Workspace

POLL_SECONDS = 0.5


@dataclass
class Outcome:
    result_state: str
    message: str = ""
    error: str | None = None
    error_trace: str | None = None
    output: dict[str, Any] = field(default_factory=dict)


@dataclass
class Execution:
    row: dict[str, Any]  # task run
    run: dict[str, Any]  # job run
    settings: Settings
    cancel_event: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen[bytes] | None = None
    worker_stopping: bool = False

    @property
    def log_path(self) -> Path:
        return Path(self.row["log_path"])

    def log(self, message: str) -> None:
        with self.log_path.open("a") as fh:
            fh.write(message.rstrip("\n") + "\n")

    def cancel(self) -> None:
        self.cancel_event.set()
        _kill(self.process)


def _kill(process: subprocess.Popen[bytes] | None) -> None:
    if process is not None and process.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


class TaskFailed(Exception):
    def __init__(self, message: str, trace: str | None = None, result_state: str = "FAILED"):
        super().__init__(message)
        self.trace = trace
        self.result_state = result_state


# --- context and paths -------------------------------------------------------------------


def build_context(ex: Execution) -> dynamic_values.Context:
    engine = db.get_engine(ex.settings.control_url)
    with engine.connect() as conn:
        values = runs.task_values_for_run(conn, ex.run["run_id"])
        states = {}
        for task_key, row in runs._latest_attempts(conn, ex.run["run_id"]).items():
            states[task_key] = {"result_state": row["result_state"], "run_id": row["run_id"]}
    return dynamic_values.Context(
        job_id=ex.run["job_id"],
        job_name=ex.run["run_name"],
        run_id=ex.run["run_id"],
        task_key=ex.row["task_key"],
        task_run_id=ex.row["run_id"],
        attempt=ex.row["attempt_number"],
        start_time=ex.run["start_time"],
        trigger=ex.run["trigger"],
        parameters=ex.run["job_parameters"] or {},
        task_values=values,
        task_states=states,
    )


def local_path(ws: Workspace, path: str, suffixes: tuple[str, ...] = ()) -> Path:
    """Map a task's workspace path to the file on the workspace volume."""
    if path.startswith("file:"):
        path = path.removeprefix("file:")
    local = ws.local(path)
    if local.exists():
        return local
    for suffix in suffixes:
        candidate = local.with_name(local.name + suffix)
        if candidate.exists():
            return candidate
    raise TaskFailed(f"File {path} does not exist in the workspace")


def task_env(ex: Execution, extra: dict[str, Any], outcome_file: Path) -> dict[str, str]:
    from duckbricks.core.jobs import node_type_for_task

    ctx = {
        "job_id": ex.run["job_id"],
        "job_run_id": ex.run["run_id"],
        "task_run_id": ex.row["run_id"],
        "task_key": ex.row["task_key"],
        "attempt": ex.row["attempt_number"],
        "node_type": node_type_for_task(ex.run["settings"], ex.row["task"]),
        "parameters": ex.run["job_parameters"] or {},
        **extra,
    }
    env = dict(os.environ)
    env.update(
        DBK_TASK_CONTEXT=json.dumps(ctx),
        DBK_TASK_OUTCOME=str(outcome_file),
        PYTHONUNBUFFERED="1",
    )
    return env


# --- subprocess plumbing -------------------------------------------------------------------


def run_subprocess(
    ex: Execution,
    argv: list[str],
    env: dict[str, str],
    cwd: Path | None,
    timeout: int,
) -> int:
    """Run argv with output appended to the task log; honour cancel and timeout."""
    deadline = time.monotonic() + timeout if timeout else None
    with ex.log_path.open("ab") as log:
        process = subprocess.Popen(
            argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=cwd,
            start_new_session=True,
        )
        ex.process = process
        try:
            while True:
                try:
                    code = process.wait(timeout=POLL_SECONDS)
                    if ex.cancel_event.is_set():
                        raise TaskFailed("Run was canceled.", result_state="CANCELED")
                    return code
                except subprocess.TimeoutExpired:
                    if ex.cancel_event.is_set():
                        _kill(process)
                        process.wait()
                        raise TaskFailed("Run was canceled.", result_state="CANCELED") from None
                    if deadline and time.monotonic() > deadline:
                        _kill(process)
                        process.wait()
                        raise TaskFailed(
                            f"Task timed out after {timeout} seconds", result_state="TIMEDOUT"
                        ) from None
        finally:
            ex.process = None


EXCEPTION_LINE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Failure)):?(.*)$")


def error_from_log(path: Path) -> tuple[str, str | None]:
    """The last Python traceback in a log, as (error line, trace)."""
    text = path.read_text(errors="replace")[-200_000:] if path.exists() else ""
    marker = text.rfind("Traceback (most recent call last):")
    if marker >= 0:
        trace = text[marker:].strip()
        for line in trace.splitlines()[1:]:
            match = EXCEPTION_LINE.match(line)
            if match:
                name = match.group(1).rsplit(".", 1)[-1]
                return f"{name}:{match.group(2)}".rstrip(":"), trace
        lines = [line for line in trace.splitlines() if line.strip()]
        return (lines[-1] if lines else "Task failed"), trace
    lines = [line for line in text.splitlines() if line.strip()]
    tail = "\n".join(lines[-20:])
    return (lines[-1] if lines else "Task failed"), tail or None


def run_code(
    ex: Execution,
    argv: list[str],
    env_extra: dict[str, Any],
    cwd: Path | None,
    env_vars: dict[str, str] | None = None,
) -> Outcome:
    timeout = int(ex.row["task"].get("timeout_seconds") or 0)
    with tempfile.TemporaryDirectory(prefix="dbk-task-") as tmp:
        outcome_file = Path(tmp) / "outcome.json"
        env = task_env(ex, env_extra, outcome_file)
        env.update(env_vars or {})
        code = run_subprocess(ex, argv, env, cwd, timeout)
        output = json.loads(outcome_file.read_text()) if outcome_file.exists() else {}
    if code != 0:
        error, trace = error_from_log(ex.log_path)
        raise TaskFailed(output.get("error") or error, trace)
    return Outcome("SUCCESS", output=output)


# --- task types ----------------------------------------------------------------------------


def run_sql_task(ex: Execution, task: dict[str, Any]) -> Outcome:
    spec = task["sql_task"]
    ws = Workspace(ex.settings)
    path = local_path(ws, spec["file"]["path"], (".sql",))
    if spec.get("warehouse_id"):
        ex.log(f"warehouse_id {spec['warehouse_id']} given: running on job compute instead")
    payload = {
        "path": str(path),
        "workspace_path": spec["file"]["path"],
        "parameters": spec.get("parameters") or {},
        "node_type": jobs.node_type_for_task(ex.run["settings"], task),
    }
    argv = [sys.executable, "-m", "duckbricks.worker.sql_runner"]
    return run_code(ex, argv, {}, None, {"DBK_SQL_TASK": json.dumps(payload)})


def run_python_task(ex: Execution, task: dict[str, Any]) -> Outcome:
    spec = task["spark_python_task"]
    path = local_path(Workspace(ex.settings), spec["python_file"], (".py",))
    params = [str(p) for p in spec.get("parameters") or []]
    widgets = {k: str(v) for k, v in (ex.run["job_parameters"] or {}).items()}
    env_extra = {"widgets": widgets}
    argv = [sys.executable, str(path), *params]
    return run_code(ex, argv, env_extra, path.parent)


def run_notebook_task(ex: Execution, task: dict[str, Any]) -> Outcome:
    spec = task["notebook_task"]
    path = local_path(Workspace(ex.settings), spec["notebook_path"], (".py",))
    if path.suffix != ".py":
        raise TaskFailed(f"Notebook {spec['notebook_path']} is not a marimo .py notebook")
    widgets = {k: str(v) for k, v in (ex.run["job_parameters"] or {}).items()}
    widgets.update({k: str(v) for k, v in (spec.get("base_parameters") or {}).items()})
    argv = [sys.executable, str(path)]
    outcome = run_code(ex, argv, {"widgets": widgets}, path.parent)
    outcome.output.setdefault("notebook_output", {})
    return outcome


def _compare(left: str, right: str, op: str) -> bool:
    try:
        lv: Any = float(left)
        rv: Any = float(right)
    except ValueError:
        lv, rv = left, right
    return {
        "EQUAL_TO": lambda: lv == rv,
        "NOT_EQUAL": lambda: lv != rv,
        "GREATER_THAN": lambda: lv > rv,
        "GREATER_THAN_OR_EQUAL": lambda: lv >= rv,
        "LESS_THAN": lambda: lv < rv,
        "LESS_THAN_OR_EQUAL": lambda: lv <= rv,
    }[op]()


def run_condition_task(ex: Execution, task: dict[str, Any]) -> Outcome:
    spec = task["condition_task"]
    left, right, op = str(spec["left"]), str(spec["right"]), spec["op"]
    result = _compare(left, right, op)
    outcome = "true" if result else "false"
    ex.log(f"condition: {left!r} {op} {right!r} -> {outcome}")
    return Outcome(
        "SUCCESS",
        output={
            "condition_outcome": outcome,
            "condition_task": {"left": left, "right": right, "op": op, "outcome": outcome},
        },
    )


def run_run_job_task(ex: Execution, task: dict[str, Any]) -> Outcome:
    spec = task["run_job_task"]
    engine = db.get_engine(ex.settings.control_url)
    with engine.begin() as conn:
        triggered = runs.trigger_job(
            conn,
            int(spec["job_id"]),
            "RUN_JOB_TASK",
            spec.get("job_parameters"),
            parent_run_id=ex.row["run_id"],
        )
    child = triggered.run_id
    ex.log(f"triggered job {spec['job_id']} as run {child}")
    output = {"run_job_output": {"run_id": child}}
    if triggered.skipped:
        raise TaskFailed(f"Run of job {spec['job_id']} was skipped: {triggered.message}")
    timeout = int(task.get("timeout_seconds") or 0)
    deadline = time.monotonic() + timeout if timeout else None
    while True:
        with engine.connect() as conn:
            row = runs.get_run_row(conn, child)
        if row["life_cycle_state"] in runs.TERMINAL:
            break
        if ex.cancel_event.is_set() or (deadline and time.monotonic() > deadline):
            with engine.begin() as conn:
                runs.cancel_run(conn, child)
            state = "CANCELED" if ex.cancel_event.is_set() else "TIMEDOUT"
            raise TaskFailed(
                f"Run {child} of job {spec['job_id']} was {state.lower()}", None, state
            )
        time.sleep(2)
    ex.log(f"run {child} finished: {row['result_state']} {row['state_message']}")
    if row["result_state"] != "SUCCESS":
        message = f"Job {spec['job_id']} run {child} {row['result_state']}: {row['state_message']}"
        return Outcome("FAILED", message=message, error=message, output=output)
    return Outcome("SUCCESS", output=output)


def run_pipeline_task(ex: Execution, task: dict[str, Any]) -> Outcome:
    from duckbricks.pipelines.service import run_update_for_task

    return run_update_for_task(ex, task["pipeline_task"])


def run_dbt(ex: Execution, task: dict[str, Any]) -> Outcome:
    from duckbricks.worker.dbt import run_dbt_task

    return run_dbt_task(ex, task)


DISPATCH = {
    "sql_task": run_sql_task,
    "spark_python_task": run_python_task,
    "notebook_task": run_notebook_task,
    "condition_task": run_condition_task,
    "run_job_task": run_run_job_task,
    "pipeline_task": run_pipeline_task,
    "dbt_task": run_dbt,
}


def execute(ex: Execution) -> Outcome:
    ex.log_path.parent.mkdir(parents=True, exist_ok=True)
    ex.log(
        f"=== task {ex.row['task_key']} (run {ex.row['run_id']}, attempt "
        f"{ex.row['attempt_number']}) of job run {ex.run['run_id']} on {ex.row['worker_id']} ==="
    )
    try:
        task = dynamic_values.resolve(ex.row["task"], build_context(ex))
        kind = jobs.task_type(task)
        outcome = DISPATCH[kind](ex, task)
    except TaskFailed as exc:
        outcome = Outcome(exc.result_state, str(exc), str(exc), exc.trace)
    except ApiError as exc:
        outcome = Outcome("FAILED", exc.message, exc.message)
    except Exception as exc:  # executor bug or environment problem: report, don't crash
        trace = traceback.format_exc()
        outcome = Outcome("FAILED", f"{type(exc).__name__}: {exc}", str(exc), trace)
    if outcome.result_state != "SUCCESS" and outcome.error:
        outcome.message = outcome.message or outcome.error
    ex.log(f"=== {outcome.result_state}{': ' + outcome.message if outcome.message else ''} ===")
    return outcome


def load_run(settings: Settings, job_run_id: int) -> dict[str, Any]:
    with db.get_engine(settings.control_url).connect() as conn:
        row = conn.execute(select(db.job_runs).where(db.job_runs.c.run_id == job_run_id))
        return dict(row.mappings().one())


def default_settings() -> Settings:
    return get_settings()
