"""Apply a resolved bundle: sync files, upsert schemas, pipelines and jobs, record state.

Resources are keyed by (bundle, target, type, key). A redeploy updates in place and never
duplicates; resources removed from the config are deleted. Control-database changes happen in
one transaction; files are staged and swapped in only after it commits.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import re
import shutil
import tarfile
import uuid
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import Connection, delete, select, text

from duckbricks.core import db, jobs, pipelines
from duckbricks.core.config import Settings, get_settings
from duckbricks.core.errors import InvalidParameter, NotFound
from duckbricks.core.unity_catalog import UnityCatalog
from duckbricks.core.workspace import Workspace, normalise

RESOURCE_REF = re.compile(
    r"\$\{resources\.(jobs|pipelines|schemas)\.([A-Za-z0-9_\-]+)\.(id|name)\}"
)
ORDER = ("schemas", "pipelines", "jobs")


@dataclass
class DeployReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: int = 0

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


def config_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _existing(conn: Connection, bundle: str, target: str) -> dict[tuple[str, str], dict[str, Any]]:
    br = db.bundle_resources
    rows = conn.execute(
        select(br).where(br.c.bundle_name == bundle, br.c.target == target)
    ).mappings()
    return {(r["resource_type"], r["resource_key"]): dict(r) for r in rows}


def _resolve_refs(
    value: Any, ids: dict[tuple[str, str], str], names: dict[tuple[str, str], str]
) -> Any:
    if isinstance(value, str):
        whole = RESOURCE_REF.fullmatch(value)

        def lookup(match: re.Match[str]) -> str:
            key = (match.group(1), match.group(2))
            table = ids if match.group(3) == "id" else names
            if key not in table:
                raise InvalidParameter(
                    f"{match.group(0)} refers to a resource that is not deployed"
                )
            return table[key]

        if whole:
            resolved = lookup(whole)
            return (
                int(resolved) if whole.group(1) == "jobs" and whole.group(3) == "id" else resolved
            )
        return RESOURCE_REF.sub(lookup, value)
    if isinstance(value, list):
        return [_resolve_refs(v, ids, names) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_refs(v, ids, names) for k, v in value.items()}
    return value


def _record(
    conn: Connection, bundle: str, target: str, rtype: str, key: str, rid: str, digest: str
) -> None:
    conn.execute(
        text(
            "INSERT INTO bundle_resources "
            "(bundle_name, target, resource_type, resource_key, resource_id, config_hash) "
            "VALUES (:b, :t, :rt, :rk, :id, :h) "
            "ON CONFLICT (bundle_name, target, resource_type, resource_key) DO UPDATE SET "
            "resource_id = EXCLUDED.resource_id, config_hash = EXCLUDED.config_hash, "
            "deployed_at = now()"
        ),
        {"b": bundle, "t": target, "rt": rtype, "rk": key, "id": rid, "h": digest},
    )


def _extract(files_b64: str, destination: Any) -> int:
    data = base64.b64decode(files_b64) if files_b64 else b""
    destination.mkdir(parents=True, exist_ok=True)
    if not data:
        return 0
    count = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise InvalidParameter(f"Refusing unsafe path in bundle archive: {member.name}")
        tar.extractall(destination, filter="data")
        count = sum(1 for m in tar.getmembers() if m.isfile())
    return count


def deploy(
    config: dict[str, Any],
    files_b64: str,
    uc: UnityCatalog,
    settings: Settings | None = None,
) -> DeployReport:
    settings = settings or get_settings()
    bundle = config["bundle"]["name"]
    target = config["bundle"]["target"]
    root_path = normalise(config["workspace"]["root_path"])
    file_path = normalise(config["workspace"].get("file_path") or f"{root_path}/files")
    resources = config.get("resources") or {}
    report = DeployReport()
    ws = Workspace(settings)

    # Stage files first so a bad archive fails before anything changes.
    staging = ws.root / ".trash" / f"staging-{uuid.uuid4().hex}"
    report.files = _extract(files_b64, staging)

    # Schemas live in DuckLake, outside the control-DB transaction; creating them is idempotent.
    for key, schema in (resources.get("schemas") or {}).items():
        created = uc.ensure_schema(schema["catalog_name"], schema["name"], schema.get("comment"))
        label = f"schemas.{key}"
        (report.created if created else report.unchanged).append(label)

    engine = db.get_engine(settings.control_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
                {"k": f"bundle:{bundle}:{target}"},
            )
            existing = _existing(conn, bundle, target)
            ids: dict[tuple[str, str], str] = {
                (rt, rk): row["resource_id"] for (rt, rk), row in existing.items()
            }
            names: dict[tuple[str, str], str] = {}
            for key, schema in (resources.get("schemas") or {}).items():
                full = f"{schema['catalog_name']}.{schema['name']}"
                ids[("schemas", key)] = full
                names[("schemas", key)] = schema["name"]
                _record(conn, bundle, target, "schemas", key, full, config_hash(schema))
            for key, job in (resources.get("jobs") or {}).items():
                names[("jobs", key)] = str(job.get("name") or key)
            for key, pipe in (resources.get("pipelines") or {}).items():
                names[("pipelines", key)] = str(pipe.get("name") or key)

            # Pipelines: create missing ones, then update.
            for key, spec in (resources.get("pipelines") or {}).items():
                label = f"pipelines.{key}"
                resolved = _resolve_refs(spec, ids, names)
                digest = config_hash(resolved)
                current = existing.get(("pipelines", key))
                if current and pipelines.exists(conn, current["resource_id"]):
                    if current["config_hash"] == digest:
                        report.unchanged.append(label)
                    else:
                        pipelines.edit_pipeline(conn, current["resource_id"], resolved)
                        report.updated.append(label)
                    pid = current["resource_id"]
                else:
                    pid = pipelines.create_pipeline(conn, resolved, settings.user_name)
                    report.created.append(label)
                ids[("pipelines", key)] = pid
                _record(conn, bundle, target, "pipelines", key, pid, digest)

            # Jobs: create missing ones with stand-in references, then reset all with real ids.
            job_specs = resources.get("jobs") or {}
            new_jobs = []
            for key, spec in job_specs.items():
                current = existing.get(("jobs", key))
                if current and _job_exists(conn, int(current["resource_id"])):
                    continue
                placeholder = _resolve_refs(spec, {**ids, **_zero_ids(job_specs, ids)}, names)
                job_id, _ = jobs.create_job(
                    conn, _deployment_meta(placeholder, bundle, target), settings.user_name
                )
                ids[("jobs", key)] = str(job_id)
                new_jobs.append(key)
            for key, spec in job_specs.items():
                label = f"jobs.{key}"
                resolved = _deployment_meta(_resolve_refs(spec, ids, names), bundle, target)
                digest = config_hash(resolved)
                current = existing.get(("jobs", key))
                job_id = int(ids[("jobs", key)])
                if key in new_jobs:
                    report.warnings += [
                        f"{label}: {w}" for w in jobs.reset_job(conn, job_id, resolved)
                    ]
                    report.created.append(label)
                elif current and current["config_hash"] == digest:
                    report.unchanged.append(label)
                else:
                    report.warnings += [
                        f"{label}: {w}" for w in jobs.reset_job(conn, job_id, resolved)
                    ]
                    report.updated.append(label)
                _record(conn, bundle, target, "jobs", key, str(job_id), digest)

            # Delete what the config no longer has.
            wanted = {(rt, rk) for rt in ORDER for rk in (resources.get(rt) or {})}
            for (rtype, rkey), row in existing.items():
                if (rtype, rkey) in wanted:
                    continue
                _delete_resource(conn, rtype, row["resource_id"], uc, report)
                conn.execute(
                    delete(db.bundle_resources).where(
                        db.bundle_resources.c.bundle_name == bundle,
                        db.bundle_resources.c.target == target,
                        db.bundle_resources.c.resource_type == rtype,
                        db.bundle_resources.c.resource_key == rkey,
                    )
                )
                report.deleted.append(f"{rtype}.{rkey}")

            conn.execute(
                text(
                    "INSERT INTO bundle_deployments (bundle_name, target, root_path, mode, config) "
                    "VALUES (:b, :t, :r, :m, CAST(:c AS jsonb)) "
                    "ON CONFLICT (bundle_name, target) DO UPDATE SET "
                    "root_path = EXCLUDED.root_path, "
                    "mode = EXCLUDED.mode, config = EXCLUDED.config, deployed_at = now()"
                ),
                {
                    "b": bundle,
                    "t": target,
                    "r": root_path,
                    "m": config["bundle"].get("mode"),
                    "c": json.dumps(config),
                },
            )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    # Swap the synced files in.
    destination = ws.local(file_path)
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(staging, destination)
    return report


def _zero_ids(
    job_specs: dict[str, Any], ids: dict[tuple[str, str], str]
) -> dict[tuple[str, str], str]:
    return {("jobs", k): "0" for k in job_specs if ("jobs", k) not in ids}


def _deployment_meta(settings: dict[str, Any], bundle: str, target: str) -> dict[str, Any]:
    out = dict(settings)
    out.pop("permissions", None)
    out["deployment"] = {"kind": "BUNDLE", "metadata_file_path": f"{bundle}/{target}"}
    out["edit_mode"] = "UI_LOCKED"
    return out


def _job_exists(conn: Connection, job_id: int) -> bool:
    return (
        conn.execute(select(db.jobs.c.job_id).where(db.jobs.c.job_id == job_id)).first() is not None
    )


def _delete_resource(
    conn: Connection, rtype: str, resource_id: str, uc: UnityCatalog, report: DeployReport
) -> None:
    if rtype == "jobs":
        with contextlib.suppress(NotFound):
            jobs.delete_job(conn, int(resource_id))
    elif rtype == "pipelines":
        pipelines.delete_pipeline(conn, resource_id)
    elif rtype == "schemas":
        with contextlib.suppress(NotFound):
            uc.delete_schema(resource_id, force=True)


def destroy(
    bundle: str, target: str, uc: UnityCatalog, settings: Settings | None = None
) -> DeployReport:
    settings = settings or get_settings()
    report = DeployReport()
    engine = db.get_engine(settings.control_url)
    with engine.begin() as conn:
        row = (
            conn.execute(
                select(db.bundle_deployments).where(
                    db.bundle_deployments.c.bundle_name == bundle,
                    db.bundle_deployments.c.target == target,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise NotFound(f"Bundle {bundle} has no deployment for target {target}")
        existing = _existing(conn, bundle, target)
        for rtype in reversed(ORDER):
            for (rt, rk), res in existing.items():
                if rt == rtype:
                    _delete_resource(conn, rt, res["resource_id"], uc, report)
                    report.deleted.append(f"{rt}.{rk}")
        conn.execute(
            delete(db.bundle_resources).where(
                db.bundle_resources.c.bundle_name == bundle, db.bundle_resources.c.target == target
            )
        )
        conn.execute(
            delete(db.bundle_deployments).where(
                db.bundle_deployments.c.bundle_name == bundle,
                db.bundle_deployments.c.target == target,
            )
        )
        root_path = row["root_path"]
    Workspace(settings).delete_tree(root_path)
    return report


def summary(
    conn: Connection, bundle: str | None = None, target: str | None = None
) -> list[dict[str, Any]]:
    bd, br = db.bundle_deployments, db.bundle_resources
    query = select(bd).order_by(bd.c.bundle_name, bd.c.target)
    if bundle:
        query = query.where(bd.c.bundle_name == bundle)
    if target:
        query = query.where(bd.c.target == target)
    out = []
    base = get_settings().public_url.rstrip("/")
    for dep in conn.execute(query).mappings():
        resources = conn.execute(
            select(br)
            .where(br.c.bundle_name == dep["bundle_name"], br.c.target == dep["target"])
            .order_by(br.c.resource_type, br.c.resource_key)
        ).mappings()
        items = []
        for r in resources:
            url = {
                "jobs": f"{base}/jobs/{r['resource_id']}",
                "pipelines": f"{base}/pipelines/{r['resource_id']}",
                "schemas": f"{base}/explore/data/{r['resource_id'].replace('.', '/')}",
            }.get(r["resource_type"])
            items.append(
                {
                    "resource_type": r["resource_type"],
                    "resource_key": r["resource_key"],
                    "resource_id": r["resource_id"],
                    "url": url,
                    "deployed_at": int(r["deployed_at"].timestamp() * 1000),
                }
            )
        out.append(
            {
                "bundle_name": dep["bundle_name"],
                "target": dep["target"],
                "root_path": dep["root_path"],
                "mode": dep["mode"],
                "deployed_at": int(dep["deployed_at"].timestamp() * 1000),
                "resources": items,
            }
        )
    return out
