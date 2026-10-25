from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from duckbricks.scheduler.quartz import QuartzError, next_fire, quartz_to_cron


@pytest.mark.parametrize(
    ("quartz", "cron"),
    [
        ("0 0 6 * * ?", "0 6 * * *"),
        ("0 30 9 ? * MON-FRI", "30 9 * * MON-FRI"),
        ("0 0 12 ? * 2", "0 12 * * 1"),
        ("0 0 12 ? * 1,7", "0 12 * * 0,6"),
        ("0 0/15 * * * ?", "0-59/15 * * * *"),
        ("0 0 0 1 * ? *", "0 0 1 * *"),
        ("00 5 4 ? * 2-6", "5 4 * * 1-5"),
    ],
)
def test_conversion(quartz: str, cron: str) -> None:
    assert quartz_to_cron(quartz) == cron


@pytest.mark.parametrize(
    ("quartz", "message"),
    [
        ("30 0 6 * * ?", "seconds"),
        ("0 0 6 L * ?", "Quartz-only"),
        ("0 0 6 ? * 6#3", "Quartz-only"),
        ("0 0 6 15W * ?", "Quartz-only"),
        ("0 0 6 * * ? 2027", "year"),
        ("0 6 * * *", "6 or 7 fields"),
        ("0 0 6 ? * 8", "out of range"),
    ],
)
def test_rejections(quartz: str, message: str) -> None:
    with pytest.raises(QuartzError, match=message):
        quartz_to_cron(quartz)


def test_next_fire_respects_time_zone() -> None:
    london = ZoneInfo("Europe/London")
    after = datetime(2026, 10, 24, 7, 0, tzinfo=london)
    nxt = next_fire("0 0 6 * * ?", after)
    assert nxt == datetime(2026, 10, 25, 6, 0, tzinfo=london)
    assert nxt.utcoffset().total_seconds() == 0  # BST ended overnight
