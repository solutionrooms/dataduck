"""Databricks dynamic value references: `{{job.parameters.x}}`, `{{tasks.t.values.k}}`, ...

Resolved in task settings just before a task runs. An unknown reference is left as written,
which matches Databricks' behaviour for references it does not recognise.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

REFERENCE = re.compile(r"\{\{\s*([A-Za-z0-9_.\-\[\]]+)\s*\}\}")


@dataclass
class Context:
    job_id: int | None
    job_name: str
    run_id: int
    task_key: str
    task_run_id: int
    attempt: int
    start_time: datetime
    trigger: str
    parameters: dict[str, str]
    task_values: dict[str, dict[str, Any]] = field(default_factory=dict)
    task_states: dict[str, dict[str, Any]] = field(default_factory=dict)

    def lookup(self, ref: str) -> str | None:
        simple = {
            "job.id": self.job_id,
            "job.name": self.job_name,
            "job.run_id": self.run_id,
            "job.repair_count": 0,
            "job.trigger.type": {"ONE_TIME": "manual", "PERIODIC": "periodic"}.get(
                self.trigger, self.trigger.lower()
            ),
            "task.name": self.task_key,
            "task.run_id": self.task_run_id,
            "task.execution_count": self.attempt + 1,
            "job.start_time.iso_date": self.start_time.date().isoformat(),
            "job.start_time.iso_datetime": self.start_time.isoformat(),
            "job.start_time.timestamp_ms": int(self.start_time.timestamp() * 1000),
            "job.start_time.year": self.start_time.year,
            "job.start_time.month": self.start_time.month,
            "job.start_time.day": self.start_time.day,
            "job.start_time.hour": self.start_time.hour,
            "job.start_time.minute": self.start_time.minute,
            "job.start_time.second": self.start_time.second,
            "job.start_time.iso_weekday": self.start_time.isoweekday(),
            # Deprecated Databricks variable names.
            "job_id": self.job_id,
            "run_id": self.task_run_id,
            "parent_run_id": self.run_id,
            "task_key": self.task_key,
            "start_date": self.start_time.date().isoformat(),
        }
        if ref in simple:
            value = simple[ref]
            return "" if value is None else str(value)
        if ref.startswith("job.parameters."):
            return self.parameters.get(ref.removeprefix("job.parameters."))
        if ref.startswith("tasks."):
            parts = ref.split(".")
            if len(parts) >= 4 and parts[2] == "values":
                values = self.task_values.get(parts[1], {})
                if parts[3] in values:
                    value = values[parts[3]]
                    return value if isinstance(value, str) else json.dumps(value)
                return None
            if len(parts) == 3 and parts[2] in ("result_state", "error_code", "run_id"):
                state = self.task_states.get(parts[1], {})
                return None if state.get(parts[2]) is None else str(state[parts[2]])
        return None


def resolve(value: Any, ctx: Context) -> Any:
    """Recursively resolve references inside strings, lists and dicts."""
    if isinstance(value, str):

        def sub(match: re.Match[str]) -> str:
            found = ctx.lookup(match.group(1))
            return match.group(0) if found is None else found

        return REFERENCE.sub(sub, value)
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    return value
