"""Bundle validation: JSON schema, then Duckbricks semantic checks (SPEC section 8).

Runs client-side (CLI and MCP) without touching the platform, so it works offline. Job settings
go through the same validator the Jobs API uses, so a bundle that validates also deploys.
"""

from __future__ import annotations

import base64
import io
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from duckbricks.bundles.resolve import BundleError, ResolvedBundle, resolve, sync_files
from duckbricks.bundles.schema import schema_errors
from duckbricks.core.errors import InvalidParameter

PIPELINE_FIELDS_SUPPORTED = {
    "name",
    "catalog",
    "schema",
    "target",
    "libraries",
    "configuration",
    "development",
    "continuous",
    "root_path",
    "edition",
    "channel",
    "serverless",
    "photon",
    "clusters",
    "notifications",
    "permissions",
    "tags",
}


@dataclass
class ValidationResult:
    bundle: ResolvedBundle | None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> dict[str, Any]:
        if self.bundle is None:
            return {"ok": False, "errors": self.errors, "warnings": self.warnings}
        resources = self.bundle.config.get("resources") or {}
        return {
            "ok": self.ok,
            "name": self.bundle.name,
            "target": self.bundle.target,
            "mode": self.bundle.config["bundle"].get("mode"),
            "root_path": self.bundle.root_path,
            "resources": {
                rtype: sorted((items or {}).keys()) for rtype, items in resources.items() if items
            },
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _check_jobs(bundle: ResolvedBundle, result: ValidationResult) -> None:
    from duckbricks.core.jobs import validate_settings

    for key, job in ((bundle.config.get("resources") or {}).get("jobs") or {}).items():
        settings = dict(job)
        settings.pop("permissions", None)
        try:
            _, warnings = validate_settings(_placeholder_refs(settings))
        except InvalidParameter as exc:
            result.errors.append(f"resources.jobs.{key}: {exc.message}")
            continue
        result.warnings += [f"resources.jobs.{key}: {w}" for w in warnings]


def _placeholder_refs(value: Any) -> Any:
    """`${resources.jobs.x.id}` resolves at deploy; validate with a stand-in id."""
    if isinstance(value, str) and value.startswith("${resources."):
        return 0 if value.startswith("${resources.jobs.") else "placeholder"
    if isinstance(value, list):
        return [_placeholder_refs(v) for v in value]
    if isinstance(value, dict):
        return {k: _placeholder_refs(v) for k, v in value.items()}
    return value


def _check_pipelines(bundle: ResolvedBundle, result: ValidationResult) -> None:
    for key, pipeline in ((bundle.config.get("resources") or {}).get("pipelines") or {}).items():
        for field_name in sorted(set(pipeline) - PIPELINE_FIELDS_SUPPORTED):
            result.warnings.append(f"resources.pipelines.{key}: {field_name} is not supported")
        if not pipeline.get("libraries"):
            result.errors.append(f"resources.pipelines.{key}: libraries is required")
        if not pipeline.get("catalog"):
            result.errors.append(f"resources.pipelines.{key}: catalog is required")
        for library in pipeline.get("libraries") or []:
            if not set(library) & {"file", "glob", "notebook"}:
                result.errors.append(
                    f"resources.pipelines.{key}: only file, glob and notebook libraries "
                    "(SQL) are supported"
                )


def _check_schemas(bundle: ResolvedBundle, result: ValidationResult) -> None:
    for key, schema in ((bundle.config.get("resources") or {}).get("schemas") or {}).items():
        if not schema.get("catalog_name") or not schema.get("name"):
            result.errors.append(f"resources.schemas.{key}: catalog_name and name are required")
        for field_name in sorted(set(schema) - {"catalog_name", "name", "comment", "grants"}):
            result.warnings.append(f"resources.schemas.{key}: {field_name} is not supported")
        if schema.get("grants"):
            result.warnings.append(f"resources.schemas.{key}: grants are accepted and ignored")


def validate(
    root: Path,
    target: str | None = None,
    user_name: str = "user@duckbricks.local",
    variables: dict[str, str] | None = None,
) -> ValidationResult:
    from duckbricks.bundles.resolve import load

    try:
        raw = load(root).config
    except BundleError as exc:
        return ValidationResult(None, errors=[str(exc)])
    errors = schema_errors(raw)
    if errors:
        return ValidationResult(None, errors=[f"schema: {e}" for e in errors])
    try:
        bundle = resolve(root, target, user_name, variables)
    except BundleError as exc:
        return ValidationResult(None, errors=[str(exc)])
    result = ValidationResult(bundle, warnings=list(bundle.warnings))
    synced = set(sync_files(bundle))
    for label, local in bundle.referenced_files:
        if not local.exists():
            result.errors.append(f"{label}: {local.relative_to(bundle.root)} does not exist")
        elif local.is_file() and local not in synced:
            result.errors.append(f"{label}: {local.relative_to(bundle.root)} is excluded from sync")
    _check_jobs(bundle, result)
    _check_pipelines(bundle, result)
    _check_schemas(bundle, result)
    return result


def package_files(bundle: ResolvedBundle) -> tuple[str, int]:
    """The synced files as a base64 tar.gz, and how many there are."""
    buffer = io.BytesIO()
    files = sync_files(bundle)
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in files:
            tar.add(path, arcname=path.relative_to(bundle.root).as_posix(), recursive=False)
    return base64.b64encode(buffer.getvalue()).decode(), len(files)
