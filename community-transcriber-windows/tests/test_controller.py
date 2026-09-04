from __future__ import annotations

from pathlib import Path
from threading import Event

from student_schedule_worker.config.models import AppConfig, ScheduleConfig, ScheduleMode
from student_schedule_worker.runtime.controller import WorkerController


class FakeEngine:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def warmup(self) -> None:
        self.calls.append("warmup")


class FakeApi:
    def __init__(self, calls: list[str], claimed: Event):
        self.calls = calls
        self.claimed = claimed

    def claim(self):
        self.calls.append("claim")
        self.claimed.set()
        return None


def config(mode: ScheduleMode) -> AppConfig:
    return AppConfig(
        server_url="https://schedule.example",
        worker_id="11c7ddc0-7a6d-4f8a-a547-92bbef795a5c",
        schedule=ScheduleConfig(mode=mode),
    )


def test_model_is_warmed_before_first_claim(tmp_path: Path) -> None:
    calls: list[str] = []
    claimed = Event()
    controller = WorkerController(
        config=config(ScheduleMode.ALWAYS),
        api=FakeApi(calls, claimed),
        engine=FakeEngine(calls),
        jobs_dir=tmp_path,
        poll_seconds=30,
    )
    controller.start()
    assert claimed.wait(1)
    controller.stop()
    assert calls[:2] == ["warmup", "claim"]


def test_never_mode_does_not_warm_or_claim(tmp_path: Path) -> None:
    calls: list[str] = []
    claimed = Event()
    controller = WorkerController(
        config=config(ScheduleMode.NEVER),
        api=FakeApi(calls, claimed),
        engine=FakeEngine(calls),
        jobs_dir=tmp_path,
    )
    controller.start()
    assert not claimed.wait(0.05)
    controller.stop()
    assert calls == []

