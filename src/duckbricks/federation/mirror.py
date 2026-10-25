"""The mirror: copy chosen tables of a foreign catalog into its own DuckLake (ADR 0013).

A sync lists the remote tables of the configured schemas (one Unity Catalog call per schema,
which includes each table's Delta version), plans what to do, and then:

- copies a table whose version changed (or that is new, failed last time, or whose filter
  changed) with `CREATE OR REPLACE TABLE ... AS SELECT * FROM delta_scan(...)`, using
  credentials vended for that table: readers see the old copy or the new one, never half;
- copies views through the connection's SQL warehouse (Arrow over external links);
- skips unchanged tables, and drops local copies of tables no longer selected.

Afterwards it expires DuckLake snapshots older than `keep_history_hours` and deletes their files,
so by default only one copy of each table stays on disk. Syncs are queued in `mirror_syncs` by
the API (manual), the scheduler (nightly schedule and polls) and run by the worker.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import duckdb
import sqlglot
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Connection, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlglot import exp

from duckbricks.core import db
from duckbricks.core.catalogs import get_catalog_info
from duckbricks.core.config import Settings, get_settings
from duckbricks.core.errors import InvalidParameter, NotFound
from duckbricks.core.session import open_session, sql_identifier, sql_literal
from duckbricks.federation.connections import get_connection
from duckbricks.federation.databricks import DatabricksClient, RemoteTable, enable_federation
from duckbricks.scheduler.quartz import next_fire

log = logging.getLogger("duckbricks.mirror")
STALE_AFTER = timedelta(minutes=10)
HEARTBEAT_SECONDS = 30.0
ACTIVE_STATES = ("QUEUED", "RUNNING")


class MirrorSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quartz_cron_expression: str = "0 0 2 * * ?"
    timezone_id: str = "UTC"
    pause_status: Literal["PAUSED", "UNPAUSED"] = "UNPAUSED"


class MirrorConfig(BaseModel):
    """What to mirror. Table keys and patterns are `schema.table` (globs allowed, any case)."""

    model_config = ConfigDict(extra="forbid")
    schemas: list[str] = Field(min_length=1, description="Remote schemas to mirror")
    include: list[str] = Field(
        default_factory=list, description="Only these tables (globs); empty means all"
    )
    exclude: list[str] = Field(default_factory=list, description="Never these tables (globs)")
    filters: dict[str, str] = Field(
        default_factory=dict,
        description="Row filters: `schema.table` -> DuckDB predicate. The table is `source`; "
        "scalar subqueries such as (SELECT max(x) FROM source) are evaluated first",
    )
    include_views: bool = True
    schedule: MirrorSchedule | None = Field(default_factory=MirrorSchedule)
    poll_minutes: dict[str, int] = Field(
        default_factory=dict,
        description="Near-real-time: `schema.table` glob -> minutes between version checks",
    )
    keep_history_hours: int = Field(0, ge=0, description="DuckLake snapshots to keep (hours)")

    @field_validator("filters")
    @classmethod
    def _filters_parse(cls, value: dict[str, str]) -> dict[str, str]:
        for key, predicate in value.items():
            _check_key(key)
            try:
                sqlglot.parse_one(f"SELECT 1 FROM source WHERE {predicate}", read="duckdb")
            except sqlglot.errors.ParseError as exc:
                raise ValueError(f"filters[{key}] is not a valid predicate: {exc}") from None
        return {k.lower(): v for k, v in value.items()}

    @field_validator("poll_minutes")
    @classmethod
    def _poll_positive(cls, value: dict[str, int]) -> dict[str, int]:
        for key, minutes in value.items():
            if minutes < 1:
                raise ValueError(f"poll_minutes[{key}] must be at least 1")
        return value

    @field_validator("schedule")
    @classmethod
    def _schedule_valid(cls, value: MirrorSchedule | None) -> MirrorSchedule | None:
        if value is not None:
            try:
                next_fire(value.quartz_cron_expression, datetime.now(ZoneInfo(value.timezone_id)))
            except Exception as exc:
                raise ValueError(f"invalid schedule: {exc}") from None
        return value


def _check_key(key: str) -> None:
    if key.count(".") != 1:
        raise ValueError(f"'{key}' must be schema.table")


def matches(key: str, patterns: list[str]) -> bool:
    key = key.lower()
    return any(fnmatch.fnmatchcase(key, p.lower()) for p in patterns)


@dataclass(frozen=True)
class Action:
    kind: Literal["COPY", "SKIP", "DROP"]
    schema: str
    name: str
    reason: str
    table: RemoteTable | None = None
    status: str = "SYNCED"  # for DROP: EXCLUDED or DROPPED

    @property
    def key(self) -> str:
        return f"{self.schema}.{self.name}"


def plan(
    config: MirrorConfig,
    remote: list[RemoteTable],
    state: dict[tuple[str, str], dict[str, Any]],
    *,
    only: list[str] | None = None,
    full_refresh: bool = False,
    trigger: str = "MANUAL",
) -> list[Action]:
    """Decide what a sync does. Pure: no I/O."""
    listed = {(t.schema, t.name): t for t in remote}
    selected: dict[tuple[str, str], RemoteTable] = {}
    for key, t in listed.items():
        if config.include and not matches(t.key, config.include):
            continue
        if matches(t.key, config.exclude) or (t.is_view and not config.include_views):
            continue
        selected[key] = t
    actions: list[Action] = []
    for (schema, name), t in sorted(selected.items()):
        if only and not matches(t.key, only):
            continue
        row = state.get((schema, name))
        wanted_filter = config.filters.get(t.key.lower())
        if t.is_view:
            if trigger == "POLL":
                actions.append(Action("SKIP", schema, name, "views refresh on the schedule", t))
            else:
                actions.append(Action("COPY", schema, name, "view (no version to compare)", t))
        elif full_refresh:
            actions.append(Action("COPY", schema, name, "full refresh", t))
        elif row is None or row["status"] not in ("SYNCED",):
            reason = "new" if row is None or row["status"] != "FAILED" else "retry after failure"
            actions.append(Action("COPY", schema, name, reason, t))
        elif t.version is None:
            actions.append(Action("COPY", schema, name, "remote version unknown", t))
        elif row["remote_version"] != t.version:
            actions.append(
                Action("COPY", schema, name, f"version {row['remote_version']} -> {t.version}", t)
            )
        elif (row.get("filter") or None) != (wanted_filter or None):
            actions.append(Action("COPY", schema, name, "filter changed", t))
        else:
            actions.append(Action("SKIP", schema, name, f"unchanged (version {t.version})", t))
    if only:
        return actions
    for (schema, name), row in sorted(state.items()):
        if (schema, name) in selected or row["status"] in ("EXCLUDED", "DROPPED"):
            continue
        if (schema, name) in listed:
            actions.append(
                Action("DROP", schema, name, "excluded by the config", status="EXCLUDED")
            )
        elif schema.lower() not in {s.lower() for s in config.schemas}:
            actions.append(
                Action("DROP", schema, name, "schema no longer mirrored", status="EXCLUDED")
            )
        else:
            actions.append(Action("DROP", schema, name, "dropped remotely", status="DROPPED"))
    return actions


def resolve_filter(con: duckdb.DuckDBPyConnection, predicate: str) -> str:
    """Evaluate scalar subqueries in a filter so the remaining comparison can be pushed into
    the Delta scan (file and row-group skipping). `source` must already exist as a view."""
    tree = sqlglot.parse_one(f"SELECT 1 FROM source WHERE {predicate}", read="duckdb")
    where = tree.args["where"].this
    for sub in list(where.find_all(exp.Subquery)):
        value = con.execute(sub.this.sql(dialect="duckdb")).fetchone()
        result = value[0] if value else None
        if result is None:
            literal: exp.Expression = exp.Null()
        elif isinstance(result, bool):
            literal = exp.Boolean(this=result)
        elif isinstance(result, int | float):
            literal = exp.Literal.number(result)
        else:
            literal = exp.Literal.string(str(result))
        sub.replace(literal)
    return str(where.sql(dialect="duckdb"))


def local_name(catalog: str, schema: str, name: str) -> str:
    return ".".join(sql_identifier(p) for p in (catalog, schema, name))


def copy_table(
    con: duckdb.DuckDBPyConnection,
    client: DatabricksClient,
    catalog: str,
    t: RemoteTable,
    predicate: str | None,
) -> tuple[int, str | None]:
    """Copy one Delta table into the mirror. Returns (rows, the filter actually applied)."""
    credential = client.vend(t)
    con.execute(credential.secret_sql)
    con.execute(
        "CREATE OR REPLACE TEMP VIEW source AS "
        f"SELECT * FROM delta_scan({sql_literal(credential.location)})"
    )
    where = resolve_filter(con, predicate) if predicate else None
    target = local_name(catalog, t.schema, t.name)
    con.execute(
        f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM source"
        + (f" WHERE {where}" if where else "")
    )
    count = con.execute(f"SELECT count(*) FROM {target}").fetchone()
    return (int(count[0]) if count else 0), where


def copy_view(
    con: duckdb.DuckDBPyConnection,
    client: DatabricksClient,
    catalog: str,
    t: RemoteTable,
    predicate: str | None,
) -> tuple[int, str | None]:
    """Copy a view through the SQL warehouse, in one DuckLake transaction."""
    remote = ".".join(f"`{p}`" for p in (t.catalog, t.schema, t.name))
    query = f"SELECT * FROM {remote}"
    if predicate:
        query += " WHERE " + sqlglot.transpile(predicate, read="duckdb", write="databricks")[0]
    target = local_name(catalog, t.schema, t.name)
    rows, created = 0, False
    con.execute("BEGIN")
    try:
        for chunk in client.arrow_tables(query):
            con.register("dbx_chunk", chunk)
            verb = f"INSERT INTO {target}" if created else f"CREATE OR REPLACE TABLE {target} AS"
            con.execute(f"{verb} SELECT * FROM dbx_chunk")
            con.unregister("dbx_chunk")
            created, rows = True, rows + chunk.num_rows
        if not created:
            raise RuntimeError("the view returned no result chunks; the previous copy is kept")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return rows, predicate


# --- control database -------------------------------------------------------------------


def load_config(conn: Connection, catalog: str) -> MirrorConfig | None:
    row = conn.execute(select(db.mirrors.c.config).where(db.mirrors.c.catalog == catalog)).first()
    return MirrorConfig.model_validate(row[0]) if row else None


def save_config(conn: Connection, catalog: str, config: MirrorConfig) -> None:
    stmt = pg_insert(db.mirrors).values(catalog=catalog, config=config.model_dump(mode="json"))
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["catalog"],
            set_={"config": stmt.excluded.config, "updated_at": text("now()")},
        )
    )


def load_state(conn: Connection, catalog: str) -> dict[tuple[str, str], dict[str, Any]]:
    rows = conn.execute(
        select(db.mirror_tables).where(db.mirror_tables.c.catalog == catalog)
    ).mappings()
    return {(r["schema_name"], r["table_name"]): dict(r) for r in rows}


def save_state(conn: Connection, catalog: str, schema: str, name: str, **values: Any) -> None:
    stmt = pg_insert(db.mirror_tables).values(
        catalog=catalog, schema_name=schema, table_name=name, **values
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["catalog", "schema_name", "table_name"],
            set_={k: stmt.excluded[k] for k in values},
        )
    )


def update_state(conn: Connection, catalog: str, schema: str, name: str, **values: Any) -> None:
    conn.execute(
        update(db.mirror_tables)
        .where(
            db.mirror_tables.c.catalog == catalog,
            db.mirror_tables.c.schema_name == schema,
            db.mirror_tables.c.table_name == name,
        )
        .values(**values)
    )


def enqueue(
    conn: Connection,
    catalog: str,
    trigger: str,
    tables: list[str] | None = None,
    full_refresh: bool = False,
) -> tuple[int, bool]:
    """Queue a sync. Returns (sync_id, created). Scheduled and polled syncs never queue behind
    an active one; a manual sync reuses an identical queued request."""
    request = {"tables": tables or [], "full_refresh": full_refresh}
    active = conn.execute(
        select(db.mirror_syncs.c.sync_id, db.mirror_syncs.c.state, db.mirror_syncs.c.request)
        .where(db.mirror_syncs.c.catalog == catalog, db.mirror_syncs.c.state.in_(ACTIVE_STATES))
        .order_by(db.mirror_syncs.c.sync_id)
    ).all()
    if trigger != "MANUAL" and active:
        return int(active[0][0]), False
    for sync_id, state, existing in active:
        if state == "QUEUED" and existing == request:
            return int(sync_id), False
    sync_id = conn.execute(
        db.mirror_syncs.insert()
        .values(catalog=catalog, trigger=trigger, state="QUEUED", request=request)
        .returning(db.mirror_syncs.c.sync_id)
    ).scalar_one()
    return int(sync_id), True


def claim(conn: Connection, worker_id: str) -> int | None:
    row = conn.execute(
        text(
            "UPDATE mirror_syncs SET state = 'RUNNING', worker_id = :w, started_at = now(), "
            "heartbeat_at = now() WHERE sync_id = (SELECT sync_id FROM mirror_syncs "
            "WHERE state = 'QUEUED' ORDER BY queued_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING sync_id"
        ),
        {"w": worker_id},
    ).first()
    return int(row[0]) if row else None


def reap(conn: Connection, now: datetime) -> list[int]:
    rows = conn.execute(
        update(db.mirror_syncs)
        .where(
            db.mirror_syncs.c.state == "RUNNING",
            db.mirror_syncs.c.heartbeat_at < now - STALE_AFTER,
        )
        .values(state="FAILED", ended_at=now, message="The worker stopped during the sync")
        .returning(db.mirror_syncs.c.sync_id)
    ).all()
    return [int(r[0]) for r in rows]


def sync_json(row: Any) -> dict[str, Any]:
    d = dict(row)
    for key in ("queued_at", "started_at", "ended_at", "heartbeat_at"):
        d[key] = d[key].isoformat() if d.get(key) else None
    return d


# --- running a sync (worker) ------------------------------------------------------------


def run_sync(sync_id: int, settings: Settings | None = None) -> dict[str, Any]:
    """Run one claimed sync to completion and record the outcome. Never raises: any error
    ends the sync as FAILED. A thread keeps the heartbeat fresh during long copies."""
    settings = settings or get_settings()
    engine = db.get_engine(settings.control_url)
    counts = {"copied": 0, "skipped": 0, "failed": 0, "rows": 0}
    done = threading.Event()

    def beat() -> None:
        while not done.wait(HEARTBEAT_SECONDS):
            try:
                with engine.begin() as conn:
                    conn.execute(
                        update(db.mirror_syncs)
                        .where(db.mirror_syncs.c.sync_id == sync_id)
                        .values(heartbeat_at=datetime.now(UTC), **counts)
                    )
            except Exception:
                log.exception("mirror sync %s heartbeat failed", sync_id)

    def finish(final: str, message: str) -> dict[str, Any]:
        with engine.begin() as conn:
            conn.execute(
                update(db.mirror_syncs)
                .where(db.mirror_syncs.c.sync_id == sync_id)
                .values(state=final, ended_at=datetime.now(UTC), message=message[:2000], **counts)
            )
        log.warning("mirror sync %s: %s (%s)", sync_id, final, message)
        return {"sync_id": sync_id, "state": final, "message": message, **counts}

    threading.Thread(target=beat, name=f"mirror-{sync_id}-heartbeat", daemon=True).start()
    try:
        final, message = _sync(sync_id, settings, counts)
        return finish(final, message)
    except Exception as exc:
        log.exception("mirror sync %s crashed", sync_id)
        return finish("FAILED", f"The sync stopped with an error: {exc}")
    finally:
        done.set()


def _sync(sync_id: int, settings: Settings, counts: dict[str, int]) -> tuple[str, str]:
    engine = db.get_engine(settings.control_url)
    with engine.connect() as conn:
        sync = (
            conn.execute(select(db.mirror_syncs).where(db.mirror_syncs.c.sync_id == sync_id))
            .mappings()
            .one()
        )
        catalog = sync["catalog"]
        config = load_config(conn, catalog)
        state = load_state(conn, catalog)
    info = get_catalog_info(catalog, settings)
    if info is None or not info.is_foreign or info.connection_name is None:
        return "FAILED", f"'{catalog}' is not a foreign catalog"
    if config is None:
        return "FAILED", f"No mirror config: PUT /api/duckbricks/mirrors/{catalog}"
    try:
        client = get_connection(info.connection_name, settings).client(settings)
        remote: list[RemoteTable] = []
        for schema in config.schemas:
            remote.extend(client.list_tables(info.remote_catalog or catalog, schema))
    except Exception as exc:
        return "FAILED", f"Cannot list remote tables: {exc}"
    request = sync["request"] or {}
    actions = plan(
        config,
        remote,
        state,
        only=request.get("tables") or None,
        full_refresh=bool(request.get("full_refresh")),
        trigger=sync["trigger"],
    )
    writable = replace(info.attachment(), read_only=False)
    con = open_session("dbk.large", catalogs=[writable], settings=settings)
    errors: list[str] = []
    changed = False
    try:
        enable_federation(con)
        for schema in sorted({a.schema for a in actions if a.kind == "COPY"}):
            con.execute(
                f"CREATE SCHEMA IF NOT EXISTS {sql_identifier(catalog)}.{sql_identifier(schema)}"
            )
        for action in actions:
            now = datetime.now(UTC)
            if action.kind == "SKIP":
                counts["skipped"] += 1
                with engine.begin() as conn:
                    update_state(conn, catalog, action.schema, action.name, checked_at=now)
                continue
            if action.kind == "DROP":
                target = local_name(catalog, action.schema, action.name)
                con.execute(f"DROP TABLE IF EXISTS {target}")
                changed = True
                with engine.begin() as conn:
                    update_state(
                        conn,
                        catalog,
                        action.schema,
                        action.name,
                        status=action.status,
                        checked_at=now,
                        error=None,
                    )
                log.warning("mirror %s: dropped %s (%s)", catalog, action.key, action.reason)
                continue
            assert action.table is not None
            t = action.table
            kind = "VIEW" if t.is_view else "TABLE"
            predicate = config.filters.get(t.key.lower())
            started = time.monotonic()
            try:
                copy = copy_view if t.is_view else copy_table
                rows, applied = copy(con, client, catalog, t, predicate)
            except Exception as exc:
                counts["failed"] += 1
                errors.append(f"{t.key}: {exc}")
                log.warning("mirror %s: %s failed: %s", catalog, t.key, exc)
                with engine.begin() as conn:
                    save_state(
                        conn,
                        catalog,
                        t.schema,
                        t.name,
                        table_type=kind,
                        status="FAILED",
                        error=str(exc)[:2000],
                        checked_at=now,
                    )
                continue
            changed = True
            counts["copied"] += 1
            counts["rows"] += rows
            elapsed = time.monotonic() - started
            with engine.begin() as conn:
                save_state(
                    conn,
                    catalog,
                    t.schema,
                    t.name,
                    table_type=kind,
                    status="SYNCED",
                    remote_version=t.version,
                    filter=predicate,
                    rows=rows,
                    duration_ms=int(elapsed * 1000),
                    error=None,
                    synced_at=datetime.now(UTC),
                    checked_at=now,
                )
            log.warning(
                "mirror %s: copied %s, %s rows in %.0fs (%s)%s",
                catalog,
                t.key,
                rows,
                elapsed,
                action.reason,
                f" where {applied}" if applied else "",
            )
        if changed:
            lake = sql_literal(catalog)
            cutoff = f"now() - INTERVAL '{config.keep_history_hours} hours'"
            con.execute(f"CALL ducklake_expire_snapshots({lake}, older_than => {cutoff})")
            con.execute(f"CALL ducklake_cleanup_old_files({lake}, cleanup_all => true)")
    finally:
        con.close()
    summary = (
        f"{counts['copied']} copied ({counts['rows']} rows), {counts['skipped']} unchanged, "
        f"{sum(a.kind == 'DROP' for a in actions)} dropped, {counts['failed']} failed"
    )
    if errors:
        summary += ". " + "; ".join(errors[:5])
    final = "SUCCESS" if not counts["failed"] else ("PARTIAL" if counts["copied"] else "FAILED")
    return final, summary


# --- scheduling (API scheduler loop) ----------------------------------------------------


def schedule_syncs(settings: Settings | None = None, now: datetime | None = None) -> list[int]:
    """Scheduler step: reap stale syncs, queue nightly and polled syncs that are due."""
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    engine = db.get_engine(settings.control_url)
    queued: list[int] = []
    with engine.begin() as conn:
        reaped = reap(conn, now)
        if reaped:
            log.warning("reaped mirror syncs with stale heartbeats: %s", reaped)
        rows = conn.execute(
            select(db.mirrors.c.catalog, db.mirrors.c.config, db.mirrors.c.updated_at)
            .select_from(db.mirrors.join(db.catalogs, db.catalogs.c.name == db.mirrors.c.catalog))
            .where(db.catalogs.c.options["duckbricks_mode"].astext == "mirror")
        ).all()
        for catalog, raw, updated_at in rows:
            config = MirrorConfig.model_validate(raw)
            last = {
                trig: when
                for trig, when in conn.execute(
                    text(
                        "SELECT trigger, max(queued_at) FROM mirror_syncs WHERE catalog = :c "
                        "GROUP BY trigger"
                    ),
                    {"c": catalog},
                ).all()
            }
            if config.schedule and config.schedule.pause_status == "UNPAUSED":
                baseline = max(t for t in (updated_at, last.get("SCHEDULE")) if t is not None)
                tz = ZoneInfo(config.schedule.timezone_id)
                due = next_fire(config.schedule.quartz_cron_expression, baseline.astimezone(tz))
                if due is not None and due <= now:
                    sync_id, created = enqueue(conn, catalog, "SCHEDULE")
                    if created:
                        queued.append(sync_id)
                        log.warning("mirror schedule queued sync %s for %s", sync_id, catalog)
                    continue
            if config.poll_minutes:
                interval = timedelta(minutes=min(config.poll_minutes.values()))
                previous = [t for t in last.values() if t is not None]
                if not previous or now - max(previous) >= interval:
                    sync_id, created = enqueue(
                        conn, catalog, "POLL", tables=sorted(config.poll_minutes)
                    )
                    if created:
                        queued.append(sync_id)
    return queued


def require_mirror_catalog(name: str, settings: Settings | None = None) -> Any:
    info = get_catalog_info(name, settings)
    if info is None:
        raise NotFound(f"Catalog '{name}' does not exist.", error_code="CATALOG_DOES_NOT_EXIST")
    if not info.is_foreign:
        raise InvalidParameter(f"Catalog '{name}' is not a foreign catalog")
    return info
