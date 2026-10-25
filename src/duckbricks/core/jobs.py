"""Jobs: validate Databricks job settings and store them.

Lenient by design: a valid Databricks field we do not support produces a warning. A task type we
cannot run is an error, so a job that cannot work never gets created.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, delete, func, insert, select, update

from duckbricks.core import db
from duckbricks.core.errors import InvalidParameter, NotFound
from duckbricks.core.node_types import get_node_types
from duckbricks.scheduler.quartz import QuartzError, quartz_to_cron

SUPPORTED_TASK_TYPES = (
    "sql_task",
    "spark_python_task",
    "notebook_task",
    "dbt_task",
    "run_job_task",
    "condition_task",
    "pipeline_task",
)
UNSUPPORTED_TASK_TYPES = (
    "python_wheel_task",
    "spark_jar_task",
    "spark_submit_task",
    "for_each_task",
    "dashboard_task",
    "power_bi_task",
    "clean_rooms_notebook_task",
    "gen_ai_compute_task",
    "dbt_platform_task",
    "alert_task",
)
RUN_IF = (
    "ALL_SUCCESS",
    "ALL_DONE",
    "NONE_FAILED",
    "AT_LEAST_ONE_SUCCESS",
    "ALL_FAILED",
    "AT_LEAST_ONE_FAILED",
)
CONDITION_OPS = (
    "EQUAL_TO",
    "NOT_EQUAL",
    "GREATER_THAN",
    "GREATER_THAN_OR_EQUAL",
    "LESS_THAN",
    "LESS_THAN_OR_EQUAL",
)
JOB_FIELDS_SUPPORTED = {
    "name",
    "description",
    "tasks",
    "job_clusters",
    "schedule",
    "trigger",
    "parameters",
    "max_concurrent_runs",
    "tags",
    "timeout_seconds",
    "environments",
    "format",
    "edit_mode",
    "queue",
    "deployment",
}
JOB_FIELDS_IGNORED = {
    "email_notifications",
    "webhook_notifications",
    "notification_settings",
    "health",
    "continuous",
    "git_source",
    "run_as",
    "budget_policy_id",
    "performance_target",
    "usage_policy_id",
    "access_control_list",
    "permissions",
}
TASK_FIELDS_SUPPORTED = {
    "task_key",
    "description",
    "depends_on",
    "run_if",
    "job_cluster_key",
    "new_cluster",
    "existing_cluster_id",
    "environment_key",
    "timeout_seconds",
    "max_retries",
    "min_retry_interval_millis",
    "retry_on_timeout",
    "disabled",
    *SUPPORTED_TASK_TYPES,
}
CLUSTER_FIELDS_IGNORED = (
    "num_workers",
    "autoscale",
    "spark_version",
    "spark_conf",
    "spark_env_vars",
    "aws_attributes",
    "azure_attributes",
    "gcp_attributes",
    "driver_node_type_id",
    "data_security_mode",
    "runtime_engine",
    "policy_id",
    "custom_tags",
    "init_scripts",
    "cluster_log_conf",
    "enable_elastic_disk",
    "single_user_name",
    "instance_pool_id",
    "driver_instance_pool_id",
)


def task_type(task: dict[str, Any]) -> str:
    for key in SUPPORTED_TASK_TYPES:
        if key in task:
            return key
    raise KeyError("no supported task type")


def _check_task_type(task: dict[str, Any], key: str) -> str:
    found = [t for t in SUPPORTED_TASK_TYPES if t in task]
    unsupported = [t for t in UNSUPPORTED_TASK_TYPES if t in task]
    if unsupported:
        raise InvalidParameter(
            f"Task '{key}' uses {unsupported[0]}, which Duckbricks cannot run. "
            f"Supported task types: {', '.join(SUPPORTED_TASK_TYPES)}."
        )
    if len(found) != 1:
        raise InvalidParameter(
            f"Task '{key}' must set exactly one task type "
            f"({', '.join(SUPPORTED_TASK_TYPES)}); found {found or 'none'}."
        )
    kind = found[0]
    spec = task[kind]
    if not isinstance(spec, dict):
        raise InvalidParameter(f"Task '{key}': {kind} must be an object")
    if kind == "sql_task":
        for other in ("query", "dashboard", "alert"):
            if other in spec:
                raise InvalidParameter(
                    f"Task '{key}': sql_task.{other} cannot run on Duckbricks; use sql_task.file"
                )
        if not isinstance(spec.get("file"), dict) or not spec["file"].get("path"):
            raise InvalidParameter(f"Task '{key}': sql_task.file.path is required")
    elif kind == "spark_python_task" and not spec.get("python_file"):
        raise InvalidParameter(f"Task '{key}': spark_python_task.python_file is required")
    elif kind == "notebook_task" and not spec.get("notebook_path"):
        raise InvalidParameter(f"Task '{key}': notebook_task.notebook_path is required")
    elif kind == "dbt_task":
        commands = spec.get("commands")
        if not commands or not all(str(c).strip().startswith("dbt") for c in commands):
            raise InvalidParameter(
                f"Task '{key}': dbt_task.commands must be a non-empty list of commands "
                "that each start with 'dbt'"
            )
    elif kind == "run_job_task" and "job_id" not in spec:
        raise InvalidParameter(f"Task '{key}': run_job_task.job_id is required")
    elif kind == "condition_task":
        if spec.get("op") not in CONDITION_OPS:
            raise InvalidParameter(
                f"Task '{key}': condition_task.op must be one of {', '.join(CONDITION_OPS)}"
            )
        if "left" not in spec or "right" not in spec:
            raise InvalidParameter(f"Task '{key}': condition_task needs left and right")
    elif kind == "pipeline_task" and not spec.get("pipeline_id"):
        raise InvalidParameter(f"Task '{key}': pipeline_task.pipeline_id is required")
    return kind


def _cluster_warnings(cluster: dict[str, Any], where: str) -> list[str]:
    warnings = []
    ignored = [f for f in CLUSTER_FIELDS_IGNORED if f in cluster]
    if ignored:
        warnings.append(f"{where}: {', '.join(ignored)} accepted and ignored (single DuckDB node)")
    _, warning = get_node_types().resolve(cluster.get("node_type_id"))
    if warning and cluster.get("node_type_id"):
        warnings.append(f"{where}: {warning}")
    return warnings


def _check_acyclic(tasks: dict[str, dict[str, Any]]) -> None:
    state: dict[str, int] = {}

    def visit(key: str, path: list[str]) -> None:
        if state.get(key) == 2:
            return
        if state.get(key) == 1:
            cycle = " -> ".join([*path[path.index(key) :], key])
            raise InvalidParameter(f"Task dependencies form a cycle: {cycle}")
        state[key] = 1
        for dep in tasks[key].get("depends_on", []):
            visit(dep["task_key"], [*path, key])
        state[key] = 2

    for key in tasks:
        visit(key, [])


def validate_settings(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate and normalise job settings. Returns (settings, warnings)."""
    settings = dict(raw)
    warnings: list[str] = []
    settings.setdefault("name", "Untitled")
    settings.setdefault("max_concurrent_runs", 1)
    settings.setdefault("format", "MULTI_TASK")

    for field in sorted(settings):
        if field in JOB_FIELDS_IGNORED:
            warnings.append(f"{field} is accepted and ignored")
        elif field not in JOB_FIELDS_SUPPORTED:
            warnings.append(f"{field} is not supported by Duckbricks and was ignored")

    tasks = settings.get("tasks") or []
    if not isinstance(tasks, list) or not tasks:
        raise InvalidParameter("A job needs at least one task")
    clusters = {c.get("job_cluster_key"): c for c in settings.get("job_clusters") or []}
    for key, cluster in clusters.items():
        if not key:
            raise InvalidParameter("Every job_clusters entry needs a job_cluster_key")
        warnings += _cluster_warnings(cluster.get("new_cluster") or {}, f"job cluster '{key}'")

    by_key: dict[str, dict[str, Any]] = {}
    for task in tasks:
        key = task.get("task_key")
        if not key:
            raise InvalidParameter("Every task needs a task_key")
        if key in by_key:
            raise InvalidParameter(f"Duplicate task_key '{key}'")
        _check_task_type(task, key)
        for field in sorted(set(task) - TASK_FIELDS_SUPPORTED):
            if field in ("libraries", "email_notifications", "webhook_notifications", "health"):
                warnings.append(f"Task '{key}': {field} accepted and ignored")
            else:
                warnings.append(f"Task '{key}': {field} is not supported and was ignored")
        run_if = task.get("run_if", "ALL_SUCCESS")
        if run_if not in RUN_IF:
            raise InvalidParameter(f"Task '{key}': run_if must be one of {', '.join(RUN_IF)}")
        if task.get("job_cluster_key") and task["job_cluster_key"] not in clusters:
            raise InvalidParameter(
                f"Task '{key}': job_cluster_key '{task['job_cluster_key']}' is not defined "
                "in job_clusters"
            )
        if task.get("new_cluster"):
            warnings += _cluster_warnings(task["new_cluster"], f"Task '{key}'")
        if task.get("existing_cluster_id"):
            warnings.append(
                f"Task '{key}': existing_cluster_id runs on job compute "
                f"({get_node_types().default})"
            )
        by_key[key] = task

    for key, task in by_key.items():
        for dep in task.get("depends_on", []) or []:
            if dep.get("task_key") not in by_key:
                raise InvalidParameter(
                    f"Task '{key}' depends on '{dep.get('task_key')}', which is not a task"
                )
    _check_acyclic(by_key)

    schedule = settings.get("schedule")
    if schedule:
        expression = schedule.get("quartz_cron_expression")
        if not expression:
            raise InvalidParameter("schedule.quartz_cron_expression is required")
        try:
            quartz_to_cron(expression)
        except QuartzError as exc:
            raise InvalidParameter(f"schedule: {exc}") from exc
        schedule.setdefault("timezone_id", "UTC")
        schedule.setdefault("pause_status", "UNPAUSED")
    trigger = settings.get("trigger")
    if trigger:
        kinds = [k for k in ("periodic", "file_arrival", "table_update", "table") if k in trigger]
        for kind in kinds:
            if kind not in ("periodic", "file_arrival"):
                warnings.append(f"trigger.{kind} is not supported and was ignored")
        periodic = trigger.get("periodic")
        if periodic and periodic.get("unit") not in ("HOURS", "DAYS", "WEEKS"):
            raise InvalidParameter("trigger.periodic.unit must be HOURS, DAYS or WEEKS")
        file_arrival = trigger.get("file_arrival")
        if file_arrival and not str(file_arrival.get("url", "")).startswith("s3://"):
            raise InvalidParameter(
                "trigger.file_arrival.url must be an s3:// location (for example s3://landing/...)"
            )
        trigger.setdefault("pause_status", "UNPAUSED")

    names = [p.get("name") for p in settings.get("parameters") or []]
    if len(names) != len(set(names)) or not all(names):
        raise InvalidParameter("Job parameters need unique, non-empty names")
    return settings, warnings


def node_type_for_task(settings: dict[str, Any], task: dict[str, Any]) -> str:
    catalog = get_node_types()
    cluster: dict[str, Any] | None = task.get("new_cluster")
    if cluster is None and task.get("job_cluster_key"):
        for jc in settings.get("job_clusters") or []:
            if jc.get("job_cluster_key") == task["job_cluster_key"]:
                cluster = jc.get("new_cluster")
    node_type, _ = catalog.resolve((cluster or {}).get("node_type_id"))
    return node_type.name


# --- storage ---------------------------------------------------------------------------


def create_job(conn: Connection, raw: dict[str, Any], user: str) -> tuple[int, list[str]]:
    settings, warnings = validate_settings(raw)
    job_id = conn.execute(
        insert(db.jobs)
        .values(name=settings["name"], settings=settings, creator_user_name=user)
        .returning(db.jobs.c.job_id)
    ).scalar_one()
    return int(job_id), warnings


def reset_job(conn: Connection, job_id: int, raw: dict[str, Any]) -> list[str]:
    settings, warnings = validate_settings(raw)
    result = conn.execute(
        update(db.jobs)
        .where(db.jobs.c.job_id == job_id)
        .values(name=settings["name"], settings=settings, updated_at=func.now())
    )
    if result.rowcount == 0:
        raise NotFound(f"Job {job_id} does not exist.")
    return warnings


def update_job(
    conn: Connection, job_id: int, new_settings: dict[str, Any], fields_to_remove: list[str]
) -> list[str]:
    current = get_job_row(conn, job_id)["settings"]
    merged = dict(current)
    for field in fields_to_remove:
        merged.pop(field, None)
    for key, value in new_settings.items():
        if key in ("tasks", "job_clusters") and key in merged:
            id_key = "task_key" if key == "tasks" else "job_cluster_key"
            existing = {item[id_key]: item for item in merged[key]}
            for item in value:
                existing[item[id_key]] = item
            merged[key] = list(existing.values())
        else:
            merged[key] = value
    return reset_job(conn, job_id, merged)


def delete_job(conn: Connection, job_id: int) -> None:
    result = conn.execute(delete(db.jobs).where(db.jobs.c.job_id == job_id))
    if result.rowcount == 0:
        raise NotFound(f"Job {job_id} does not exist.")


def get_job_row(conn: Connection, job_id: int) -> dict[str, Any]:
    row = conn.execute(select(db.jobs).where(db.jobs.c.job_id == job_id)).mappings().first()
    if row is None:
        raise NotFound(f"Job {job_id} does not exist.")
    return dict(row)


def job_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "creator_user_name": row["creator_user_name"],
        "run_as_user_name": row["creator_user_name"],
        "created_time": int(row["created_at"].timestamp() * 1000),
        "settings": row["settings"],
    }


def list_jobs(
    conn: Connection, limit: int = 25, offset: int = 0, name: str | None = None
) -> tuple[list[dict[str, Any]], bool]:
    query = select(db.jobs).order_by(db.jobs.c.job_id.desc())
    if name:
        query = query.where(func.lower(db.jobs.c.name) == name.lower())
    rows = conn.execute(query.limit(limit + 1).offset(offset)).mappings().all()
    return [job_json(dict(r)) for r in rows[:limit]], len(rows) > limit
