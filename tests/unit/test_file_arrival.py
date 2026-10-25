"""File arrival trigger decisions (no database: the state writes and trigger are patched)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from duckbricks.scheduler import file_arrival

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
URL = "s3://landing/x/"


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    recorded: dict[str, list[Any]] = {"saved": [], "fired": []}
    monkeypatch.setattr(
        file_arrival,
        "_save",
        lambda conn, job_id, state, fired: recorded["saved"].append((state, fired)),
    )
    monkeypatch.setattr(
        file_arrival.runs,
        "trigger_job",
        lambda conn, job_id, trigger: (
            recorded["fired"].append(trigger) or SimpleNamespace(run_id=1, skipped=False)
        ),
    )
    return recorded


def trigger(**extra: Any) -> dict[str, Any]:
    return {"file_arrival": {"url": URL, **extra}}


def test_first_poll_records_baseline_without_firing(calls: dict[str, list[Any]]) -> None:
    fired = file_arrival.check_trigger(None, 1, trigger(), None, None, ["a", "b"], NOW)  # type: ignore[arg-type]
    assert not fired and not calls["fired"]
    assert calls["saved"][-1][0]["seen"] == ["a", "b"]


def test_new_file_fires(calls: dict[str, list[Any]]) -> None:
    state = {"url": URL, "seen": ["a"], "pending": []}
    assert file_arrival.check_trigger(None, 1, trigger(), state, None, ["a", "b"], NOW)  # type: ignore[arg-type]
    assert calls["fired"] == ["FILE_ARRIVAL"]
    saved, fired_at = calls["saved"][-1]
    assert saved["seen"] == ["a", "b"] and saved["pending"] == [] and fired_at == NOW


def test_no_new_files_does_not_fire(calls: dict[str, list[Any]]) -> None:
    state = {"url": URL, "seen": ["a"], "pending": []}
    assert not file_arrival.check_trigger(None, 1, trigger(), state, None, ["a"], NOW)  # type: ignore[arg-type]
    assert not calls["fired"]


def test_min_time_between_triggers_defers(calls: dict[str, list[Any]]) -> None:
    state = {"url": URL, "seen": ["a"], "pending": []}
    last = NOW - timedelta(seconds=30)
    spec = trigger(min_time_between_triggers_seconds=60)
    assert not file_arrival.check_trigger(None, 1, spec, state, last, ["a", "b"], NOW)  # type: ignore[arg-type]
    pending_state = calls["saved"][-1][0]
    assert pending_state["pending"] == ["b"]
    later = NOW + timedelta(seconds=40)
    assert file_arrival.check_trigger(None, 1, spec, pending_state, last, ["a", "b"], later)  # type: ignore[arg-type]


def test_wait_after_last_change(calls: dict[str, list[Any]]) -> None:
    spec = trigger(wait_after_last_change_seconds=120)
    state = {"url": URL, "seen": ["a"], "pending": []}
    assert not file_arrival.check_trigger(None, 1, spec, state, None, ["a", "b"], NOW)  # type: ignore[arg-type]
    state = calls["saved"][-1][0]
    assert not file_arrival.check_trigger(
        None,
        1,
        spec,
        state,
        None,
        ["a", "b"],
        NOW + timedelta(seconds=60),  # type: ignore[arg-type]
    )
    state = calls["saved"][-1][0]
    assert file_arrival.check_trigger(
        None,
        1,
        spec,
        state,
        None,
        ["a", "b"],
        NOW + timedelta(seconds=121),  # type: ignore[arg-type]
    )


def test_changed_url_resets_baseline(calls: dict[str, list[Any]]) -> None:
    state = {"url": "s3://old/", "seen": [], "pending": []}
    assert not file_arrival.check_trigger(None, 1, trigger(), state, None, ["a"], NOW)  # type: ignore[arg-type]
    assert calls["saved"][-1][0] == {"url": URL, "seen": ["a"], "pending": []}
