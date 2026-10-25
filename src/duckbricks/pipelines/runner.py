"""Runs one pipeline update in a fresh process: `python -m duckbricks.pipelines.runner`.

Input: `DBK_PIPELINE_RUN` (JSON). Output: the outcome file (`DBK_TASK_OUTCOME`) with the event
log, newly processed files per streaming table, and table lineage. The worker records those.

Materialized views are fully recomputed. Streaming tables read only files not seen before
(incremental by file); a full refresh replaces them. Expectations count failing rows; `DROP ROW`
removes them, `FAIL UPDATE` stops the update, no action keeps them and warns.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from duckbricks.pipelines.parser import (
    Dataset,
    PipelineSqlError,
    duckdb_reader,
    find_read_files,
    parse_statement,
    references,
    rewrite_metadata_columns,
    split_statements,
    strip_stream,
)

FILE_EXTENSIONS = {
    "csv": (".csv", ".csv.gz", ".tsv"),
    "json": (".json", ".jsonl", ".ndjson"),
    "parquet": (".parquet",),
}


class Run:
    def __init__(self, spec: dict[str, Any]):
        self.spec = spec
        self.catalog: str = spec["catalog"]
        self.schema: str = spec["schema"]
        self.events: list[dict[str, Any]] = []
        self.files: dict[str, list[str]] = {}
        self.reset: list[str] = []
        self.lineage: list[tuple[str, str]] = []

    def event(self, level: str, event_type: str, message: str, **details: Any) -> None:
        print(f"[{level}] {message}", flush=True)
        self.events.append(
            {
                "level": level,
                "event_type": event_type,
                "message": message,
                "details": details,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    def fq(self, name: str) -> str:
        parts = name.split(".")
        if len(parts) == 1:
            return f"{self.catalog}.{self.schema}.{parts[0]}"
        if len(parts) == 2:
            return f"{self.catalog}.{parts[0]}.{parts[1]}"
        return name


def q(name: str) -> str:
    return ".".join('"' + p.replace('"', '""') + '"' for p in name.split("."))


def load_datasets(run: Run) -> list[Dataset]:
    datasets = []
    for item in run.spec["files"]:
        path = Path(item["local"])
        if path.suffix == ".py":
            raise PipelineSqlError(
                f"{item['path']}: Python pipeline sources are not supported; use SQL files"
            )
        for statement in split_statements(path.read_text()):
            dataset = parse_statement(statement, item["path"])
            if dataset is not None:
                datasets.append(dataset)
    if not datasets:
        raise PipelineSqlError("The pipeline defines no datasets")
    return datasets


def order(run: Run, datasets: list[Dataset]) -> list[tuple[Dataset, set[str]]]:
    by_name: dict[str, Dataset] = {}
    for d in datasets:
        key = d.name if d.temporary and "." not in d.name else run.fq(d.name)
        if key in by_name:
            raise PipelineSqlError(f"Dataset {key} is defined twice")
        by_name[key] = d
    short = {k.rsplit(".", 1)[-1]: k for k in by_name}
    deps: dict[str, set[str]] = {}
    for key, d in by_name.items():
        found = set()
        for ref in references(d.query):
            target = ref if ref in by_name else run.fq(ref) if run.fq(ref) in by_name else None
            if target is None and "." not in ref:
                target = short.get(ref)
            if target and target != key:
                found.add(target)
            elif "." in ref or ref not in short:
                run.lineage.append((run.fq(ref), key))
        deps[key] = found
    ordered, state = [], {}

    def visit(key: str, path: list[str]) -> None:
        if state.get(key) == 2:
            return
        if state.get(key) == 1:
            raise PipelineSqlError("Datasets form a cycle: " + " -> ".join([*path, key]))
        state[key] = 1
        for dep in sorted(deps[key]):
            visit(dep, [*path, key])
        state[key] = 2
        ordered.append(key)

    for key in by_name:
        visit(key, [])
    for key in ordered:
        for dep in deps[key]:
            if not by_name[dep].temporary and not by_name[key].temporary:
                run.lineage.append((dep, key))
    return [(by_name[k], deps[k]) for k in ordered]


def list_files(con: Any, path: str, fmt: str) -> list[str]:
    pattern = path if any(c in path for c in "*?[") else path.rstrip("/") + "/**"
    rows = con.execute("SELECT file FROM glob(?) ORDER BY file", [pattern]).fetchall()
    exts = FILE_EXTENSIONS.get(fmt, ())
    return [r[0] for r in rows if not exts or r[0].lower().endswith(exts)]


def table_exists(con: Any, name: str) -> bool:
    catalog, schema, table = name.split(".")
    row = con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = ? AND schema_name = ? "
        "AND table_name = ?",
        [catalog, schema, table],
    ).fetchone()
    return bool(row and row[0])


def build(run: Run, con: Any, dataset: Dataset, key: str) -> None:
    started = time.monotonic()
    query = rewrite_metadata_columns(dataset.query)
    full_refresh = bool(run.spec.get("full_refresh"))
    exists = dataset.kind != "VIEW" and "." in key and table_exists(con, key)
    incremental = dataset.kind == "STREAMING_TABLE" and exists and not full_refresh
    new_files: list[str] = []
    for warning in dataset.warnings:
        run.event("WARN", "flow_definition", f"{key}: {warning}")
    for rf in reversed(find_read_files(query)):
        files = list_files(con, rf.path, rf.format)
        if incremental:
            seen = set(run.spec["processed"].get(key, []))
            files = [f for f in files if f not in seen]
        if dataset.kind == "STREAMING_TABLE":
            new_files += files
        if not files:
            message = (
                f"{key}: no new files under {rf.path}"
                if incremental
                else f"{key}: no files found under {rf.path}; dataset not built"
            )
            run.event(
                "INFO" if incremental else "WARN",
                "flow_progress",
                message,
                flow_progress={"status": "SKIPPED", "metrics": {"num_output_rows": 0}},
            )
            return
        reader, warnings = duckdb_reader(rf, files)
        for warning in warnings:
            run.event("WARN", "flow_definition", f"{key}: {warning}")
        query = query[: rf.start] + reader + query[rf.end :]
    query = strip_stream(query)

    if dataset.kind == "VIEW":
        temp = "TEMP " if dataset.temporary else ""
        target = q(dataset.name) if dataset.temporary else q(key)
        con.execute(f"CREATE OR REPLACE {temp}VIEW {target} AS {query}")
        run.event(
            "INFO", "flow_progress", f"{key}: view created", flow_progress={"status": "COMPLETED"}
        )
        return

    expectations = []
    where = []
    if dataset.expectations:
        checks = ", ".join(
            f"count(*) FILTER (WHERE NOT coalesce(({e.expr}), false))" for e in dataset.expectations
        )
        row = con.execute(f"SELECT count(*), {checks} FROM ({query}) AS _src").fetchone()
        total = int(row[0])
        for e, failed in zip(dataset.expectations, row[1:], strict=True):
            failed = int(failed)
            expectations.append(
                {
                    "name": e.name,
                    "dataset": key,
                    "passed_records": total - failed,
                    "failed_records": failed,
                    "action": e.action,
                }
            )
            if e.action == "FAIL" and failed:
                run.event(
                    "ERROR",
                    "flow_progress",
                    f"{key}: expectation {e.name} failed on {failed} row(s); FAIL UPDATE",
                    flow_progress={
                        "status": "FAILED",
                        "data_quality": {"expectations": expectations},
                    },
                )
                raise PipelineSqlError(
                    f"Expectation {e.name} on {key} failed for {failed} of {total} row(s) "
                    f"(ON VIOLATION FAIL UPDATE): {e.expr}"
                )
            if e.action == "DROP":
                where.append(f"coalesce(({e.expr}), false)")
    filtered = f"SELECT * FROM ({query}) AS _src" + (
        f" WHERE {' AND '.join(where)}" if where else ""
    )
    dropped = sum(x["failed_records"] for x in expectations if x["action"] == "DROP")

    if dataset.temporary and "." not in dataset.name:
        con.execute(f"CREATE OR REPLACE TEMP TABLE {q(dataset.name)} AS {filtered}")
        written = con.execute(f"SELECT count(*) FROM {q(dataset.name)}").fetchone()[0]
    elif incremental:
        written = con.execute(f"INSERT INTO {q(key)} BY NAME {filtered}").fetchone()[0]
    else:
        con.execute(f"CREATE OR REPLACE TABLE {q(key)} AS {filtered}")
        written = con.execute(f"SELECT count(*) FROM {q(key)}").fetchone()[0]
        if dataset.kind == "STREAMING_TABLE":
            run.reset.append(key)
    if dataset.comment and not dataset.temporary:
        with contextlib.suppress(Exception):  # best effort on DuckLake
            con.execute(
                f"COMMENT ON TABLE {q(key)} IS '{dataset.comment.replace(chr(39), chr(39) * 2)}'"
            )
    if new_files:
        run.files[key] = new_files
    for e in expectations:
        if e["action"] == "WARN" and e["failed_records"]:
            run.event(
                "WARN",
                "flow_progress",
                f"{key}: expectation {e['name']} failed on {e['failed_records']} row(s) (kept)",
            )
    mode = "appended" if incremental else "written"
    drop_text = f", {dropped} row(s) dropped by expectations" if dropped else ""
    run.event(
        "INFO",
        "flow_progress",
        f"{key}: {written} row(s) {mode}{drop_text} ({time.monotonic() - started:.1f}s)",
        flow_progress={
            "status": "COMPLETED",
            "metrics": {"num_output_rows": int(written)},
            "data_quality": {"dropped_records": dropped, "expectations": expectations},
        },
        dataset=key,
        kind=dataset.kind,
        incremental=incremental,
        new_files=len(new_files),
    )


def main() -> int:
    spec = json.loads(os.environ["DBK_PIPELINE_RUN"])
    run = Run(spec)
    outcome_path = os.environ.get("DBK_TASK_OUTCOME")
    code = 0
    try:
        from duckbricks.core.session import open_session, sql_identifier

        datasets = load_datasets(run)
        plan = order(run, datasets)
        run.event(
            "INFO",
            "update_progress",
            f"Planning complete: {len(plan)} dataset(s) in {run.catalog}.{run.schema}"
            + (" (full refresh)" if spec.get("full_refresh") else ""),
        )
        con = open_session(spec.get("node_type"), default_catalog=run.catalog)
        target = f"{sql_identifier(run.catalog)}.{sql_identifier(run.schema)}"
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {target}")
        con.execute(f"USE {target}")
        for key, value in (spec.get("configuration") or {}).items():
            run.event("INFO", "flow_definition", f"configuration {key}={value} (not applied)")
        for dataset, _ in plan:
            key = (
                dataset.name
                if dataset.temporary and "." not in dataset.name
                else run.fq(dataset.name)
            )
            build(run, con, dataset, key)
        con.close()
        run.event("INFO", "update_progress", "Update COMPLETED")
    except Exception as exc:
        code = 1
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        if not isinstance(exc, PipelineSqlError):
            traceback.print_exc()
        run.event("ERROR", "update_progress", f"Update FAILED: {message}")
        run.error = message  # type: ignore[attr-defined]
    finally:
        if outcome_path:
            Path(outcome_path).write_text(
                json.dumps(
                    {
                        "events": run.events,
                        "files": run.files,
                        "reset": run.reset,
                        "lineage": sorted(set(run.lineage)),
                        "error": getattr(run, "error", None),
                    }
                )
            )
    return code


if __name__ == "__main__":
    sys.exit(main())
