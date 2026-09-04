from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from threading import Event
from typing import Any
from uuid import uuid4

from httpx import TransportError

from .. import __version__
from ..api.dto import JobLease
from ..api.errors import (
    ApiError,
    LeaseLostError,
    MediaIntegrityError,
    OperationCancelled,
    UnauthorizedError,
)
from ..constants import PROTOCOL_VERSION, WHISPER_MODEL
from ..engine.whisper import EngineResult, TranscriptionCancelled
from .heartbeat import LeaseHeartbeat
from .state import WorkerState, WorkerStatus

logger = logging.getLogger(__name__)


class JobOutcome(StrEnum):
    COMPLETED = "completed"
    ABORTED = "aborted"
    LEASE_LOST = "lease_lost"
    FAILED = "failed"


class JobRunner:
    def __init__(
        self,
        *,
        api: Any,
        engine: Any,
        jobs_dir: Path,
        status_callback: Callable[[WorkerStatus], None],
        heartbeat_factory: Callable[..., LeaseHeartbeat] = LeaseHeartbeat,
        upload_retry_seconds: float = 5.0,
        max_upload_attempts: int = 5,
    ):
        self.api = api
        self.engine = engine
        self.jobs_dir = jobs_dir
        self.status_callback = status_callback
        self.heartbeat_factory = heartbeat_factory
        self.upload_retry_seconds = upload_retry_seconds
        if max_upload_attempts < 1:
            raise ValueError("max_upload_attempts must be positive")
        self.max_upload_attempts = max_upload_attempts

    @staticmethod
    def _transient_upload_error(exc: Exception) -> bool:
        if isinstance(exc, ApiError):
            return exc.status_code in {408, 425, 429} or bool(exc.status_code and exc.status_code >= 500)
        return isinstance(exc, (ConnectionError, OSError, TimeoutError, TransportError))

    def _release(self, lease: JobLease, *, code: str, message: str | None = None) -> None:
        try:
            self.api.release(lease, code=code, message=message)
        except Exception:
            logger.warning("Could not release transcription lease", exc_info=True)

    @staticmethod
    def _payload(result: EngineResult, submission_id: str, engine_version: str) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "submission_id": submission_id,
            "engine": "faster-whisper",
            "model": WHISPER_MODEL,
            "engine_version": engine_version,
            "client_version": __version__,
            "transcript": result.transcript,
            "duration_seconds": result.duration_seconds,
            "detected_language": result.detected_language,
            "language_probability": result.language_probability,
        }

    def run(
        self,
        lease: JobLease,
        *,
        stop_event: Event,
        rendering_allowed: Callable[[], bool],
    ) -> JobOutcome:
        job_dir = self.jobs_dir / lease.job_id
        media_path = job_dir / "source-media.ogg"
        job_dir.mkdir(parents=True, exist_ok=True)
        heartbeat = self.heartbeat_factory(self.api, lease)
        heartbeat.start()
        result_exists = False
        stage = "download"

        def may_render() -> bool:
            return not stop_event.is_set() and not heartbeat.lost.is_set() and rendering_allowed()

        try:
            self.status_callback(WorkerStatus(WorkerState.DOWNLOADING))
            self.api.download_media(lease, media_path, should_continue=may_render)
            if not may_render():
                raise OperationCancelled("rendering window closed")

            heartbeat.set_stage("transcribe")
            stage = "transcribe"
            self.status_callback(WorkerStatus(WorkerState.TRANSCRIBING))
            result = self.engine.transcribe(media_path, lease.transcription, should_continue=may_render)
            result_exists = True

            # Once computation is complete, schedule/stop changes must not discard useful work.
            heartbeat.set_stage("upload")
            stage = "upload"
            self.status_callback(WorkerStatus(WorkerState.UPLOADING))
            submission_id = str(uuid4())
            payload = self._payload(result, submission_id, self.engine.engine_version)
            upload_attempt = 0
            while not heartbeat.lost.is_set() and upload_attempt < self.max_upload_attempts:
                upload_attempt += 1
                try:
                    self.api.submit_result(lease, payload)
                    return JobOutcome.COMPLETED
                except (LeaseLostError, UnauthorizedError):
                    return JobOutcome.LEASE_LOST
                except Exception as exc:
                    if not self._transient_upload_error(exc):
                        self._release(
                            lease,
                            code="internal_error",
                            message="Server rejected the completed transcription result",
                        )
                        return JobOutcome.FAILED
                    logger.warning("Result upload failed; retrying with the same submission id", exc_info=True)
                    retry_delay = min(self.upload_retry_seconds * (2 ** (upload_attempt - 1)), 30.0)
                    if stop_event.wait(retry_delay):
                        self._release(lease, code="shutdown")
                        return JobOutcome.ABORTED
            if not heartbeat.lost.is_set():
                self._release(
                    lease,
                    code="internal_error",
                    message="Could not upload the completed transcription result",
                )
                return JobOutcome.FAILED
            return JobOutcome.LEASE_LOST
        except MediaIntegrityError:
            if not heartbeat.lost.is_set():
                self._release(
                    lease,
                    code="invalid_media",
                    message="Downloaded audio failed its SHA-256 integrity check",
                )
            return JobOutcome.FAILED
        except (OperationCancelled, TranscriptionCancelled):
            if not heartbeat.lost.is_set():
                code = "shutdown" if stop_event.is_set() else "outside_schedule"
                self._release(lease, code=code)
            return JobOutcome.ABORTED
        except (LeaseLostError, UnauthorizedError):
            return JobOutcome.LEASE_LOST
        except Exception:
            logger.exception("Job %s failed on the worker", lease.job_id)
            if not result_exists and not heartbeat.lost.is_set():
                code = {
                    "download": "download_failed",
                    "transcribe": "transcription_failed",
                }.get(stage, "internal_error")
                self._release(lease, code=code, message="Worker could not process this job")
            return JobOutcome.FAILED
        finally:
            heartbeat.stop()
            shutil.rmtree(job_dir, ignore_errors=True)
