from __future__ import annotations

import logging
from datetime import UTC, datetime
from threading import Event, Lock, Thread

from ..api.dto import JobLease
from ..api.errors import LeaseLostError, UnauthorizedError
from ..constants import HEARTBEAT_SECONDS

logger = logging.getLogger(__name__)


class LeaseHeartbeat:
    def __init__(self, api: object, lease: JobLease, *, interval: float = HEARTBEAT_SECONDS):
        self.api = api
        self.lease = lease
        self.interval = interval
        self.lost = Event()
        self._stop = Event()
        self._lock = Lock()
        self._progress = 5
        self._thread: Thread | None = None
        self._lease_expires_at = lease.lease_expires_at.astimezone(UTC)

    def set_stage(self, stage: str) -> None:
        progress_by_stage = {"download": 5, "transcribe": 50, "upload": 90}
        with self._lock:
            self._progress = progress_by_stage.get(stage, self._progress)

    def start(self) -> None:
        self._thread = Thread(target=self._run, name="lease-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(self.interval + 1.0, 2.0))

    def _run(self) -> None:
        while True:
            now = datetime.now(UTC)
            seconds_remaining = (self._lease_expires_at - now).total_seconds()
            if seconds_remaining <= 0:
                self.lost.set()
                return
            # For short server TTLs, renew well before the configured normal interval.
            wait_seconds = min(self.interval, max(seconds_remaining / 3.0, 0.25))
            if self._stop.wait(wait_seconds):
                return
            with self._lock:
                progress = self._progress
            try:
                renewed_until = self.api.heartbeat(self.lease, progress=progress)
                if not isinstance(renewed_until, datetime) or renewed_until.tzinfo is None:
                    raise ValueError("heartbeat did not return a timezone-aware lease expiry")
                self._lease_expires_at = renewed_until.astimezone(UTC)
            except (LeaseLostError, UnauthorizedError):
                self.lost.set()
                return
            except Exception:
                logger.warning("Lease heartbeat failed", exc_info=True)
                if datetime.now(UTC) >= self._lease_expires_at:
                    self.lost.set()
                    return
