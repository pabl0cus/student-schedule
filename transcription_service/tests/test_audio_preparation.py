from __future__ import annotations

import hashlib
import io
import subprocess
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from app.audio_preparation import (
    AudioPreparationCancelled,
    AudioPreparationWorker,
    FfmpegAudioEncoder,
)
from app.config import Settings
from app.database import RecordingRepository


class FakeEncoder:
    def __init__(self, payload: bytes = b"OggS-normalized", error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[tuple[Path, Path]] = []

    def encode(self, source: Path, destination: Path, *, stop_event: Event) -> None:
        assert not stop_event.is_set()
        self.calls.append((source, destination))
        if self.error is not None:
            raise self.error
        destination.write_bytes(self.payload)


def _repository(settings: Settings) -> RecordingRepository:
    repository = RecordingRepository(settings.database_path)
    repository.initialize()
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    settings.prepared_audio_dir.mkdir(parents=True, exist_ok=True)
    return repository


def _recording(repository: RecordingRepository, settings: Settings) -> str:
    recording_id = str(uuid4())
    filename = f"{recording_id}.webm"
    settings.media_dir.joinpath(filename).write_bytes(b"original-video")
    repository.create(
        recording_id=recording_id,
        lesson_key="lesson",
        lesson_title="Алгоритми",
        scope_label="Група ІП-01",
        original_filename="lecture.webm",
        stored_filename=filename,
        content_type="video/webm",
        size_bytes=len(b"original-video"),
        recorded_at="2026-09-04",
    )
    return recording_id


def _worker(
    repository: RecordingRepository,
    settings: Settings,
    encoder: FakeEncoder,
    *,
    max_attempts: int = 3,
) -> AudioPreparationWorker:
    return AudioPreparationWorker(
        repository=repository,
        media_dir=settings.media_dir,
        prepared_audio_dir=settings.prepared_audio_dir,
        lock_path=settings.audio_preparation_lock_path,
        encoder_factory=lambda: encoder,
        poll_seconds=0.01,
        max_attempts=max_attempts,
        max_output_bytes=1024,
    )


def test_preparation_atomically_publishes_descriptor_and_keeps_original(settings: Settings) -> None:
    repository = _repository(settings)
    recording_id = _recording(repository, settings)
    encoder = FakeEncoder()
    worker = _worker(repository, settings, encoder)

    claimed = repository.claim_audio_preparation(max_attempts=3)
    assert claimed is not None
    worker._process(claimed)

    artifact = repository.get_audio_artifact(recording_id)
    assert artifact is not None
    assert artifact["state"] == "ready"
    assert artifact["stored_filename"] == f"{recording_id}.ogg"
    assert artifact["content_type"] == "audio/ogg"
    assert artifact["size_bytes"] == len(encoder.payload)
    assert artifact["sha256"] == hashlib.sha256(encoder.payload).hexdigest()
    assert settings.prepared_audio_dir.joinpath(f"{recording_id}.ogg").read_bytes() == encoder.payload
    assert settings.media_dir.joinpath(f"{recording_id}.webm").read_bytes() == b"original-video"
    assert not list(settings.prepared_audio_dir.glob(".*.preparing"))


def test_failed_encoder_retries_to_limit_then_marks_recording_failed(settings: Settings) -> None:
    repository = _repository(settings)
    recording_id = _recording(repository, settings)
    encoder = FakeEncoder(error=RuntimeError("decoder rejected input"))
    worker = _worker(repository, settings, encoder, max_attempts=2)

    for expected_state in ("pending", "failed"):
        claimed = repository.claim_audio_preparation(max_attempts=2)
        assert claimed is not None
        worker._process(claimed)
        assert repository.get_audio_artifact(recording_id)["state"] == expected_state

    assert repository.get(recording_id)["status"] == "failed"
    assert repository.get(recording_id)["attempts"] == 0
    assert len(encoder.calls) == 2

    retried = repository.retry(recording_id)
    assert retried is not None
    assert retried["attempts"] == 0
    assert repository.get_audio_artifact(recording_id)["state"] == "pending"
    assert repository.get_audio_artifact(recording_id)["attempts"] == 0


def test_cancelled_preparation_is_benign_and_does_not_consume_attempt(settings: Settings) -> None:
    repository = _repository(settings)
    recording_id = _recording(repository, settings)
    encoder = FakeEncoder(error=AudioPreparationCancelled("shutdown"))
    worker = _worker(repository, settings, encoder)
    claimed = repository.claim_audio_preparation(max_attempts=3)
    assert claimed is not None
    assert repository.get_audio_artifact(recording_id)["attempts"] == 1

    worker._process(claimed)

    artifact = repository.get_audio_artifact(recording_id)
    assert artifact["state"] == "pending"
    assert artifact["attempts"] == 0
    assert repository.get(recording_id)["status"] == "queued"


def test_startup_adopts_atomic_final_and_resets_unfinished_claim(settings: Settings) -> None:
    repository = _repository(settings)
    adopted_id = _recording(repository, settings)
    assert repository.claim_audio_preparation(max_attempts=3)["id"] == adopted_id
    adopted_payload = b"OggS-after-crash"
    settings.prepared_audio_dir.joinpath(f"{adopted_id}.ogg").write_bytes(adopted_payload)
    pending_id = _recording(repository, settings)
    assert repository.claim_audio_preparation(max_attempts=3)["id"] == pending_id
    unrelated = settings.prepared_audio_dir / "keep-me.ogg"
    unrelated.write_bytes(b"not managed")

    _worker(repository, settings, FakeEncoder())._reconcile_filesystem()

    adopted = repository.get_audio_artifact(adopted_id)
    assert adopted["state"] == "ready"
    assert adopted["sha256"] == hashlib.sha256(adopted_payload).hexdigest()
    pending = repository.get_audio_artifact(pending_id)
    assert pending["state"] == "pending"
    assert pending["attempts"] == 0
    assert unrelated.read_bytes() == b"not managed"


def test_startup_invalidates_corrupt_ready_artifact(settings: Settings) -> None:
    repository = _repository(settings)
    recording_id = _recording(repository, settings)
    assert repository.claim_audio_preparation(max_attempts=3) is not None
    path = settings.prepared_audio_dir / f"{recording_id}.ogg"
    path.write_bytes(b"OggS-good")
    assert repository.mark_audio_prepared(
        recording_id,
        stored_filename=path.name,
        size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    path.write_bytes(b"OggS-bad!")

    _worker(repository, settings, FakeEncoder())._reconcile_filesystem()

    assert repository.get_audio_artifact(recording_id)["state"] == "pending"
    assert not path.exists()


def test_pending_regeneration_replaces_old_canonical_file(settings: Settings) -> None:
    repository = _repository(settings)
    recording_id = _recording(repository, settings)
    assert repository.claim_audio_preparation(max_attempts=3) is not None
    path = settings.prepared_audio_dir / f"{recording_id}.ogg"
    path.write_bytes(b"OggS-old")
    assert repository.mark_audio_prepared(
        recording_id,
        stored_filename=path.name,
        size_bytes=path.stat().st_size,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    assert repository.invalidate_audio_artifact(recording_id)
    encoder = FakeEncoder(b"OggS-new")
    claimed = repository.claim_audio_preparation(max_attempts=3)
    assert claimed is not None

    _worker(repository, settings, encoder)._process(claimed)

    assert path.read_bytes() == b"OggS-new"
    assert repository.get_audio_artifact(recording_id)["state"] == "ready"


def test_ffmpeg_uses_hardened_audio_only_argv_without_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class CompletedProcess:
        returncode = 0
        stderr = io.BytesIO()

        def poll(self) -> int:
            return 0

    def fake_popen(command: list[str], **kwargs: object) -> CompletedProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return CompletedProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=b"12.5\n"),
    )
    source = tmp_path / "source.webm"
    destination = tmp_path / "output.ogg"
    source.write_bytes(b"media")

    FfmpegAudioEncoder(binary="ffmpeg-test", timeout_seconds=10, max_output_bytes=100).encode(
        source,
        destination,
        stop_event=Event(),
    )

    command = captured["command"]
    assert isinstance(command, list)
    joined = " ".join(command)
    assert command[0] == "ffmpeg-test"
    assert captured["kwargs"]["shell"] is False
    for required in (
        "-nostdin",
        "-protocol_whitelist file",
        "-map 0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-map_metadata -1",
        "-map_chapters -1",
        "-copyts",
        "-start_at_zero",
        "aresample=async=1:first_pts=0",
        "-ac 1",
        "-ar 16000",
        "-c:a libopus",
        "-b:a 32k",
        "-f ogg",
    ):
        assert required in joined


def test_ffmpeg_rejects_prepared_audio_longer_than_transcript_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoder = FfmpegAudioEncoder(binary="ffmpeg-test", probe_binary="ffprobe-test")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=b"86400.01\n"),
    )

    with pytest.raises(ValueError, match="24-hour"):
        encoder._validate_duration(tmp_path / "output.ogg", stop_event=Event())
