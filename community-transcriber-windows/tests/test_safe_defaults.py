from __future__ import annotations

from student_schedule_worker.config.models import AppConfig, ScheduleMode


def test_new_worker_is_paused_until_user_selects_a_schedule() -> None:
    config = AppConfig(
        server_url="https://schedule.example",
        worker_id="894d99d7-d44c-40cf-a491-068567ac5161",
    )
    assert config.schedule.mode is ScheduleMode.NEVER

