from datetime import UTC, datetime

from duckbricks.core.dynamic_values import Context, resolve


def ctx() -> Context:
    return Context(
        job_id=7,
        job_name="daily",
        run_id=1001,
        task_key="load",
        task_run_id=1002,
        attempt=0,
        start_time=datetime(2026, 10, 25, 6, 0, tzinfo=UTC),
        trigger="PERIODIC",
        parameters={"catalog": "dev"},
        task_values={"check": {"rows": 42, "label": "ok"}},
    )


def test_resolves_nested_values() -> None:
    task = {
        "sql_task": {
            "parameters": {
                "catalog": "{{job.parameters.catalog}}",
                "d": "{{ job.start_time.iso_date }}",
            }
        },
        "args": ["{{job.id}}", "{{job.run_id}}", "{{task.name}}", "{{tasks.check.values.rows}}"],
        "label": "{{tasks.check.values.label}}-{{job.trigger.type}}",
    }
    out = resolve(task, ctx())
    assert out["sql_task"]["parameters"] == {"catalog": "dev", "d": "2026-10-25"}
    assert out["args"] == ["7", "1001", "load", "42"]
    assert out["label"] == "ok-periodic"


def test_unknown_references_stay() -> None:
    assert resolve("{{job.parameters.nope}} {{secrets.x}}", ctx()) == (
        "{{job.parameters.nope}} {{secrets.x}}"
    )
