from __future__ import annotations

import json

import pytest

from student_schedule_worker.config.models import AppConfig, ScheduleConfig, ScheduleMode, TimeWindow
from student_schedule_worker.config.store import ConfigStore


def test_config_round_trip_contains_no_token(tmp_path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    config = AppConfig(
        server_url="https://schedule.example",
        worker_id="f5b8a635-18ae-4958-a0fb-5877e5248f05",
        schedule=ScheduleConfig(
            mode=ScheduleMode.CUSTOM,
            windows=(TimeWindow("10:00", "16:00"), TimeWindow("22:00", "06:00")),
        ),
        autostart=True,
    )
    store.save(config)

    raw = path.read_text(encoding="utf-8")
    assert "token" not in raw.lower()
    assert store.load() == config
    assert json.loads(raw)["schedule"]["windows"][1] == {"start": "22:00", "end": "06:00"}


def test_missing_config_returns_none(tmp_path) -> None:
    assert ConfigStore(tmp_path / "missing.json").load() is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"server_url": 123, "worker_id": "f5b8a635-18ae-4958-a0fb-5877e5248f05"},
        {"server_url": "https://schedule.example", "worker_id": []},
        {
            "server_url": "https://schedule.example",
            "worker_id": "f5b8a635-18ae-4958-a0fb-5877e5248f05",
            "schema_version": "1",
        },
        {
            "server_url": "https://schedule.example",
            "worker_id": "f5b8a635-18ae-4958-a0fb-5877e5248f05",
            "schedule": {"mode": []},
        },
    ],
)
def test_malformed_config_is_reported_as_value_error(tmp_path, payload) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        ConfigStore(path).load()


def test_invalid_stored_server_origin_is_rejected(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "server_url": "http://public.example",
                "worker_id": "f5b8a635-18ae-4958-a0fb-5877e5248f05",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="HTTP is allowed only"):
        ConfigStore(path).load()


def test_config_reset_removes_only_config_file(tmp_path) -> None:
    path = tmp_path / "config.json"
    sibling = tmp_path / "keep.txt"
    path.write_text("{}", encoding="utf-8")
    sibling.write_text("keep", encoding="utf-8")
    ConfigStore(path).delete()
    assert not path.exists()
    assert sibling.read_text(encoding="utf-8") == "keep"
