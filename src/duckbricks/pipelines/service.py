"""Worker side of a pipeline update: prepare inputs, run the runner, record the results."""

from __future__ import annotations

import glob
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import func

from duckbricks.core import db, pipelines
from duckbricks.core.jobs import node_type_for_task
from duckbricks.core.lineage import record_table_edges
from duckbricks.core.workspace import Workspace
from duckbricks.worker.executors import (
    Execution,
    Outcome,
    TaskFailed,
    local_path,
    run_subprocess,
    task_env,
)


def library_files(ws: Workspace, spec: dict[str, Any]) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for library in spec.get("libraries") or []:
        for kind in ("file", "notebook"):
            item = library.get(kind)
            if isinstance(item, dict) and item.get("path"):
                local = local_path(ws, item["path"], (".sql", ".py"))
                files.append({"path": item["path"], "local": str(local)})
        pattern = (library.get("glob") or {}).get("include")
        if pattern:
            local_pattern = str(ws.local(pattern.split("*", 1)[0] or "/")) + (
                pattern[len(pattern.split("*", 1)[0]) :] if "*" in pattern else ""
            )
            if not any(c in pattern for c in "*?["):
                local_pattern = local_pattern.rstrip("/") + "/**"
            for match in sorted(glob.glob(local_pattern, recursive=True)):
                if match.endswith((".sql", ".py")) and Path(match).is_file():
                    files.append({"path": ws.workspace_path(Path(match)), "local": match})
    if not files:
        raise TaskFailed("The pipeline's libraries matched no files")
    return files


def run_update_for_task(ex: Execution, task_spec: dict[str, Any]) -> Outcome:
    pipeline_id = str(task_spec["pipeline_id"])
    full_refresh = bool(task_spec.get("full_refresh"))
    engine = db.get_engine(ex.settings.control_url)
    with engine.begin() as conn:
        update_id = task_spec.get("_update_id") or pipelines.create_update(
            conn, pipeline_id, full_refresh, cause="JOB_TASK"
        )
        pipelines.set_update(conn, update_id, state="RUNNING", task_run_id=ex.row["run_id"])
        row = pipelines.get_row(conn, pipeline_id)
        processed = pipelines.processed_files(conn, pipeline_id)
    spec = row["spec"]
    ex.log(f"pipeline {row['name']} ({pipeline_id}) update {update_id}")
    state, message, outcome = "FAILED", "", {}
    try:
        files = library_files(Workspace(ex.settings), spec)
        payload = {
            "pipeline_id": pipeline_id,
            "update_id": update_id,
            "full_refresh": full_refresh,
            "catalog": spec["catalog"],
            "schema": spec.get("schema") or spec.get("target") or "default",
            "configuration": spec.get("configuration") or {},
            "files": files,
            "processed": {} if full_refresh else processed,
            "node_type": node_type_for_task(ex.run["settings"], ex.row["task"]),
        }
        with tempfile.TemporaryDirectory(prefix="dbk-pipe-") as tmp:
            outcome_file = Path(tmp) / "outcome.json"
            env = task_env(ex, {}, outcome_file)
            env["DBK_PIPELINE_RUN"] = json.dumps(payload)
            timeout = int(ex.row["task"].get("timeout_seconds") or 0)
            try:
                code = run_subprocess(
                    ex, [sys.executable, "-m", "duckbricks.pipelines.runner"], env, None, timeout
                )
            finally:
                if outcome_file.exists():
                    outcome = json.loads(outcome_file.read_text())
        if code == 0:
            state = "COMPLETED"
        else:
            message = outcome.get("error") or "Pipeline update failed"
    except TaskFailed as exc:
        state = "CANCELED" if exc.result_state == "CANCELED" else "FAILED"
        message = str(exc)
        if state == "FAILED":
            outcome.setdefault("events", []).append(
                {
                    "level": "ERROR",
                    "event_type": "update_progress",
                    "message": message,
                    "details": {},
                }
            )
        raise_after: TaskFailed | None = exc
    else:
        raise_after = None
    with engine.begin() as conn:
        for event in outcome.get("events", []):
            pipelines.add_event(
                conn,
                pipeline_id,
                update_id,
                event["level"],
                event["event_type"],
                event["message"],
                event.get("details"),
            )
        if state == "COMPLETED":
            pipelines.record_files(
                conn, pipeline_id, outcome.get("files", {}), outcome.get("reset", [])
            )
        pipelines.set_update(
            conn, update_id, state=state, end_time=func.now(), message=message or None
        )
    if state == "COMPLETED" and outcome.get("lineage"):
        record_table_edges(
            engine, [tuple(e) for e in outcome["lineage"]], "pipeline", pipeline_id, update_id
        )
    if raise_after is not None:
        raise raise_after
    output = {
        "pipeline_output": {"pipeline_id": pipeline_id, "update_id": update_id, "state": state}
    }
    if state != "COMPLETED":
        return Outcome("FAILED", message=message, error=message, output=output)
    return Outcome("SUCCESS", output=output)
