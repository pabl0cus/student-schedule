from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from threading import Event, Lock, Thread

from ..api.errors import UnauthorizedError
from ..config.models import AppConfig
from ..schedule.evaluator import is_allowed, seconds_until_recheck
from .job_runner import JobRunner
from .state import WorkerState, WorkerStatus

logger = logging.getLogger(__name__)


class WorkerController:
    def __init__(
        self,
        *,
        config: AppConfig,
        api: object,
        engine: object,
        jobs_dir: Path,
        status_callback: Callable[[WorkerStatus], None] = lambda _status: None,
        poll_seconds: float = 15.0,
        runner_factory: Callable[..., JobRunner] = JobRunner,
    ):
        self.api = api
        self.engine = engine
        self.jobs_dir = jobs_dir
        self.status_callback = status_callback
        self.poll_seconds = poll_seconds
        self.runner_factory = runner_factory
        self._config = config
        self._config_lock = Lock()
        self._stop = Event()
        self._wake = Event()
        self._paused = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = Thread(target=self._run, name="community-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        self.status_callback(WorkerStatus(WorkerState.STOPPING))
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def update_config(self, config: AppConfig) -> None:
        with self._config_lock:
            self._config = config
        self._wake.set()

    def pause(self) -> None:
        self._paused.set()
        self._wake.set()

    def resume(self) -> None:
        self._paused.clear()
        self._wake.set()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _snapshot_config(self) -> AppConfig:
        with self._config_lock:
            return self._config

    def _rendering_allowed(self) -> bool:
        return not self._stop.is_set() and not self._paused.is_set() and is_allowed(self._snapshot_config().schedule)

    def _wait(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    def _run(self) -> None:
        self.status_callback(WorkerStatus(WorkerState.STARTING))
        runner = self.runner_factory(
            api=self.api,
            engine=self.engine,
            jobs_dir=self.jobs_dir,
            status_callback=self.status_callback,
        )
        model_ready = False
        while not self._stop.is_set():
            config = self._snapshot_config()
            if self._paused.is_set():
                self.status_callback(WorkerStatus(WorkerState.PAUSED))
                self._wait(30.0)
                continue
            if not is_allowed(config.schedule):
                self.status_callback(WorkerStatus(WorkerState.WAITING_WINDOW))
                self._wait(seconds_until_recheck(config.schedule))
                continue

            try:
                if not model_ready:
                    self.status_callback(WorkerStatus(WorkerState.LOADING_MODEL))
                    self.engine.warmup()
                    model_ready = True
                    if not self._rendering_allowed():
                        continue

                self.status_callback(WorkerStatus(WorkerState.IDLE))
                lease = self.api.claim()
                if lease is None:
                    self._wait(getattr(self.api, "claim_retry_after_seconds", self.poll_seconds))
                    continue
                runner.run(lease, stop_event=self._stop, rendering_allowed=self._rendering_allowed)
            except UnauthorizedError:
                self.status_callback(WorkerStatus(WorkerState.UNAUTHORIZED))
                self._wait(60.0)
            except Exception as exc:
                logger.exception("Community worker loop failed")
                self.status_callback(WorkerStatus(WorkerState.BACKOFF, str(exc)[:200]))
                self._wait(30.0)

        self.status_callback(WorkerStatus(WorkerState.STOPPED))
