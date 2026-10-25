import pytest

from duckbricks.core.errors import InvalidParameter
from duckbricks.core.jobs import node_type_for_task, validate_settings


def sql(key: str, **extra: object) -> dict[str, object]:
    return {"task_key": key, "sql_task": {"file": {"path": f"/x/{key}.sql"}}, **extra}


def test_minimal_job_gets_defaults() -> None:
    settings, warnings = validate_settings({"tasks": [sql("a")]})
    assert settings["name"] == "Untitled" and settings["max_concurrent_runs"] == 1
    assert warnings == []


@pytest.mark.parametrize(
    ("tasks", "message"),
    [
        ([], "at least one task"),
        ([sql("a"), sql("a")], "Duplicate"),
        ([{"task_key": "a", "python_wheel_task": {}}], "cannot run"),
        ([{"task_key": "a", "sql_task": {"query": {"query_id": "1"}}}], "sql_task.query"),
        ([sql("a", depends_on=[{"task_key": "b"}])], "not a task"),
        (
            [sql("a", depends_on=[{"task_key": "b"}]), sql("b", depends_on=[{"task_key": "a"}])],
            "cycle",
        ),
        ([sql("a", run_if="SOMETIMES")], "run_if"),
        ([{"task_key": "a", "dbt_task": {"commands": ["ls"]}}], "start with 'dbt'"),
    ],
)
def test_errors(tasks: list[dict[str, object]], message: str) -> None:
    with pytest.raises(InvalidParameter, match=message):
        validate_settings({"tasks": tasks})


def test_schedule_is_checked() -> None:
    with pytest.raises(InvalidParameter, match="seconds"):
        validate_settings(
            {"tasks": [sql("a")], "schedule": {"quartz_cron_expression": "15 0 6 * * ?"}}
        )
    settings, _ = validate_settings(
        {"tasks": [sql("a")], "schedule": {"quartz_cron_expression": "0 0 6 * * ?"}}
    )
    assert settings["schedule"]["timezone_id"] == "UTC"


def test_warnings_for_ignored_fields() -> None:
    _, warnings = validate_settings(
        {
            "tasks": [sql("a", libraries=[{"pypi": {"package": "x"}}])],
            "webhook_notifications": {},
            "unknown_thing": 1,
            "trigger": {"table_update": {"table_names": ["x"]}},
        }
    )
    text = " ".join(warnings)
    assert "libraries" in text and "webhook_notifications" in text
    assert "unknown_thing" in text and "table_update" in text


def test_node_type_resolution() -> None:
    settings = {
        "job_clusters": [{"job_cluster_key": "big", "new_cluster": {"node_type_id": "dbk.large"}}]
    }
    assert node_type_for_task(settings, {"job_cluster_key": "big"}) == "dbk.large"
    assert node_type_for_task(settings, {"new_cluster": {"node_type_id": "i3.xlarge"}}) == (
        "dbk.medium"
    )
    assert node_type_for_task(settings, {}) == "dbk.medium"
