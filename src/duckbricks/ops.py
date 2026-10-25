"""Operations shared by the CLI and the MCP server. Each one is a few API calls.

Only bundle validation reads local files (the bundle directory); everything else goes
through the API, like any other client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from duckbricks.bundles.resolve import BundleError, find_root
from duckbricks.bundles.validate import ValidationResult, package_files, validate
from duckbricks.client import ApiCallError, DuckbricksClient

TERMINAL = ("TERMINATED", "SKIPPED", "INTERNAL_ERROR")


def current_user(client: DuckbricksClient) -> str:
    return str(client.get("/api/2.0/preview/scim/v2/Me")["userName"])


def bundle_root(path: str | Path | None) -> Path:
    return find_root(Path(path or ".").expanduser().resolve())


def bundle_validate(
    client: DuckbricksClient,
    path: str | Path | None = None,
    target: str | None = None,
    variables: dict[str, str] | None = None,
) -> ValidationResult:
    try:
        root = bundle_root(path)
    except BundleError as exc:
        return ValidationResult(None, errors=[str(exc)])
    return validate(root, target, current_user(client), variables)


def bundle_deploy(
    client: DuckbricksClient,
    path: str | Path | None = None,
    target: str | None = None,
    variables: dict[str, str] | None = None,
) -> dict[str, Any]:
    result = bundle_validate(client, path, target, variables)
    if not result.ok or result.bundle is None:
        raise BundleError("Validation failed:\n- " + "\n- ".join(result.errors))
    bundle = result.bundle
    files, count = package_files(bundle)
    config = dict(bundle.config)
    config["bundle"] = {**config["bundle"], "target": bundle.target}
    report = client.post(
        "/api/duckbricks/bundles/deploy", {"config": config, "files_tar_gz_b64": files}
    )
    local = set(result.warnings)
    server = [w for w in report.get("warnings", []) if f"resources.{w}" not in local]
    report["warnings"] = list(dict.fromkeys([*result.warnings, *server]))
    report.update(bundle_name=bundle.name, target=bundle.target, files=count)
    return report


def bundle_identity(
    client: DuckbricksClient, path: str | Path | None, target: str | None
) -> tuple[str, str]:
    result = bundle_validate(client, path, target)
    if result.bundle is None:
        raise BundleError("; ".join(result.errors))
    return result.bundle.name, result.bundle.target


def bundle_summary(
    client: DuckbricksClient, path: str | Path | None = None, target: str | None = None
) -> dict[str, Any]:
    name, target = bundle_identity(client, path, target)
    deployments = client.get("/api/duckbricks/bundles", bundle_name=name, target=target)[
        "deployments"
    ]
    if not deployments:
        raise BundleError(f"Bundle {name} is not deployed to target {target}")
    return dict(deployments[0])


def bundle_job_id(
    client: DuckbricksClient, job_key: str, path: str | Path | None, target: str | None
) -> int:
    summary = bundle_summary(client, path, target)
    for resource in summary["resources"]:
        if resource["resource_type"] == "jobs" and resource["resource_key"] == job_key:
            return int(resource["resource_id"])
    keys = [r["resource_key"] for r in summary["resources"] if r["resource_type"] == "jobs"]
    raise BundleError(f"No job '{job_key}' in the deployed bundle (jobs: {', '.join(keys)})")


def bundle_destroy(
    client: DuckbricksClient, path: str | Path | None = None, target: str | None = None
) -> dict[str, Any]:
    name, target = bundle_identity(client, path, target)
    report = client.post("/api/duckbricks/bundles/destroy", {"bundle_name": name, "target": target})
    report.update(bundle_name=name, target=target)
    return report


def run_now(
    client: DuckbricksClient, job_id: int, job_parameters: dict[str, str] | None = None
) -> int:
    body: dict[str, Any] = {"job_id": job_id}
    if job_parameters:
        body["job_parameters"] = job_parameters
    return int(client.post("/api/2.2/jobs/run-now", body)["run_id"])


def failed_task_logs(
    client: DuckbricksClient, run: dict[str, Any], lines: int = 200
) -> dict[str, str]:
    out = {}
    for task in run.get("tasks", []):
        if task.get("state", {}).get("result_state") in ("FAILED", "TIMEDOUT", "INTERNAL_ERROR"):
            try:
                logs = client.get(f"/api/duckbricks/runs/{task['run_id']}/logs")["logs"]
            except ApiCallError:
                continue
            out[task["task_key"]] = "\n".join(logs.splitlines()[-lines:])
    return out


__all__ = [
    "TERMINAL",
    "ApiCallError",
    "BundleError",
    "bundle_deploy",
    "bundle_destroy",
    "bundle_job_id",
    "bundle_summary",
    "bundle_validate",
    "current_user",
    "failed_task_logs",
    "run_now",
]
