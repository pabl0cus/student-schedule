from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WorkerState(StrEnum):
    STARTING = "starting"
    PAUSED = "paused"
    WAITING_WINDOW = "waiting_window"
    LOADING_MODEL = "loading_model"
    IDLE = "idle"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    UPLOADING = "uploading"
    BACKOFF = "backoff"
    UNAUTHORIZED = "unauthorized"
    ERROR = "error"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class WorkerStatus:
    state: WorkerState
    detail: str = ""


STATUS_LABELS_UK: dict[WorkerState, str] = {
    WorkerState.STARTING: "Запуск",
    WorkerState.PAUSED: "Призупинено",
    WorkerState.WAITING_WINDOW: "Очікування дозволеного часу",
    WorkerState.LOADING_MODEL: "Підготовка Whisper large-v3",
    WorkerState.IDLE: "Очікування запису",
    WorkerState.DOWNLOADING: "Завантаження аудіо",
    WorkerState.TRANSCRIBING: "Транскрипція",
    WorkerState.UPLOADING: "Надсилання результату",
    WorkerState.BACKOFF: "Сервер тимчасово недоступний",
    WorkerState.UNAUTHORIZED: "Токен відхилено",
    WorkerState.ERROR: "Помилка",
    WorkerState.STOPPING: "Завершення роботи",
    WorkerState.STOPPED: "Зупинено",
}

