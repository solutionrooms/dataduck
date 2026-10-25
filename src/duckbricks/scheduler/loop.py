"""The scheduler loop, run inside the API process every 15 seconds.

Each tick: reap task runs with stale heartbeats, enforce job timeouts, fire cron schedules and
periodic triggers, poll file-arrival triggers, and stop idle warehouses. Missed schedule times
are not backfilled, matching Databricks.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, select, text

from duckbricks.core import db, runs
from duckbricks.core.config import Settings, get_settings
from duckbricks.scheduler.quartz import next_fire

log = logging.getLogger("duckbricks.scheduler")
TICK_SECONDS = 15.0
PERIOD_UNITS = {"HOURS": timedelta(hours=1), "DAYS": timedelta(days=1), "WEEKS": timedelta(weeks=1)}


def _set_fired(conn: Connection, job_id: int, when: datetime, state: dict[str, Any] | None) -> None:
    conn.execute(
        text(
            "INSERT INTO job_triggers (job_id, last_fired_at, state) "
            "VALUES (:j, :t, CAST(:s AS jsonb)) ON CONFLICT (job_id) DO UPDATE "
            "SET last_fired_at = EXCLUDED.last_fired_at, "
            "state = CASE WHEN :keep THEN job_triggers.state ELSE EXCLUDED.state END"
        ),
        {
            "j": job_id,
            "t": when,
            "s": __import__("json").dumps(state or {}),
            "keep": state is None,
        },
    )


def due_time(settings: dict[str, Any], baseline: datetime) -> datetime | None:
    """When the job's schedule or periodic trigger next fires after `baseline`, if active."""
    schedule = settings.get("schedule")
    if schedule and schedule.get("pause_status", "UNPAUSED") == "UNPAUSED":
        tz = ZoneInfo(schedule.get("timezone_id") or "UTC")
        return next_fire(schedule["quartz_cron_expression"], baseline.astimezone(tz))
    trigger = settings.get("trigger") or {}
    periodic = trigger.get("periodic")
    if periodic and trigger.get("pause_status", "UNPAUSED") == "UNPAUSED":
        return baseline + int(periodic.get("interval") or 1) * PERIOD_UNITS[periodic["unit"]]
    return None


class SchedulerLoop:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.extra_steps: list[Callable[[], None]] = []

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            self._stop.wait(TICK_SECONDS)

    def tick(self, now: datetime | None = None) -> list[int]:
        now = now or datetime.now(UTC)
        engine = db.get_engine(self.settings.control_url)
        with engine.begin() as conn:
            reaped = runs.reap(conn)
            if reaped:
                log.warning("reaped task runs with stale heartbeats: %s", reaped)
            runs.enforce_job_timeouts(conn)
        fired = self.fire_schedules(now)
        for step in self.extra_steps:
            try:
                step()
            except Exception:
                log.exception("scheduler step %s failed", getattr(step, "__name__", step))
        return fired

    def fire_schedules(self, now: datetime) -> list[int]:
        engine = db.get_engine(self.settings.control_url)
        fired = []
        with engine.connect() as conn:
            rows = conn.execute(
                select(
                    db.jobs.c.job_id,
                    db.jobs.c.settings,
                    db.jobs.c.updated_at,
                    db.job_triggers.c.last_fired_at,
                ).select_from(db.jobs.outerjoin(db.job_triggers))
            ).all()
        for job_id, settings, updated_at, last_fired in rows:
            baseline = max(t for t in (updated_at, last_fired) if t is not None)
            try:
                due = due_time(settings, baseline)
            except Exception as exc:
                log.warning("job %s has an invalid schedule: %s", job_id, exc)
                continue
            if due is None or due > now:
                continue
            with engine.begin() as conn:
                triggered = runs.trigger_job(conn, job_id, "PERIODIC")
                _set_fired(conn, job_id, now, None)
            fired.append(job_id)
            log.warning(
                "schedule fired job %s -> run %s%s",
                job_id,
                triggered.run_id,
                " (skipped: max concurrent runs)" if triggered.skipped else "",
            )
        return fired
