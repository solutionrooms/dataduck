"""Phase 1 acceptance: jobs created from JSON run on the worker (make up)."""

import base64
import uuid

import pytest

from duckbricks.client import DuckbricksClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client() -> DuckbricksClient:
    return DuckbricksClient()


def upload(client: DuckbricksClient, path: str, content: str) -> None:
    client.post(
        "/api/2.0/workspace/import",
        {
            "path": path,
            "content": base64.b64encode(content.encode()).decode(),
            "overwrite": True,
        },
    )


def make_job(client: DuckbricksClient, root: str, table: str, fail: bool = False) -> int:
    upload(
        client,
        f"{root}/create.sql",
        f"CREATE OR REPLACE TABLE dev.default.{table} AS SELECT range AS id FROM range($n::INT);\n"
        f"INSERT INTO dev.default.{table} VALUES (-1);\n",
    )
    body = "raise RuntimeError('boom from task code')" if fail else ""
    upload(
        client,
        f"{root}/check.py",
        "import sys\n"
        "from duckbricks.runtime import con, spark, dbutils\n"
        f"{body}\n"
        f"n = con.execute('SELECT count(*) FROM dev.default.{table}').fetchone()[0]\n"
        f"assert spark.table('dev.default.{table}').count() == n\n"
        "assert n == int(sys.argv[1]) + 1, n\n"
        "dbutils.jobs.taskValues.set('rows', n)\n"
        "print('rows', n)\n",
    )
    job = client.post(
        "/api/2.2/jobs/create",
        {
            "name": f"test {table}",
            "parameters": [{"name": "n", "default": "5"}],
            "max_concurrent_runs": 2,
            "tasks": [
                {
                    "task_key": "create",
                    "sql_task": {
                        "file": {"path": f"{root}/create.sql"},
                        "parameters": {"n": "{{job.parameters.n}}"},
                    },
                },
                {
                    "task_key": "check",
                    "depends_on": [{"task_key": "create"}],
                    "spark_python_task": {
                        "python_file": f"{root}/check.py",
                        "parameters": ["{{job.parameters.n}}"],
                    },
                },
                {
                    "task_key": "report",
                    "depends_on": [{"task_key": "check"}],
                    "condition_task": {
                        "op": "EQUAL_TO",
                        "left": "{{tasks.check.values.rows}}",
                        "right": "6",
                    },
                },
            ],
        },
    )
    return int(job["job_id"])


def test_two_task_job_succeeds(client: DuckbricksClient) -> None:
    root = f"/Users/test/{uuid.uuid4().hex[:8]}"
    table = f"jobs_ok_{uuid.uuid4().hex[:6]}"
    job_id = make_job(client, root, table)
    run_id = client.post("/api/2.2/jobs/run-now", {"job_id": job_id})["run_id"]
    run = client.wait_for_run(run_id, timeout=180)
    states = {t["task_key"]: t["state"].get("result_state") for t in run["tasks"]}
    assert run["state"]["result_state"] == "SUCCESS", (run["state"], states)
    assert states == {"create": "SUCCESS", "check": "SUCCESS", "report": "SUCCESS"}
    report = next(t for t in run["tasks"] if t["task_key"] == "report")
    assert report["duckbricks_output"]["condition_outcome"] == "true"
    out = client.get("/api/2.2/jobs/runs/get-output", run_id=run["tasks"][1]["run_id"])
    assert "rows 6" in out["logs"]
    client.post("/api/2.0/sql/statements", {"statement": f"DROP TABLE dev.default.{table}"})


def test_failing_first_task_marks_downstream_upstream_failed(client: DuckbricksClient) -> None:
    root = f"/Users/test/{uuid.uuid4().hex[:8]}"
    table = f"jobs_fail_{uuid.uuid4().hex[:6]}"
    job_id = make_job(client, root, table)
    # Break the first task: the SQL file now fails.
    upload(client, f"{root}/create.sql", "SELECT * FROM table_that_does_not_exist;")
    run = client.wait_for_run(client.post("/api/2.2/jobs/run-now", {"job_id": job_id})["run_id"])
    states = {t["task_key"]: t["state"].get("result_state") for t in run["tasks"]}
    assert run["state"]["result_state"] == "FAILED"
    assert states["create"] == "FAILED"
    assert states["check"] == "UPSTREAM_FAILED"
    assert states["report"] == "UPSTREAM_FAILED"
    out = client.get("/api/2.2/jobs/runs/get-output", run_id=run["tasks"][0]["run_id"])
    assert "table_that_does_not_exist" in out["error"]
    assert "table_that_does_not_exist" in run["state"]["state_message"]


def test_python_error_text_is_reported(client: DuckbricksClient) -> None:
    root = f"/Users/test/{uuid.uuid4().hex[:8]}"
    table = f"jobs_py_{uuid.uuid4().hex[:6]}"
    job_id = make_job(client, root, table, fail=True)
    run = client.wait_for_run(client.post("/api/2.2/jobs/run-now", {"job_id": job_id})["run_id"])
    check = next(t for t in run["tasks"] if t["task_key"] == "check")
    assert check["state"]["result_state"] == "FAILED"
    out = client.get("/api/2.2/jobs/runs/get-output", run_id=check["run_id"])
    assert out["error"] == "RuntimeError: boom from task code"
    assert "Traceback" in out["error_trace"]
    client.post("/api/2.0/sql/statements", {"statement": f"DROP TABLE dev.default.{table}"})


def test_two_jobs_writing_different_tables_concurrently(client: DuckbricksClient) -> None:
    tables = [f"jobs_conc_{i}_{uuid.uuid4().hex[:6]}" for i in range(2)]
    job_ids = [make_job(client, f"/Users/test/{uuid.uuid4().hex[:8]}", t) for t in tables]
    run_ids = [
        client.post("/api/2.2/jobs/run-now", {"job_id": j, "job_parameters": {"n": "100000"}})[
            "run_id"
        ]
        for j in job_ids
    ]
    runs = [client.wait_for_run(r, timeout=300) for r in run_ids]
    assert [r["state"]["result_state"] for r in runs] == ["SUCCESS", "SUCCESS"], [
        r["state"] for r in runs
    ]
    # Both create tasks ran at the same time.
    a, b = (next(t for t in r["tasks"] if t["task_key"] == "create") for r in runs)
    assert min(a["end_time"], b["end_time"]) > max(a["start_time"], b["start_time"])
    for t in tables:
        client.post("/api/2.0/sql/statements", {"statement": f"DROP TABLE dev.default.{t}"})


def test_cancel_running_job(client: DuckbricksClient) -> None:
    root = f"/Users/test/{uuid.uuid4().hex[:8]}"
    upload(client, f"{root}/sleep.py", "import time\ntime.sleep(120)\n")
    job_id = client.post(
        "/api/2.2/jobs/create",
        {
            "name": "sleeper",
            "tasks": [
                {"task_key": "sleep", "spark_python_task": {"python_file": f"{root}/sleep.py"}},
                {
                    "task_key": "after",
                    "depends_on": [{"task_key": "sleep"}],
                    "spark_python_task": {"python_file": f"{root}/sleep.py"},
                },
            ],
        },
    )["job_id"]
    run_id = client.post("/api/2.2/jobs/run-now", {"job_id": job_id})["run_id"]
    import time

    for _ in range(60):
        run = client.get("/api/2.2/jobs/runs/get", run_id=run_id)
        if run["tasks"][0]["state"]["life_cycle_state"] == "RUNNING":
            break
        time.sleep(0.5)
    client.post("/api/2.2/jobs/runs/cancel", {"run_id": run_id})
    run = client.wait_for_run(run_id, timeout=60)
    assert run["state"]["result_state"] == "CANCELED"
    assert {t["state"]["result_state"] for t in run["tasks"]} == {"CANCELED"}


def test_unsupported_task_type_is_rejected(client: DuckbricksClient) -> None:
    from duckbricks.client import ApiCallError

    with pytest.raises(ApiCallError) as err:
        client.post(
            "/api/2.2/jobs/create",
            {
                "name": "jar",
                "tasks": [{"task_key": "t", "spark_jar_task": {"main_class_name": "x"}}],
            },
        )
    assert "spark_jar_task" in err.value.message


def test_unsupported_fields_warn(client: DuckbricksClient) -> None:
    created = client.post(
        "/api/2.2/jobs/create",
        {
            "name": "warn",
            "email_notifications": {"on_failure": ["a@b.c"]},
            "job_clusters": [
                {
                    "job_cluster_key": "c",
                    "new_cluster": {
                        "spark_version": "15.4.x",
                        "num_workers": 2,
                        "node_type_id": "Standard_DS3_v2",
                    },
                }
            ],
            "tasks": [
                {
                    "task_key": "t",
                    "job_cluster_key": "c",
                    "spark_python_task": {"python_file": "/Users/x/y.py"},
                }
            ],
        },
    )
    text = " ".join(created["warnings"])
    assert "email_notifications" in text and "num_workers" in text and "Standard_DS3_v2" in text
    client.post("/api/2.2/jobs/delete", {"job_id": created["job_id"]})
