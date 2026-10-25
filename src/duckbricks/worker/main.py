"""The worker: claims task runs from the queue and runs them, `WORKER_SLOTS` at a time.

Run with `python -m duckbricks.worker.main`. Heartbeats every 10 s; the API's reaper fails task
runs whose heartbeat is over 60 s old. Cancel requests arrive with the heartbeat response.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb
from sqlalchemy import text, update

from duckbricks.core import db, runs
from duckbricks.core.config import Settings, get_settings
from duckbricks.worker.executors import Execution, execute, load_run

log = logging.getLogger("duckbricks.worker")
HEARTBEAT_SECONDS = 10.0
IDLE_POLL_SECONDS = 1.0


def dbt_versions() -> dict[str, str | None]:
    """dbt's version, distribution and the DuckDB driver it loads (SPEC section 9)."""
    out: dict[str, str | None] = {
        "dbt_version": None,
        "dbt_distribution": os.environ.get("DBK_DBT_DISTRIBUTION"),
        "dbt_duckdb_version": None,
    }
    try:
        version = subprocess.run(
            ["dbt", "--version"], capture_output=True, text=True, timeout=60, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return out
    out["dbt_version"] = version.strip().splitlines()[0] if version.strip() else None
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp)
        (project / "dbt_project.yml").write_text(
            "name: probe\nversion: '1.0'\nprofile: probe\nconfig-version: 2\n"
        )
        (project / "profiles.yml").write_text(
            "probe:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
            "      path: ':memory:'\n      schema: main\n      threads: 1\n"
        )
        try:
            shown = subprocess.run(
                ["dbt", "show", "--inline", "select version() as v", "--profiles-dir", tmp],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=tmp,
                check=False,
            )
            match = re.search(r"v\d+\.\d+\.\d+", shown.stdout + shown.stderr)
            out["dbt_duckdb_version"] = match.group() if match else None
        except (OSError, subprocess.TimeoutExpired):
            pass
    return out


class Worker:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.worker_id = f"{socket.gethostname()}-{os.getpid()}"
        self.slots = max(1, self.settings.worker_slots)
        self.pool = ThreadPoolExecutor(self.slots, thread_name_prefix="task")
        self.running: dict[int, Execution] = {}
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.engine = db.get_engine(self.settings.control_url)

    def register(self) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO workers (worker_id, hostname, slots, duckdb_version) "
                    "VALUES (:w, :h, :s, :d) ON CONFLICT (worker_id) DO UPDATE "
                    "SET heartbeat_at = now()"
                ),
                {
                    "w": self.worker_id,
                    "h": socket.gethostname(),
                    "s": self.slots,
                    "d": duckdb.__version__,
                },
            )
        threading.Thread(target=self._record_dbt, daemon=True).start()

    def _record_dbt(self) -> None:
        versions = dbt_versions()
        with self.engine.begin() as conn:
            conn.execute(
                update(db.workers)
                .where(db.workers.c.worker_id == self.worker_id)
                .values(**versions)
            )
        log.warning("dbt: %s", versions)

    def heartbeat_loop(self) -> None:
        while not self.stopping.wait(HEARTBEAT_SECONDS):
            try:
                with self.lock:
                    ids = list(self.running)
                with self.engine.begin() as conn:
                    cancel = runs.heartbeat(conn, self.worker_id, ids)
                for run_id in cancel:
                    with self.lock:
                        ex = self.running.get(run_id)
                    if ex is not None and not ex.cancel_event.is_set():
                        log.warning("cancelling task run %s", run_id)
                        ex.cancel()
            except Exception:
                log.exception("heartbeat failed")

    def claim(self) -> Execution | None:
        with self.engine.begin() as conn:
            row = runs.claim_task(conn, self.worker_id)
        if row is None:
            return None
        with self.engine.begin() as conn:
            runs.mark_run_started(conn, row["job_run_id"])
        return Execution(
            row=row, run=load_run(self.settings, row["job_run_id"]), settings=self.settings
        )

    def _run(self, ex: Execution) -> None:
        run_id = ex.row["run_id"]
        try:
            outcome = execute(ex)
            if ex.worker_stopping:
                with self.engine.begin() as conn:
                    runs.finish_task(
                        conn,
                        run_id,
                        "FAILED",
                        "The worker stopped while the task was running",
                        "Worker stopped",
                        life_cycle_state="INTERNAL_ERROR",
                    )
                return
            with self.engine.begin() as conn:
                runs.finish_task(
                    conn,
                    run_id,
                    outcome.result_state,
                    outcome.message,
                    outcome.error,
                    outcome.error_trace,
                    outcome.output,
                )
        except Exception:
            log.exception("task run %s crashed the executor", run_id)
            with self.engine.begin() as conn:
                runs.finish_task(
                    conn,
                    run_id,
                    "FAILED",
                    "Worker error",
                    "Worker error",
                    life_cycle_state="INTERNAL_ERROR",
                )
        finally:
            with self.lock:
                self.running.pop(run_id, None)

    def serve(self) -> None:
        self.register()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        log.warning("worker %s started with %s slots", self.worker_id, self.slots)
        while not self.stopping.is_set():
            with self.lock:
                free = self.slots - len(self.running)
            ex = self.claim() if free > 0 else None
            if ex is None:
                self.stopping.wait(IDLE_POLL_SECONDS)
                continue
            log.warning(
                "running task %s (%s) of job run %s",
                ex.row["task_key"],
                ex.row["run_id"],
                ex.row["job_run_id"],
            )
            with self.lock:
                self.running[ex.row["run_id"]] = ex
            self.pool.submit(self._run, ex)
        self.shutdown()

    def shutdown(self) -> None:
        with self.lock:
            executions = list(self.running.values())
        for ex in executions:
            ex.worker_stopping = True
            ex.cancel()
        self.pool.shutdown(wait=True, cancel_futures=True)

    def stop(self, *_: object) -> None:
        self.stopping.set()


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
    worker = Worker()
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    while True:
        try:
            worker.serve()
            return
        except Exception:
            log.exception("worker loop failed; retrying in 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
