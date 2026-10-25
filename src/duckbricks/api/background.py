"""Extra scheduler-loop steps that live outside the scheduler package."""

from __future__ import annotations

from duckbricks.scheduler.loop import SchedulerLoop


def register_steps(scheduler: SchedulerLoop) -> None:
    """Add file-arrival polling, mirror scheduling and warehouse auto-stop."""
    try:
        from duckbricks.scheduler.file_arrival import poll_file_arrivals
    except ImportError:
        pass
    else:
        scheduler.extra_steps.append(poll_file_arrivals)
    try:
        from duckbricks.federation.mirror import schedule_syncs
    except ImportError:
        pass
    else:
        scheduler.extra_steps.append(schedule_syncs)
    try:
        from duckbricks.warehouses.lifecycle import auto_stop_idle
    except ImportError:
        pass
    else:
        scheduler.extra_steps.append(auto_stop_idle)
