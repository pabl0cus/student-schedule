from __future__ import annotations

from datetime import datetime

import pytest

from student_schedule_worker.config.models import ScheduleConfig, ScheduleMode, TimeWindow
from student_schedule_worker.schedule.evaluator import is_allowed


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 9, 4, hour, minute)


def test_always_and_never_modes() -> None:
    assert is_allowed(ScheduleConfig(mode=ScheduleMode.ALWAYS), at(3, 15))
    assert not is_allowed(ScheduleConfig(mode=ScheduleMode.NEVER), at(3, 15))


def test_daytime_window_is_start_inclusive_and_end_exclusive() -> None:
    schedule = ScheduleConfig(mode=ScheduleMode.CUSTOM, windows=(TimeWindow("10:00", "16:00"),))
    assert not is_allowed(schedule, at(9, 59))
    assert is_allowed(schedule, at(10, 0))
    assert is_allowed(schedule, at(15, 59))
    assert not is_allowed(schedule, at(16, 0))


def test_overnight_window_crosses_midnight() -> None:
    schedule = ScheduleConfig(mode=ScheduleMode.CUSTOM, windows=(TimeWindow("22:00", "06:00"),))
    assert not is_allowed(schedule, at(21, 59))
    assert is_allowed(schedule, at(22, 0))
    assert is_allowed(schedule, at(0, 0))
    assert is_allowed(schedule, at(5, 59))
    assert not is_allowed(schedule, at(6, 0))


def test_multiple_daily_windows() -> None:
    schedule = ScheduleConfig(
        mode=ScheduleMode.CUSTOM,
        windows=(TimeWindow("10:00", "16:00"), TimeWindow("22:00", "06:00")),
    )
    assert is_allowed(schedule, at(12, 0))
    assert not is_allowed(schedule, at(18, 0))
    assert is_allowed(schedule, at(23, 0))


@pytest.mark.parametrize("start,end", [("10:00", "10:00"), ("1:00", "02:00"), ("24:00", "01:00")])
def test_invalid_windows_are_rejected(start: str, end: str) -> None:
    with pytest.raises(ValueError):
        TimeWindow(start, end)


def test_custom_mode_needs_a_window() -> None:
    with pytest.raises(ValueError):
        ScheduleConfig(mode=ScheduleMode.CUSTOM)

