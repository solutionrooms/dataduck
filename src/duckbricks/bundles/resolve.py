"""Load, merge and resolve a `databricks.yml` bundle the way the Databricks CLI does (v1 subset).

Pipeline: load the root file and its `include` globs -> merge -> pick a target and overlay it ->
resolve variables -> interpolate `${...}` -> apply the target mode -> translate relative paths to
workspace paths. `${resources.<type>.<key>.id}` stays as written; the API resolves it at apply
time.
"""

from __future__ import annotations

import copy
import fnmatch
import glob
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT_FILES = ("databricks.yml", "databricks.yaml", "bundle.yml", "bundle.yaml")
REFERENCE = re.compile(r"\$\{([A-Za-z0-9_.\-\[\]]+)\}")
KEYED_LISTS = {
    "tasks": "task_key",
    "job_clusters": "job_cluster_key",
    "environments": "environment_key",
}
SUPPORTED_RESOURCES = ("jobs", "schemas", "pipelines")


class BundleError(ValueError):
    pass


@dataclass
class LoadedBundle:
    root: Path
    config: dict[str, Any]
    # (resource type, key) -> directory of the YAML file that defined it, for path translation
    origins: dict[tuple[str, str], Path] = field(default_factory=dict)
    files: list[Path] = field(default_factory=list)


@dataclass
class ResolvedBundle:
    root: Path
    target: str
    config: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    referenced_files: list[tuple[str, Path]] = field(default_factory=list)  # (field, local path)

    @property
    def name(self) -> str:
        return str(self.config["bundle"]["name"])

    @property
    def root_path(self) -> str:
        return str(self.config["workspace"]["root_path"])

    @property
    def file_path(self) -> str:
        return str(self.config["workspace"]["file_path"])


# --- loading ---------------------------------------------------------------------------------


def find_root(start: Path) -> Path:
    for directory in (start, *start.parents):
        for name in ROOT_FILES:
            if (directory / name).exists():
                return directory
    raise BundleError(f"No databricks.yml found in {start} or its parents")


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise BundleError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise BundleError(f"{path}: expected a mapping at the top level")
    return data


def load(root: Path) -> LoadedBundle:
    root = root.resolve()
    root_file = next((root / n for n in ROOT_FILES if (root / n).exists()), None)
    if root_file is None:
        raise BundleError(f"No databricks.yml in {root}")
    config = _read_yaml(root_file)
    bundle = LoadedBundle(root=root, config=config, files=[root_file])
    _record_origins(bundle, config, root_file.parent)
    for pattern in config.get("include") or []:
        matches = sorted(glob.glob(str(root / pattern), recursive=True))
        if not matches and not glob.has_magic(pattern):
            raise BundleError(f"include: {pattern} does not exist")
        for match in matches:
            path = Path(match)
            if path == root_file:
                continue
            part = _read_yaml(path)
            if "bundle" in part or "include" in part:
                raise BundleError(f"{path}: included files cannot set 'bundle' or 'include'")
            _record_origins(bundle, part, path.parent)
            _merge_included(config, part, path)
            bundle.files.append(path)
    return bundle


def _record_origins(bundle: LoadedBundle, part: dict[str, Any], directory: Path) -> None:
    for rtype, items in (part.get("resources") or {}).items():
        for key in items or {}:
            bundle.origins.setdefault((rtype, key), directory)
    for target in (part.get("targets") or {}).values():
        for rtype, items in ((target or {}).get("resources") or {}).items():
            for key in items or {}:
                bundle.origins.setdefault((rtype, key), directory)


def _merge_included(config: dict[str, Any], part: dict[str, Any], path: Path) -> None:
    for top, value in part.items():
        if top == "resources":
            resources = config.setdefault("resources", {})
            for rtype, items in (value or {}).items():
                existing = resources.setdefault(rtype, {})
                for key, item in (items or {}).items():
                    if key in existing:
                        raise BundleError(f"{path}: resource {rtype}.{key} is defined twice")
                    existing[key] = item
        elif top in ("targets", "variables"):
            existing = config.setdefault(top, {})
            for key, item in (value or {}).items():
                existing[key] = deep_merge(existing.get(key), item)
        else:
            config[top] = deep_merge(config.get(top), value)


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            if key in KEYED_LISTS and isinstance(value, list) and isinstance(out.get(key), list):
                out[key] = _merge_keyed(out[key], value, KEYED_LISTS[key])
            else:
                out[key] = deep_merge(out.get(key), value)
        return out
    return copy.deepcopy(override) if override is not None else copy.deepcopy(base)


def _merge_keyed(base: list[Any], override: list[Any], key: str) -> list[Any]:
    merged = {item.get(key): item for item in base if isinstance(item, dict)}
    order = list(merged)
    for item in override:
        k = item.get(key) if isinstance(item, dict) else None
        if k in merged:
            merged[k] = deep_merge(merged[k], item)
        else:
            merged[k] = item
            order.append(k)
    return [merged[k] for k in order]


# --- resolution ------------------------------------------------------------------------------


def pick_target(config: dict[str, Any], target: str | None) -> str:
    targets = config.get("targets") or {}
    if target:
        if target not in targets:
            raise BundleError(
                f"Target '{target}' is not defined. Targets: {', '.join(targets) or 'none'}"
            )
        return target
    defaults = [name for name, t in targets.items() if (t or {}).get("default")]
    if len(defaults) > 1:
        raise BundleError(f"More than one default target: {', '.join(defaults)}")
    if defaults:
        return str(defaults[0])
    if len(targets) == 1:
        return str(next(iter(targets)))
    if not targets:
        return "default"
    raise BundleError(f"Pick a target with -t; no default among {', '.join(targets)}")


def overlay_target(config: dict[str, Any], target: str) -> dict[str, Any]:
    t = copy.deepcopy((config.get("targets") or {}).get(target) or {})
    out = {k: copy.deepcopy(v) for k, v in config.items() if k not in ("targets", "environments")}
    target_vars = t.pop("variables", None) or {}
    for key in ("default", "compute_id", "cluster_id", "git"):
        t.pop(key, None)
    mode = t.pop("mode", None)
    for key, value in t.items():
        out[key] = deep_merge(out.get(key), value)
    variables = out.setdefault("variables", {})
    for name, value in target_vars.items():
        if name not in variables:
            raise BundleError(f"targets.{target}.variables.{name} is not declared in variables")
        spec = dict(variables[name] or {})
        if isinstance(value, dict) and set(value) <= {"default", "lookup", "description"}:
            spec.update(value)
        else:
            spec["default"] = value
        variables[name] = spec
    out.setdefault("bundle", {})["target"] = target
    if mode:
        out["bundle"]["mode"] = mode
    return out


def variable_values(
    config: dict[str, Any], overrides: dict[str, str], env: dict[str, str]
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    missing = []
    for name, spec in (config.get("variables") or {}).items():
        spec = spec or {}
        if "lookup" in spec:
            raise BundleError(
                f"variables.{name}.lookup is not supported by Duckbricks; set a default instead"
            )
        if name in overrides:
            values[name] = overrides[name]
        elif f"BUNDLE_VAR_{name}" in env:
            values[name] = env[f"BUNDLE_VAR_{name}"]
        elif "default" in spec:
            values[name] = spec["default"]
        else:
            missing.append(name)
    if missing:
        raise BundleError(
            f"No value assigned to required variable(s): {', '.join(missing)}. "
            "Set a default, a target override, --var name=value or BUNDLE_VAR_<name>."
        )
    unknown = sorted(set(overrides) - set(values))
    if unknown:
        raise BundleError(f"--var given for undeclared variable(s): {', '.join(unknown)}")
    return values


def _lookup(ref: str, scope: dict[str, Any]) -> Any:
    if ref.startswith("variables.") and ref.endswith(".value"):
        ref = "var." + ref[len("variables.") : -len(".value")]
    if ref == "bundle.environment":
        ref = "bundle.target"
    node: Any = scope
    for part in ref.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise KeyError(ref)
    return node


def interpolate(value: Any, scope: dict[str, Any], passes: int = 10) -> Any:
    for _ in range(passes):
        new = _interpolate_once(value, scope)
        if new == value:
            return new
        value = new
    return value


def _interpolate_once(value: Any, scope: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = REFERENCE.fullmatch(value)
        if whole and not whole.group(1).startswith("resources."):
            try:
                return copy.deepcopy(_lookup(whole.group(1), scope))
            except KeyError:
                raise BundleError(f"Reference ${{{whole.group(1)}}} cannot be resolved") from None

        def sub(match: re.Match[str]) -> str:
            ref = match.group(1)
            if ref.startswith("resources."):
                return match.group(0)
            try:
                found = _lookup(ref, scope)
            except KeyError:
                raise BundleError(f"Reference ${{{ref}}} cannot be resolved") from None
            if isinstance(found, (dict, list)):
                raise BundleError(f"${{{ref}}} is a complex value and cannot be embedded in text")
            return str(found)

        return REFERENCE.sub(sub, value)
    if isinstance(value, list):
        return [_interpolate_once(v, scope) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate_once(v, scope) for k, v in value.items()}
    return value


def short_name(user: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", user.split("@", 1)[0])


def resolve(
    root: Path,
    target: str | None = None,
    user_name: str = "user@duckbricks.local",
    variables: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
) -> ResolvedBundle:
    loaded = load(root)
    raw = loaded.config
    if not (raw.get("bundle") or {}).get("name"):
        raise BundleError("bundle.name is required")
    target_name = pick_target(raw, target)
    config = overlay_target(raw, target_name)
    warnings: list[str] = []
    mode = config["bundle"].get("mode")
    short = short_name(user_name)

    workspace = config.setdefault("workspace", {})
    for ignored in ("host", "profile", "auth_type", "client_id", "azure_workspace_resource_id"):
        if ignored in workspace:
            warnings.append(f"workspace.{ignored} is ignored (Duckbricks uses DBK_HOST)")
    workspace.setdefault(
        "root_path", f"/Users/{user_name}/.bundle/${{bundle.name}}/${{bundle.target}}"
    )
    workspace["root_path"] = workspace["root_path"].replace("~", f"/Users/{user_name}", 1)
    workspace.setdefault("file_path", "${workspace.root_path}/files")
    workspace.setdefault("artifact_path", "${workspace.root_path}/artifacts")
    workspace.setdefault("state_path", "${workspace.root_path}/state")

    values = variable_values(config, variables or {}, env if env is not None else dict(os.environ))
    scope = {
        "var": values,
        "bundle": {
            "name": config["bundle"]["name"],
            "target": target_name,
            "environment": target_name,
            "git": {},
        },
        "workspace": {
            "current_user": {
                "userName": user_name,
                "short_name": short,
                "user_name": user_name,
                "domain_friendly_name": short,
            },
        },
    }
    # Workspace paths may reference bundle/var/current_user; resolve them first.
    for key in ("root_path", "file_path", "artifact_path", "state_path"):
        scope["workspace"][key] = interpolate(
            workspace[key], {**scope, "workspace": scope["workspace"]}
        )
        workspace[key] = _strip_workspace_prefix(scope["workspace"][key])
        scope["workspace"][key] = workspace[key]

    config = interpolate(config, scope)
    resources = config.setdefault("resources", {}) or {}
    for rtype in list(resources):
        if rtype not in SUPPORTED_RESOURCES:
            warnings.append(
                f"resources.{rtype} is not supported by Duckbricks and was ignored "
                f"({len(resources[rtype] or {})} item(s))"
            )
            del resources[rtype]
    for unsupported in ("artifacts", "python", "scripts", "run_as", "permissions", "experimental"):
        if config.get(unsupported):
            warnings.append(f"{unsupported} is not supported by Duckbricks and was ignored")

    resolved = ResolvedBundle(
        root=loaded.root, target=target_name, config=config, warnings=warnings
    )
    _apply_mode(resolved, mode, short)
    _translate_paths(resolved, loaded.origins)
    return resolved


def _strip_workspace_prefix(path: str) -> str:
    return path[len("/Workspace") :] if path.startswith("/Workspace/") else path


def _apply_mode(bundle: ResolvedBundle, mode: str | None, short: str) -> None:
    config = bundle.config
    presets = config.get("presets") or {}
    resources = config.get("resources") or {}
    prefix = presets.get("name_prefix")
    if mode == "development" and prefix is None:
        prefix = f"[dev {short}] "
    if mode not in (None, "development", "production"):
        raise BundleError(f"bundle mode must be development or production, not {mode}")
    for job in (resources.get("jobs") or {}).values():
        if prefix:
            job["name"] = prefix + str(job.get("name") or "")
        if mode == "development" or presets.get("trigger_pause_status") == "PAUSED":
            for key in ("schedule", "trigger"):
                if job.get(key):
                    job[key]["pause_status"] = "PAUSED"
            job.setdefault("tags", {})
            if mode == "development":
                job["tags"]["dev"] = short
        if presets.get("jobs_max_concurrent_runs"):
            job["max_concurrent_runs"] = presets["jobs_max_concurrent_runs"]
    for pipeline in (resources.get("pipelines") or {}).values():
        if prefix:
            pipeline["name"] = prefix + str(pipeline.get("name") or "")
        if mode == "development":
            pipeline["development"] = True


PATH_FIELDS = (
    ("sql_task", ("file", "path")),
    ("spark_python_task", ("python_file",)),
    ("notebook_task", ("notebook_path",)),
    ("dbt_task", ("project_directory",)),
)


def _is_local(path: str) -> bool:
    return not (
        path.startswith("/") or "://" in path or path.startswith("dbfs:") or path.startswith("${")
    )


def _translate(bundle: ResolvedBundle, origin: Path, path: str, label: str) -> str:
    local = (origin / path).resolve()
    try:
        relative = local.relative_to(bundle.root)
    except ValueError:
        raise BundleError(f"{label}: {path} is outside the bundle root") from None
    bundle.referenced_files.append((label, local))
    rel = relative.as_posix()
    return bundle.file_path if rel == "." else f"{bundle.file_path}/{rel}"


def _translate_paths(bundle: ResolvedBundle, origins: dict[tuple[str, str], Path]) -> None:
    resources = bundle.config.get("resources") or {}
    for key, job in (resources.get("jobs") or {}).items():
        origin = origins.get(("jobs", key), bundle.root)
        source_is_git = bool(job.get("git_source"))
        for task in job.get("tasks") or []:
            for kind, path_keys in PATH_FIELDS:
                spec = task.get(kind)
                if not isinstance(spec, dict) or spec.get("source") == "GIT" or source_is_git:
                    continue
                holder: Any = spec
                for part in path_keys[:-1]:
                    holder = holder.get(part) if isinstance(holder, dict) else None
                if not isinstance(holder, dict):
                    continue
                value = holder.get(path_keys[-1])
                if isinstance(value, str) and _is_local(value):
                    label = f"resources.jobs.{key}.tasks[{task.get('task_key')}].{kind}"
                    holder[path_keys[-1]] = _translate(bundle, origin, value, label)
    for key, pipeline in (resources.get("pipelines") or {}).items():
        origin = origins.get(("pipelines", key), bundle.root)
        for library in pipeline.get("libraries") or []:
            for kind in ("notebook", "file"):
                spec = library.get(kind)
                if (
                    isinstance(spec, dict)
                    and isinstance(spec.get("path"), str)
                    and _is_local(spec["path"])
                ):
                    label = f"resources.pipelines.{key}.libraries.{kind}"
                    spec["path"] = _translate(bundle, origin, spec["path"], label)
            spec = library.get("glob")
            if (
                isinstance(spec, dict)
                and isinstance(spec.get("include"), str)
                and _is_local(spec["include"])
            ):
                include = spec["include"]
                base = include.split("*", 1)[0].rstrip("/") or "."
                translated = _translate(bundle, origin, base, f"resources.pipelines.{key}.glob")
                spec["include"] = (
                    translated + include[len(base) :]
                    if base != "."
                    else (f"{translated}/{include}")
                )
        root_path = pipeline.get("root_path")
        if isinstance(root_path, str) and _is_local(root_path):
            pipeline["root_path"] = _translate(
                bundle, origin, root_path, f"resources.pipelines.{key}.root_path"
            )


# --- file sync ---------------------------------------------------------------------------

DEFAULT_EXCLUDES = (
    ".git",
    ".databricks",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".DS_Store",
    "target",
    "logs",
    "dbt_packages",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)


def sync_files(bundle: ResolvedBundle) -> list[Path]:
    """Files to upload: everything under the root minus default and .gitignore excludes."""
    sync = bundle.config.get("sync") or {}
    excludes = list(sync.get("exclude") or [])
    includes = list(sync.get("include") or [])
    gitignore = bundle.root / ".gitignore"
    if gitignore.exists():
        excludes += [
            line.strip()
            for line in gitignore.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
    out = []
    for dirpath, dirnames, filenames in os.walk(bundle.root):
        rel_dir = Path(dirpath).relative_to(bundle.root)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in DEFAULT_EXCLUDES and not _excluded((rel_dir / d).as_posix() + "/", excludes)
        ]
        for name in filenames:
            rel = (rel_dir / name).as_posix()
            if name in DEFAULT_EXCLUDES:
                continue
            if _excluded(rel, excludes) and not _excluded(rel, includes):
                continue
            out.append(bundle.root / rel)
    return sorted(out)


def _excluded(rel: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        p = pattern.lstrip("/")
        if p.endswith("/"):
            if rel.startswith(p) or f"/{p}" in f"/{rel}":
                return True
            continue
        name = rel.rstrip("/").rsplit("/", 1)[-1]
        if fnmatch.fnmatch(rel.rstrip("/"), p) or fnmatch.fnmatch(name, p):
            return True
        if fnmatch.fnmatch(rel, p + "/*"):
            return True
    return False
