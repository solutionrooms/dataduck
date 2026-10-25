"""Golden-file tests for bundle resolution: input YAML to resolved JSON.

Regenerate the expected files after an intended change with
`UPDATE_GOLDEN=1 uv run pytest tests/unit/test_bundle_resolve.py`, then review the diff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from duckbricks.bundles.resolve import BundleError, resolve, sync_files
from duckbricks.bundles.validate import validate

REPO = Path(__file__).resolve().parents[2]
GOLDEN = REPO / "tests" / "golden" / "bundles"
USER = "jane.doe@example.com"

CASES = [
    ("shop_demo", REPO / "examples" / "bundles" / "shop_demo", "dev", {}, {}),
    ("shop_demo", REPO / "examples" / "bundles" / "shop_demo", "prod", {}, {}),
    ("web_events", REPO / "examples" / "bundles" / "web_events", "dev", {}, {}),
    (
        "edge_cases",
        GOLDEN / "edge_cases",
        "dev",
        {"retries": "4"},
        {"BUNDLE_VAR_env_var": "from_env"},
    ),
    ("edge_cases", GOLDEN / "edge_cases", "staging", {}, {}),
    ("edge_cases", GOLDEN / "edge_cases", "prod", {}, {}),
]


def snapshot(root: Path, target: str, variables: dict[str, str], env: dict[str, str]) -> dict:
    bundle = resolve(root, target, USER, variables, env)
    return {
        "target": bundle.target,
        "config": bundle.config,
        "warnings": bundle.warnings,
        "referenced_files": sorted(
            (label, path.relative_to(root).as_posix()) for label, path in bundle.referenced_files
        ),
        "synced_files": sorted(p.relative_to(root).as_posix() for p in sync_files(bundle)),
    }


@pytest.mark.parametrize(
    ("name", "root", "target", "variables", "env"),
    CASES,
    ids=[f"{c[0]}-{c[2]}" for c in CASES],
)
def test_resolution_matches_golden(
    name: str, root: Path, target: str, variables: dict[str, str], env: dict[str, str]
) -> None:
    actual = json.loads(json.dumps(snapshot(root, target, variables, env), default=str))
    expected_file = GOLDEN / f"{name}.{target}.json"
    if os.environ.get("UPDATE_GOLDEN") or not expected_file.exists():
        expected_file.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    assert actual == json.loads(expected_file.read_text())


def test_development_mode_prefixes_and_pauses() -> None:
    config = resolve(GOLDEN / "edge_cases", "dev", USER, env={}).config
    job = config["resources"]["jobs"]["nightly"]
    assert job["name"] == "[dev jane_doe] nightly dev"
    assert job["schedule"]["pause_status"] == "PAUSED"
    assert job["tags"]["dev"] == "jane_doe"


def test_variable_precedence() -> None:
    config = resolve(
        GOLDEN / "edge_cases", "dev", USER, {"retries": "4"}, {"BUNDLE_VAR_env_var": "env"}
    ).config
    load = config["resources"]["jobs"]["nightly"]["tasks"][0]
    assert load["max_retries"] == 4  # --var beats default; coerced to the schema type
    assert load["sql_task"]["parameters"]["note"] == "env"  # BUNDLE_VAR_ beats default


def test_target_override_merges_tasks_by_key() -> None:
    config = resolve(GOLDEN / "edge_cases", "staging", USER, env={}).config
    job = config["resources"]["jobs"]["nightly"]
    assert job["max_concurrent_runs"] == 3
    assert [t["task_key"] for t in job["tasks"]] == ["load", "report", "downstream"]
    load = job["tasks"][0]
    assert load["timeout_seconds"] == 600 and "sql_task" in load
    assert job["name"] == "[staging] nightly staging"
    assert job["schedule"]["pause_status"] == "PAUSED"


def test_resource_references_are_left_for_the_server() -> None:
    config = resolve(GOLDEN / "edge_cases", "prod", USER, env={}).config
    task = config["resources"]["jobs"]["nightly"]["tasks"][2]
    assert task["run_job_task"]["job_id"] == "${resources.jobs.cleanup.id}"


def test_unsupported_resources_warn() -> None:
    bundle = resolve(GOLDEN / "edge_cases", "prod", USER, env={})
    assert "dashboards" not in bundle.config["resources"]
    assert any("resources.dashboards" in w for w in bundle.warnings)
    assert any("workspace.host" in w for w in bundle.warnings)


def test_unknown_target_is_an_error() -> None:
    with pytest.raises(BundleError):
        resolve(GOLDEN / "edge_cases", "nope", USER, env={})


def test_validate_reports_missing_file(tmp_path: Path) -> None:
    (tmp_path / "databricks.yml").write_text(
        "bundle: {name: broken}\n"
        "resources:\n"
        "  jobs:\n"
        "    j:\n"
        "      name: j\n"
        "      tasks:\n"
        "        - task_key: t\n"
        "          spark_python_task: {python_file: ./missing.py}\n"
    )
    result = validate(tmp_path, None, USER)
    assert not result.ok
    assert any("missing.py" in e for e in result.errors)


def test_validate_rejects_schema_errors(tmp_path: Path) -> None:
    (tmp_path / "databricks.yml").write_text(
        "bundle: {name: broken}\nresources:\n  jobs:\n    j:\n      max_concurrent_runs: lots\n"
    )
    result = validate(tmp_path, None, USER)
    assert not result.ok
    assert any(e.startswith("schema:") for e in result.errors)


def test_validate_rejects_unsupported_task_type(tmp_path: Path) -> None:
    (tmp_path / "databricks.yml").write_text(
        "bundle: {name: broken}\n"
        "resources:\n"
        "  jobs:\n"
        "    j:\n"
        "      name: j\n"
        "      tasks:\n"
        "        - task_key: t\n"
        "          spark_jar_task: {main_class_name: Main}\n"
    )
    result = validate(tmp_path, None, USER)
    assert not result.ok
    assert any("spark_jar_task" in e for e in result.errors)
