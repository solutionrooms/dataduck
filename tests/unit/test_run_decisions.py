import pytest

from duckbricks.core.errors import InvalidParameter
from duckbricks.core.runs import decide, dependency_outcome, resolve_parameters

UPSTREAM = ("TERMINATED", "UPSTREAM_FAILED")
SKIP = ("SKIPPED", "EXCLUDED")


@pytest.mark.parametrize(
    ("run_if", "outcomes", "expected"),
    [
        ("ALL_SUCCESS", ["success", "success"], None),
        ("ALL_SUCCESS", ["success", "failed"], UPSTREAM),
        ("ALL_SUCCESS", ["success", "excluded"], SKIP),
        ("ALL_DONE", ["failed", "excluded"], None),
        ("NONE_FAILED", ["success", "excluded"], None),
        ("NONE_FAILED", ["failed"], UPSTREAM),
        ("AT_LEAST_ONE_SUCCESS", ["failed", "success"], None),
        ("AT_LEAST_ONE_SUCCESS", ["failed", "excluded"], UPSTREAM),
        ("AT_LEAST_ONE_SUCCESS", ["excluded"], SKIP),
        ("ALL_FAILED", ["failed", "failed"], None),
        ("ALL_FAILED", ["failed", "success"], SKIP),
        ("AT_LEAST_ONE_FAILED", ["success", "failed"], None),
        ("AT_LEAST_ONE_FAILED", ["success"], SKIP),
        ("ALL_SUCCESS", [], None),
    ],
)
def test_run_if(run_if: str, outcomes: list[str], expected: object) -> None:
    assert decide(run_if, outcomes) == expected


def row(result: str, outcome: str | None = None, disabled: bool = False) -> dict[str, object]:
    output = {"condition_outcome": outcome} if outcome else {}
    return {"result_state": result, "output": output, "task": {"disabled": disabled}}


def test_condition_branches() -> None:
    assert dependency_outcome(row("SUCCESS", "true"), {"task_key": "c", "outcome": "true"}) == (
        "success"
    )
    assert dependency_outcome(row("SUCCESS", "false"), {"task_key": "c", "outcome": "true"}) == (
        "excluded"
    )
    assert dependency_outcome(row("UPSTREAM_FAILED"), {"task_key": "c"}) == "failed"
    assert dependency_outcome(row("EXCLUDED", disabled=True), {"task_key": "c"}) == "success"


def test_job_parameters() -> None:
    settings = {"parameters": [{"name": "a", "default": "1"}, {"name": "b", "default": "x"}]}
    assert resolve_parameters(settings, {"a": 5}) == {"a": "5", "b": "x"}
    with pytest.raises(InvalidParameter, match="not defined"):
        resolve_parameters(settings, {"c": 1})
