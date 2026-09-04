from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Event, Lock, Thread
from typing import BinaryIO, Callable

from .database import RecordingRepository
from .transcriber import Transcriber


logger = logging.getLogger(__name__)


class WorkerSingletonLock:
    """An OS-backed lock that prevents two processes from recovering the same queue."""

    def __init__(self, path: Path):
        self.path = path
        self._handle: BinaryIO | None = None
        self._state_lock = Lock()

    def acquire(self) -> bool:
        with self._state_lock:
            if self._handle is not None:
                return True

            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
            try:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)

                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return False

            self._handle = handle
            return True

    def release(self) -> None:
        with self._state_lock:
            handle = self._handle
            if handle is None:
                return

            self._handle = None
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                logger.warning("Could not explicitly release transcription worker lock", exc_info=True)
            finally:
                try:
                    handle.close()
                except OSError:
                    logger.warning("Could not close transcription worker lock file", exc_info=True)


class QueueWorker:
    def __init__(
        self,
        *,
        repository: RecordingRepository,
        media_dir: Path,
        lock_path: Path,
        transcriber_factory: Callable[[], Transcriber],
        poll_seconds: float,
    ):
        self.repository = repository
        self.media_dir = media_dir.resolve()
        self.transcriber_factory = transcriber_factory
        self.poll_seconds = poll_seconds
        self._singleton_lock = WorkerSingletonLock(lock_path)
        self._lifecycle_lock = Lock()
        self._stop_event = Event()
        self._wake_event = Event()
        self._thread: Thread | None = None
        self._transcriber: Transcriber | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.is_running:
                return
            if not self._singleton_lock.acquire():
                raise RuntimeError("Another transcription worker already owns this data directory")

            try:
                self.repository.recover_interrupted()
                self._stop_event.clear()
                self._thread = Thread(target=self._run, name="transcription-worker", daemon=True)
                self._thread.start()
            except BaseException:
                self._singleton_lock.release()
                raise

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if not thread.is_alive():
                self._singleton_lock.release()

    def notify(self) -> None:
        self._wake_event.set()

    def _safe_media_path(self, stored_filename: str) -> Path:
        candidate = (self.media_dir / stored_filename).resolve()
        if not candidate.is_relative_to(self.media_dir):
            raise ValueError("Stored media path escapes the media directory")
        return candidate

    def _get_transcriber(self) -> Transcriber:
        if self._transcriber is None:
            self._transcriber = self.transcriber_factory()
        return self._transcriber

    def _process(self, recording: dict[str, object]) -> None:
        recording_id = str(recording["id"])
        try:
            media_path = self._safe_media_path(str(recording["stored_filename"]))
            if not media_path.is_file():
                raise FileNotFoundError("Uploaded media file is missing")
            initial_prompt = (
                "Українська університетська лекція. "
                f"Дисципліна: {recording['lesson_title']}. "
                f"Контекст: {recording['scope_label']}."
            )
            result = self._get_transcriber().transcribe(media_path, initial_prompt=initial_prompt)
            if not self.repository.mark_ready(recording_id, result):
                raise RuntimeError("Recording state changed before transcription completed")
        except Exception as exc:  # the persisted failure is part of the queue contract
            logger.exception("Transcription failed for recording %s", recording_id)
            message = str(exc).strip() or exc.__class__.__name__
            if not self.repository.mark_failed(recording_id, message):
                raise RuntimeError("Could not persist the transcription failure") from exc

    def _run(self) -> None:
        recover_after_error = False
        try:
            while not self._stop_event.is_set():
                try:
                    if recover_after_error:
                        self.repository.recover_interrupted()
                        recover_after_error = False

                    recording = self.repository.claim_next()
                    if recording is not None:
                        self._process(recording)
                        continue
                except Exception:
                    recover_after_error = True
                    logger.exception("Unexpected transcription worker loop failure")

                self._wake_event.wait(self.poll_seconds)
                self._wake_event.clear()
        finally:
            self._singleton_lock.release()
