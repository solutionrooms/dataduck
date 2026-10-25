"""Convert Databricks Quartz cron expressions to five-field cron (croniter).

Quartz fields: seconds minutes hours day-of-month month day-of-week [year].
Rules (SPEC section 7, ADR 0001 #6):
- seconds must be 0; a year field must be `*`.
- `?` becomes `*`.
- day-of-week numbers are Quartz 1-7 (SUN=1) and become cron 0-6 (SUN=0); names pass through.
- `L`, `W`, `#` and `LW` are Quartz-only and rejected.
- `a/b` steps become `a-max/b`, which croniter understands.
"""

from __future__ import annotations

import re
from datetime import datetime

from croniter import croniter


class QuartzError(ValueError):
    pass


_MAX = {1: 59, 2: 23, 3: 31, 4: 12, 5: 7}
_DOW_NUMBER = re.compile(r"\d+")


def _steps(field: str, index: int) -> str:
    """Quartz `5/15` means 'from 5 every 15'; cron needs an explicit range."""
    parts = []
    for part in field.split(","):
        if "/" in part:
            start, step = part.split("/", 1)
            if start in ("*", "?"):
                start = "0" if index in (1, 2) else "1"
            if "-" not in start:
                start = f"{start}-{_MAX[index]}"
            part = f"{start}/{step}"
        parts.append(part)
    return ",".join(parts)


def _dow(field: str) -> str:
    def shift(match: re.Match[str]) -> str:
        value = int(match.group())
        if not 1 <= value <= 7:
            raise QuartzError(f"day-of-week {value} is out of range (Quartz uses 1-7, SUN=1)")
        return str(value - 1)

    out = []
    for part in field.split(","):
        base, slash, step = part.partition("/")
        base = _DOW_NUMBER.sub(shift, base)
        out.append(base + (slash + step if slash else ""))
    return ",".join(out)


def quartz_to_cron(expression: str) -> str:
    fields = expression.split()
    if len(fields) not in (6, 7):
        raise QuartzError(
            f"Quartz expression '{expression}' must have 6 or 7 fields "
            "(seconds minutes hours day-of-month month day-of-week [year])"
        )
    if len(fields) == 7:
        if fields[6] != "*":
            raise QuartzError(f"Quartz year field '{fields[6]}' is not supported; use '*'")
        fields = fields[:6]
    seconds, minutes, hours, dom, month, dow = fields
    if seconds not in ("0", "00"):
        raise QuartzError(
            f"Quartz seconds field '{seconds}' is not supported; schedules run on whole minutes"
        )
    for name, value in (("day-of-month", dom), ("day-of-week", dow)):
        if re.search(r"[LW#]", value.upper().replace("WED", "")):
            raise QuartzError(
                f"Quartz-only syntax in {name} ('{value}'): L, W and # are not supported"
            )
    dom = "*" if dom == "?" else _steps(dom, 3)
    dow = "*" if dow == "?" else _dow(_steps(dow, 5))
    cron = " ".join([_steps(minutes, 1), _steps(hours, 2), dom, month.replace("?", "*"), dow])
    if not croniter.is_valid(cron):
        raise QuartzError(f"Quartz expression '{expression}' converts to invalid cron '{cron}'")
    return cron


def next_fire(expression: str, after: datetime) -> datetime:
    """Next fire time strictly after `after` (which carries the schedule's time zone)."""
    nxt: datetime = croniter(quartz_to_cron(expression), after).get_next(datetime)
    return nxt
