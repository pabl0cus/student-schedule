from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import shutil
import stat
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Protocol
from uuid import UUID, uuid4

from .database import RecordingRepository
from .worker import WorkerSingletonLock

logger = logging.getLogger(__name__)

PREPARED_CONTENT_TYPE = "audio/ogg"
PREPARED_SUFFIX = ".ogg"
MAX_TRANSCRIPT_DURATION_SECONDS = 24 * 60 * 60
TEMP_ARTIFACT_PATTERN = re.compile(
    r"^\.(?P<recording_id>[0-9a-f-]{36})\.[0-9a-f]{32}\.preparing$"
)


class AudioPreparationCancelled(RuntimeError):
    pass


class AudioEncoder(Protocol):
    def encode(self, source: Path, destination: Path, *, stop_event: Event) -> None: ...


class FfmpegAudioEncoder:
    """Convert an untrusted media container into compact, audio-only Ogg/Opus."""

    def __init__(
        self,
        *,
        binary: str = "ffmpeg",
        probe_binary: str | None = None,
        timeout_seconds: float = 6 * 60 * 60,
        max_output_bytes: int = 500 * 1024 * 1024,
    ):
        if not binary.strip():
            raise ValueError("ffmpeg binary must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("ffmpeg timeout must be greater than zero")
        if max_output_bytes <= 0:
            raise ValueError("maximum prepared audio size must be greater than zero")
        self.binary = binary
        binary_path = Path(binary)
        probe_name = "ffprobe.exe" if binary_path.name.lower().endswith(".exe") else "ffprobe"
        self.probe_binary = probe_binary or str(binary_path.with_name(probe_name))
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def check_available(self) -> None:
        if shutil.which(self.binary) is None:
            raise RuntimeError(f"ffmpeg executable is unavailable: {self.binary}")
        if shutil.which(self.probe_binary) is None:
            raise RuntimeError(f"ffprobe executable is unavailable: {self.probe_binary}")

    def encode(self, source: Path, destination: Path, *, stop_event: Event) -> None:
        command = [
            self.binary,
            "-nostdin",
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "error",
            "-xerror",
            "-y",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "aac,avi,flac,mov,matroska,webm,mp3,ogg,wav",
            "-copyts",
            "-start_at_zero",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-af",
            "aresample=async=1:first_pts=0",
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
            "-vbr",
            "on",
            "-application",
            "audio",
            "-threads",
            "1",
            "-f",
            "ogg",
            str(destination),
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        started_at = time.monotonic()
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=creation_flags,
        )
        error_tail = bytearray()

        def drain_errors() -> None:
            assert process.stderr is not None
            while chunk := process.stderr.read(4096):
                error_tail.extend(chunk)
                if len(error_tail) > 8192:
                    del error_tail[:-8192]

        error_reader = Thread(target=drain_errors, name="ffmpeg-stderr", daemon=True)
        error_reader.start()
        while process.poll() is None:
            stop_requested = stop_event.wait(0.2)
            if process.poll() is not None:
                break
            if stop_requested:
                self._terminate(process)
                error_reader.join(timeout=5)
                raise AudioPreparationCancelled("Audio preparation was interrupted")
            try:
                if destination.stat().st_size > self.max_output_bytes:
                    self._terminate(process)
                    error_reader.join(timeout=5)
                    raise ValueError("Prepared audio exceeds the configured size limit")
            except FileNotFoundError:
                pass
            if time.monotonic() - started_at > self.timeout_seconds:
                self._terminate(process)
                error_reader.join(timeout=5)
                raise TimeoutError("ffmpeg exceeded the audio preparation time limit")

        error_reader.join(timeout=5)
        if process.returncode != 0:
            detail = bytes(error_tail).decode("utf-8", errors="replace").strip()
            raise RuntimeError(detail[-2000:] or f"ffmpeg exited with code {process.returncode}")
        self._validate_duration(destination, stop_event=stop_event)

    def _validate_duration(self, destination: Path, *, stop_event: Event) -> None:
        if stop_event.is_set():
            raise AudioPreparationCancelled("Audio preparation was interrupted")
        command = [
            self.probe_binary,
            "-v",
            "quiet",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "ogg",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(destination),
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                timeout=min(self.timeout_seconds, 60.0),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("ffprobe exceeded the prepared-audio validation time limit") from exc
        if stop_event.is_set():
            raise AudioPreparationCancelled("Audio preparation was interrupted")
        if completed.returncode != 0 or len(completed.stdout) > 128:
            raise RuntimeError("ffprobe could not validate prepared audio duration")
        try:
            duration = float(completed.stdout.decode("ascii").strip())
        except (UnicodeError, ValueError) as exc:
            raise RuntimeError("ffprobe returned an invalid prepared-audio duration") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise RuntimeError("Prepared audio duration is invalid")
        if duration > MAX_TRANSCRIPT_DURATION_SECONDS:
            raise ValueError("Prepared audio exceeds the 24-hour transcription limit")

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            if process.poll() is not None:
                return
            raise
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


class AudioPreparationWorker:
    """Single-concurrency durable preparation queue for community worker audio."""

    def __init__(
        self,
        *,
        repository: RecordingRepository,
        media_dir: Path,
        prepared_audio_dir: Path,
        lock_path: Path,
        encoder_factory: Callable[[], AudioEncoder],
        poll_seconds: float,
        max_attempts: int,
        max_output_bytes: int,
    ):
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be greater than zero")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be greater than zero")
        self.repository = repository
        self.media_dir = media_dir.resolve()
        self.prepared_audio_dir = prepared_audio_dir.resolve()
        self.encoder_factory = encoder_factory
        self.poll_seconds = poll_seconds
        self.max_attempts = max_attempts
        self.max_output_bytes = max_output_bytes
        self._singleton_lock = WorkerSingletonLock(lock_path)
        self._lifecycle_lock = Lock()
        self._stop_event = Event()
        self._wake_event = Event()
        self._thread: Thread | None = None
        self._encoder: AudioEncoder | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.is_running:
                return
            self.prepared_audio_dir.mkdir(parents=True, exist_ok=True)
            if not self._singleton_lock.acquire():
                raise RuntimeError("Another audio preparation worker already owns this data directory")
            try:
                availability_check = getattr(self._encoder_instance(), "check_available", None)
                if callable(availability_check):
                    availability_check()
                self._reconcile_filesystem()
                self._stop_event.clear()
                self._thread = Thread(target=self._run, name="audio-preparation-worker", daemon=True)
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

    def _encoder_instance(self) -> AudioEncoder:
        if self._encoder is None:
            self._encoder = self.encoder_factory()
        return self._encoder

    @staticmethod
    def _recording_id(value: object) -> str:
        normalized = str(UUID(str(value)))
        if normalized != value:
            raise ValueError("Recording id is not canonical")
        return normalized

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _safe_source(self, stored_filename: object) -> Path:
        name = str(stored_filename)
        if Path(name).name != name or name in ("", ".", ".."):
            raise ValueError("Stored media filename is unsafe")
        candidate = self.media_dir / name
        if candidate.parent.resolve() != self.media_dir or candidate.is_symlink() or not candidate.is_file():
            raise FileNotFoundError("Uploaded media file is missing or unsafe")
        return candidate

    def _safe_artifact(self, filename: str) -> Path:
        if Path(filename).name != filename or not filename.endswith(PREPARED_SUFFIX):
            raise ValueError("Prepared audio filename is unsafe")
        candidate = self.prepared_audio_dir / filename
        if candidate.parent.resolve() != self.prepared_audio_dir:
            raise ValueError("Prepared audio path escapes its storage directory")
        return candidate

    def _descriptor(self, path: Path) -> tuple[int, str]:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
            raise ValueError("Prepared audio is not a regular file")
        if metadata.st_size <= 0 or metadata.st_size > self.max_output_bytes:
            raise ValueError("Prepared audio has an invalid size")
        return metadata.st_size, self._hash_file(path)

    def _reconcile_filesystem(self) -> None:
        artifacts = self.repository.list_audio_artifacts()
        known_ready: set[str] = set()
        for artifact in artifacts:
            recording_id = self._recording_id(artifact["recording_id"])
            filename = f"{recording_id}{PREPARED_SUFFIX}"
            final_path = self._safe_artifact(filename)
            state = artifact["state"]
            if state == "processing" and final_path.exists():
                try:
                    size_bytes, sha256 = self._descriptor(final_path)
                    if self.repository.mark_audio_prepared(
                        recording_id,
                        stored_filename=filename,
                        size_bytes=size_bytes,
                        sha256=sha256,
                    ):
                        known_ready.add(filename)
                        continue
                except (OSError, ValueError):
                    logger.warning("Could not adopt interrupted audio artifact %s", recording_id, exc_info=True)
                final_path.unlink(missing_ok=True)
            elif state == "ready":
                try:
                    expected_name = str(artifact["stored_filename"])
                    ready_path = self._safe_artifact(expected_name)
                    size_bytes, sha256 = self._descriptor(ready_path)
                    if (
                        expected_name == filename
                        and size_bytes == artifact["size_bytes"]
                        and sha256 == artifact["sha256"]
                        and artifact["content_type"] == PREPARED_CONTENT_TYPE
                    ):
                        known_ready.add(expected_name)
                        continue
                except (OSError, ValueError):
                    pass
                logger.warning("Resetting missing or corrupt prepared audio for recording %s", recording_id)
                self.repository.invalidate_audio_artifact(recording_id)
                final_path.unlink(missing_ok=True)

        self.repository.recover_audio_preparation()

        for candidate in self.prepared_audio_dir.iterdir():
            try:
                match = TEMP_ARTIFACT_PATTERN.fullmatch(candidate.name)
                if match is not None:
                    UUID(match.group("recording_id"))
                    if candidate.is_file() or candidate.is_symlink():
                        candidate.unlink(missing_ok=True)
                    continue
                if candidate.suffix == PREPARED_SUFFIX:
                    UUID(candidate.stem)
                    if candidate.name not in known_ready and (candidate.is_file() or candidate.is_symlink()):
                        candidate.unlink(missing_ok=True)
            except (OSError, ValueError):
                logger.warning("Could not reconcile prepared audio artifact %s", candidate, exc_info=True)
        self._sync_directory(self.prepared_audio_dir)

    def _process(self, recording: dict[str, object]) -> None:
        recording_id = self._recording_id(recording["id"])
        filename = f"{recording_id}{PREPARED_SUFFIX}"
        final_path = self._safe_artifact(filename)
        temporary_path = self.prepared_audio_dir / f".{recording_id}.{uuid4().hex}.preparing"
        published = False
        try:
            source_path = self._safe_source(recording["stored_filename"])
            final_path.unlink(missing_ok=True)
            self._sync_directory(self.prepared_audio_dir)
            self._encoder_instance().encode(source_path, temporary_path, stop_event=self._stop_event)
            size_bytes, sha256 = self._descriptor(temporary_path)
            with temporary_path.open("r+b") as output:
                os.fsync(output.fileno())
            os.replace(temporary_path, final_path)
            self._sync_directory(self.prepared_audio_dir)
            if not self.repository.mark_audio_prepared(
                recording_id,
                stored_filename=filename,
                size_bytes=size_bytes,
                sha256=sha256,
            ):
                raise RuntimeError("Audio preparation state changed before publication")
            published = True
        except AudioPreparationCancelled:
            self.repository.reset_audio_preparation(recording_id)
        except Exception as exc:
            logger.exception("Audio preparation failed for recording %s", recording_id)
            message = str(exc).strip() or exc.__class__.__name__
            if self.repository.release_audio_preparation(
                recording_id,
                error=message,
                max_attempts=self.max_attempts,
            ) is None:
                logger.error("Could not persist audio preparation failure for recording %s", recording_id)
        finally:
            temporary_path.unlink(missing_ok=True)
            if not published:
                final_path.unlink(missing_ok=True)

    def _run(self) -> None:
        recover_after_error = False
        try:
            while not self._stop_event.is_set():
                try:
                    if recover_after_error:
                        self.repository.recover_audio_preparation()
                        recover_after_error = False
                    recording = self.repository.claim_audio_preparation(max_attempts=self.max_attempts)
                    if recording is not None:
                        self._process(recording)
                        continue
                except Exception:
                    recover_after_error = True
                    logger.exception("Unexpected audio preparation worker loop failure")

                self._wake_event.wait(self.poll_seconds)
                self._wake_event.clear()
        finally:
            self._singleton_lock.release()
