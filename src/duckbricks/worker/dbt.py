"""`dbt_task`: run dbt v2 against the platform's DuckLake catalogs (SPEC section 9).

For each run the executor copies the project to a temp dir, writes `profiles.yml` (target
`duckbricks`) and `catalogs.yml` (one DuckLake entry per registered catalog), and runs each
command. The task's `catalog` and `schema` reach the project as `DBK_CATALOG`/`DBK_SCHEMA`
env vars, so models can use `+catalog_name: "{{ env_var('DBK_CATALOG', 'dev') }}"`.

After a successful run with the full dbt distribution, a strict static-analysis compile writes
the info schema and its column lineage is loaded into the lineage tables (Phase 4).
"""

from __future__ import annotations

import os
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from duckbricks.core import db
from duckbricks.core.catalogs import list_catalogs
from duckbricks.core.jobs import node_type_for_task
from duckbricks.core.node_types import get_node_types
from duckbricks.core.workspace import Workspace
from duckbricks.worker.executors import Execution, Outcome, TaskFailed, local_path, run_subprocess

TARGET = "duckbricks"
ARTIFACTS = ("run_results.json", "manifest.json", "sources.json", "semantic_manifest.json")
COPY_IGNORE = shutil.ignore_patterns("target", "logs", ".venv", "__pycache__", ".git")


def profile_name(project: Path) -> str:
    config = yaml.safe_load((project / "dbt_project.yml").read_text()) or {}
    return str(config.get("profile") or config.get("name") or "default")


def write_profiles(directory: Path, profile: str, schema: str, threads: int) -> None:
    profiles = {
        profile: {
            "target": TARGET,
            "outputs": {
                TARGET: {
                    "type": "duckdb",
                    "path": ":memory:",
                    "schema": schema,
                    "threads": threads,
                    "extensions": ["httpfs", "postgres", "ducklake"],
                    "secrets": [
                        {
                            "type": "s3",
                            "name": "dbk_s3",
                            "key_id": "{{ env_var('DBK_S3_ACCESS_KEY') }}",
                            "secret": "{{ env_var('DBK_S3_SECRET_KEY') }}",
                            "endpoint": "{{ env_var('DBK_S3_ENDPOINT') }}",
                            "region": "{{ env_var('DBK_S3_REGION', 'us-east-1') }}",
                            "url_style": "path",
                            "use_ssl": False,
                        }
                    ],
                }
            },
        }
    }
    (directory / "profiles.yml").write_text(yaml.safe_dump(profiles, sort_keys=False))


def write_catalogs(project: Path, lake_db: str) -> None:
    entries = [
        {
            "name": c.name,
            "type": "ducklake",
            "table_format": "default",
            "config": {
                "duckdb": {
                    "metadata_path": f"postgres:dbname={lake_db}",
                    "metadata_schema": c.metadata_schema,
                    "data_path": c.data_path,
                }
            },
        }
        for c in list_catalogs()
    ]
    (project / "catalogs.yml").write_text(yaml.safe_dump({"catalogs": entries}, sort_keys=False))


def dbt_env(ex: Execution, catalog: str, schema: str, profiles_dir: Path) -> dict[str, str]:
    s = ex.settings
    env = dict(os.environ)
    env.update(
        PGHOST=s.pg_host,
        PGPORT=str(s.pg_port),
        PGUSER=s.pg_user,
        PGPASSWORD=s.pg_password.get_secret_value(),
        DBK_S3_ACCESS_KEY=s.s3_access_key.get_secret_value(),
        DBK_S3_SECRET_KEY=s.s3_secret_key.get_secret_value(),
        DBK_S3_ENDPOINT=s.s3_endpoint,
        DBK_S3_REGION=s.s3_region,
        DBK_CATALOG=catalog,
        DBK_SCHEMA=schema,
        DBT_PROFILES_DIR=str(profiles_dir),
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
    )
    return env


def command_argv(command: str, add_target: bool) -> list[str]:
    argv = shlex.split(command)
    if not argv or argv[0] != "dbt":
        raise TaskFailed(f"dbt command must start with 'dbt': {command}")
    if add_target and "--target" not in argv and "-t" not in argv:
        argv += ["--target", TARGET]
    return argv


def prepare_project(ex: Execution, spec: dict[str, Any], work: Path) -> tuple[Path, Path, bool]:
    """Copy the project, write configs. Returns (project dir, profiles dir, generated profile)."""
    ws = Workspace(ex.settings)
    source = local_path(ws, spec.get("project_directory") or "/")
    if not (source / "dbt_project.yml").exists():
        raise TaskFailed(f"No dbt_project.yml in {spec.get('project_directory')}")
    project = work / "project"
    shutil.copytree(source, project, ignore=COPY_IGNORE)
    write_catalogs(project, ex.settings.lake_db)
    if spec.get("profiles_directory"):
        profiles_dir = (project / spec["profiles_directory"]).resolve()
        return project, profiles_dir, False
    node_type, _ = get_node_types().resolve(None)
    threads = (
        get_node_types()
        .node_types.get(node_type_for_task(ex.run["settings"], ex.row["task"]), node_type)
        .threads
    )
    write_profiles(work, profile_name(project), spec.get("schema") or "default", threads)
    return project, work, True


def run_dbt_task(ex: Execution, task: dict[str, Any]) -> Outcome:
    spec = task["dbt_task"]
    if spec.get("warehouse_id"):
        ex.log("dbt_task.warehouse_id is ignored: dbt runs on job compute")
    catalog = spec.get("catalog") or "workspace"
    schema = spec.get("schema") or "default"
    timeout = int(task.get("timeout_seconds") or 0)
    artifacts_dir = ex.log_path.parent / f"{ex.log_path.stem}_dbt"
    with tempfile.TemporaryDirectory(prefix="dbk-dbt-") as tmp:
        work = Path(tmp)
        project, profiles_dir, generated = prepare_project(ex, spec, work)
        env = dbt_env(ex, catalog, schema, profiles_dir)
        ex.log(f"dbt project {spec.get('project_directory')} -> catalog {catalog}, schema {schema}")
        try:
            for command in spec["commands"]:
                argv = command_argv(command, generated)
                ex.log(f"\n$ {' '.join(argv)}")
                code = run_subprocess(ex, argv, env, project, timeout)
                if code != 0:
                    raise TaskFailed(f"'{command}' failed with exit code {code}")
            if os.environ.get("DBK_DBT_DISTRIBUTION", "full") == "full":
                capture_lineage(ex, project, env, generated, timeout)
            else:
                ex.log("dbt-oss build: column lineage capture skipped (ADR 0006)")
        finally:
            save_artifacts(project, artifacts_dir)
    return Outcome("SUCCESS", output={"dbt_output": {"artifacts_link": str(artifacts_dir)}})


def save_artifacts(project: Path, destination: Path) -> None:
    target = project / "target"
    if not target.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        if (target / name).exists():
            shutil.copy2(target / name, destination / name)
    info_schema = target / "info_schema"
    if info_schema.is_dir():
        shutil.copytree(info_schema, destination / "info_schema", dirs_exist_ok=True)


def capture_lineage(
    ex: Execution, project: Path, env: dict[str, str], generated: bool, timeout: int
) -> None:
    argv = command_argv("dbt compile --static-analysis strict --generate-info-schema", generated)
    ex.log(f"\n$ {' '.join(argv)}  # lineage capture")
    try:
        code = run_subprocess(ex, argv, env, project, timeout)
    except TaskFailed as exc:
        ex.log(f"lineage capture stopped: {exc}")
        return
    info_schema = project / "target" / "info_schema" / "v1"
    if code != 0 or not info_schema.is_dir():
        ex.log("lineage capture: static-analysis compile failed; lineage not updated")
        return
    from duckbricks.core.lineage import load_dbt_lineage

    engine = db.get_engine(ex.settings.control_url)
    counts = load_dbt_lineage(
        engine, info_schema, str(ex.run["job_id"] or ""), str(ex.run["run_id"])
    )
    ex.log(f"lineage captured: {counts['tables']} table edges, {counts['columns']} column edges")
