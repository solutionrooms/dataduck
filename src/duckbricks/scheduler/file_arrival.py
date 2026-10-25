"""File arrival triggers: run a job when new files appear under an s3:// prefix.

A scheduler-loop step. The first poll of a trigger records the files already there without
firing, as Databricks does. Later polls fire when new files exist, honouring
`min_time_between_triggers_seconds` and `wait_after_last_change_seconds`. The files seen so far
live in `job_triggers.state`.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import duckdb
from sqlalchemy import select, text
from sqlalchemy.engine import Connection

from duckbricks.core import db, runs
from duckbricks.core.config import get_settings
from duckbricks.core.session import open_session

log = logging.getLogger("duckbricks.scheduler")
_lock = threading.Lock()
_session: duckdb.DuckDBPyConnection | None = None


def _list_files(url: str) -> list[str]:
    """Every file under an s3:// prefix (or matching a glob)."""
    global _session
    pattern = url if any(c in url for c in "*?[") else url.rstrip("/") + "/**"
    with _lock:
        if _session is None:
            _session = open_session(get_settings().api_node_type, catalogs=[])
        rows = _session.execute("SELECT file FROM glob(?)", [pattern]).fetchall()
    return sorted(r[0] for r in rows)


def _save(conn: Connection, job_id: int, state: dict[str, Any], fired_at: datetime | None) -> None:
    conn.execute(
        text(
            "INSERT INTO job_triggers (job_id, last_fired_at, state) "
            "VALUES (:j, :t, CAST(:s AS jsonb)) ON CONFLICT (job_id) DO UPDATE SET "
            "state = EXCLUDED.state, "
            "last_fired_at = COALESCE(EXCLUDED.last_fired_at, job_triggers.last_fired_at)"
        ),
        {"j": job_id, "t": fired_at, "s": json.dumps(state)},
    )


def check_trigger(
    conn: Connection,
    job_id: int,
    trigger: dict[str, Any],
    state: dict[str, Any] | None,
    last_fired: datetime | None,
    files: list[str],
    now: datetime,
) -> bool:
    """Update the trigger's state from the current file list; fire the job if due."""
    spec = trigger["file_arrival"]
    if state is None or "seen" not in state or state.get("url") != spec["url"]:
        _save(conn, job_id, {"url": spec["url"], "seen": files, "pending": []}, None)
        return False
    seen = set(state["seen"])
    pending = sorted(set(state.get("pending", [])) | {f for f in files if f not in seen})
    last_change = state.get("last_change")
    if len(pending) != len(state.get("pending", [])):
        last_change = now.isoformat()
    fire = bool(pending)
    min_gap = int(spec.get("min_time_between_triggers_seconds") or 0)
    if fire and last_fired and now - last_fired < timedelta(seconds=min_gap):
        fire = False
    settle = int(spec.get("wait_after_last_change_seconds") or 0)
    if (
        fire
        and last_change
        and now - datetime.fromisoformat(last_change) < timedelta(seconds=settle)
    ):
        fire = False
    if fire:
        triggered = runs.trigger_job(conn, job_id, "FILE_ARRIVAL")
        log.warning(
            "file arrival fired job %s -> run %s (%d new file(s))",
            job_id,
            triggered.run_id,
            len(pending),
        )
        _save(conn, job_id, {"url": spec["url"], "seen": files, "pending": []}, now)
        return True
    _save(
        conn,
        job_id,
        {"url": spec["url"], "seen": sorted(seen), "pending": pending, "last_change": last_change},
        None,
    )
    return False


def poll_file_arrivals() -> None:
    """Scheduler step: check every unpaused file arrival trigger."""
    engine = db.get_engine(get_settings().control_url)
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                db.jobs.c.job_id,
                db.jobs.c.settings,
                db.job_triggers.c.state,
                db.job_triggers.c.last_fired_at,
            ).select_from(db.jobs.outerjoin(db.job_triggers))
        ).all()
    now = datetime.now(UTC)
    for job_id, settings, state, last_fired in rows:
        trigger = settings.get("trigger") or {}
        if "file_arrival" not in trigger or trigger.get("pause_status") == "PAUSED":
            continue
        try:
            files = _list_files(trigger["file_arrival"]["url"])
        except Exception as exc:  # a bad prefix must not stop other triggers
            log.warning("file arrival trigger of job %s cannot list files: %s", job_id, exc)
            continue
        with engine.begin() as conn:
            check_trigger(conn, job_id, trigger, state or None, last_fired, files, now)
