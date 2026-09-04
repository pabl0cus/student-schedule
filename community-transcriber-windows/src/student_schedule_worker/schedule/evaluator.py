from __future__ import annotations

from datetime import datetime, timedelta

from ..config.models import ScheduleConfig, ScheduleMode, TimeWindow


def _window_contains(window: TimeWindow, minute: int) -> bool:
    start = window.start_minute
    end = window.end_minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def is_allowed(schedule: ScheduleConfig, moment: datetime | None = None) -> bool:
    if schedule.mode is ScheduleMode.ALWAYS:
        return True
    if schedule.mode is ScheduleMode.NEVER:
        return False

    current = moment or datetime.now().astimezone()
    minute = current.hour * 60 + current.minute
    return any(_window_contains(window, minute) for window in schedule.windows)


def seconds_until_recheck(schedule: ScheduleConfig, moment: datetime | None = None) -> float:
    """Return a bounded delay; frequent reevaluation also handles sleep and clock changes."""

    if schedule.mode is ScheduleMode.NEVER:
        return 60.0
    if schedule.mode is ScheduleMode.ALWAYS:
        return 30.0

    current = moment or datetime.now().astimezone()
    current_allowed = is_allowed(schedule, current)
    candidates: list[float] = []
    midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    for day_offset in range(3):
        day = midnight + timedelta(days=day_offset)
        for window in schedule.windows:
            for minute in (window.start_minute, window.end_minute):
                boundary = day + timedelta(minutes=minute)
                delta = (boundary - current).total_seconds()
                if delta > 0:
                    candidates.append(delta)
    if not candidates:
        return 30.0
    # Never sleep longer than a minute: resume/time-zone changes stay responsive.
    return max(0.25, min(min(candidates), 60.0 if not current_allowed else 30.0))

