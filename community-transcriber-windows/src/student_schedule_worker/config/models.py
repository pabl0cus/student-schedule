from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class ScheduleMode(StrEnum):
    ALWAYS = "always"
    NEVER = "never"
    CUSTOM = "custom"


def parse_clock(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 2 or any(not part.isdigit() for part in parts):
        raise ValueError("time must use the HH:MM format")
    hours, minutes = (int(part) for part in parts)
    if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
        raise ValueError("time is outside the 00:00-23:59 range")
    if value != f"{hours:02d}:{minutes:02d}":
        raise ValueError("time must use zero-padded HH:MM")
    return hours * 60 + minutes


@dataclass(frozen=True, slots=True)
class TimeWindow:
    start: str
    end: str

    def __post_init__(self) -> None:
        start_minute = parse_clock(self.start)
        end_minute = parse_clock(self.end)
        if start_minute == end_minute:
            raise ValueError("start and end must differ; use always for a full day")

    @property
    def start_minute(self) -> int:
        return parse_clock(self.start)

    @property
    def end_minute(self) -> int:
        return parse_clock(self.end)

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start, "end": self.end}

    @classmethod
    def from_dict(cls, value: object) -> TimeWindow:
        if not isinstance(value, dict):
            raise ValueError("schedule window must be an object")
        if set(value) != {"start", "end"}:
            raise ValueError("schedule window contains unknown fields")
        return cls(start=str(value["start"]), end=str(value["end"]))


@dataclass(frozen=True, slots=True)
class ScheduleConfig:
    mode: ScheduleMode = ScheduleMode.NEVER
    windows: tuple[TimeWindow, ...] = ()

    def __post_init__(self) -> None:
        if self.mode is ScheduleMode.CUSTOM and not self.windows:
            raise ValueError("custom schedule needs at least one time window")

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode.value, "windows": [window.to_dict() for window in self.windows]}

    @classmethod
    def from_dict(cls, value: object) -> ScheduleConfig:
        if not isinstance(value, dict):
            raise ValueError("schedule must be an object")
        if set(value) - {"mode", "windows"}:
            raise ValueError("schedule contains unknown fields")
        try:
            mode = ScheduleMode(value.get("mode", ScheduleMode.NEVER.value))
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown schedule mode") from exc
        raw_windows = value.get("windows", [])
        if not isinstance(raw_windows, list):
            raise ValueError("schedule windows must be a list")
        return cls(mode=mode, windows=tuple(TimeWindow.from_dict(item) for item in raw_windows))


@dataclass(frozen=True, slots=True)
class AppConfig:
    server_url: str
    worker_id: str = field(default_factory=lambda: str(uuid4()))
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    autostart: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        from ..api.security import normalized_server_origin

        if not isinstance(self.server_url, str):
            raise ValueError("server_url must be a string")
        object.__setattr__(self, "server_url", normalized_server_origin(self.server_url))
        try:
            UUID(self.worker_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("worker_id must be a UUID") from exc
        if self.schema_version != 1:
            raise ValueError("unsupported config schema version")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "server_url": self.server_url,
            "worker_id": self.worker_id,
            "schedule": self.schedule.to_dict(),
            "autostart": self.autostart,
        }

    @classmethod
    def from_dict(cls, value: object) -> AppConfig:
        if not isinstance(value, dict):
            raise ValueError("config must be an object")
        allowed = {"schema_version", "server_url", "worker_id", "schedule", "autostart"}
        if set(value) - allowed:
            raise ValueError("config contains unknown fields")
        if "server_url" not in value or "worker_id" not in value:
            raise ValueError("config must contain server_url and worker_id")
        server_url = value["server_url"]
        worker_id = value["worker_id"]
        schema_version = value.get("schema_version", 1)
        if not isinstance(server_url, str):
            raise ValueError("server_url must be a string")
        if not isinstance(worker_id, str):
            raise ValueError("worker_id must be a string")
        if type(schema_version) is not int:
            raise ValueError("schema_version must be an integer")
        autostart = value.get("autostart", False)
        if type(autostart) is not bool:
            raise ValueError("autostart must be a boolean")
        return cls(
            schema_version=schema_version,
            server_url=server_url,
            worker_id=worker_id,
            schedule=ScheduleConfig.from_dict(value.get("schedule", {})),
            autostart=autostart,
        )
