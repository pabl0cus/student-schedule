from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import app.database as database_module
import pytest
from app.config import Settings
from app.database import RecordingRepository
from app.schemas import TranscriptionPayload
from app.transcriber import TranscriptionResult

INITIAL_TIME = "2026-09-04T10:00:00.000+00:00"


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    current = {"value": INITIAL_TIME}
    monkeypatch.setattr(database_module, "utc_now", lambda: current["value"])
    return current


def _repository(settings: Settings) -> RecordingRepository:
    repository = RecordingRepository(settings.database_path)
    repository.initialize()
    return repository


def _create_worker(repository: RecordingRepository, worker_id: str) -> dict[str, object]:
    return repository.create_community_worker(
        worker_id=worker_id,
        label=f"Worker {worker_id}",
        token_hash=hashlib.sha256(worker_id.encode()).hexdigest(),
    )


def _create_recording(repository: RecordingRepository, number: int) -> str:
    recording_id = f"00000000-0000-0000-0000-{number:012d}"
    repository.create(
        recording_id=recording_id,
        lesson_key=f"lesson-{number}",
        lesson_title=f"Title {number}",
        scope_label="Scope",
        original_filename=f"lecture-{number}.webm",
        stored_filename=f"{recording_id}.webm",
        content_type="video/webm",
        size_bytes=10,
        recorded_at=None,
    )
    preparation = repository.claim_audio_preparation(max_attempts=3)
    assert preparation is not None
    assert preparation["id"] == recording_id
    assert repository.mark_audio_prepared(
        recording_id,
        stored_filename=f"{recording_id}.ogg",
        size_bytes=5,
        sha256="a" * 64,
    )
    return recording_id


def _claim(
    repository: RecordingRepository,
    worker_id: str,
    number: int,
    *,
    lease_seconds: int = 300,
    max_attempts: int = 5,
) -> dict[str, object] | None:
    return repository.claim_remote(
        worker_id=worker_id,
        claim_id=f"claim-{worker_id}-{number}",
        lease_token_hash=f"lease-hash-{worker_id}-{number}",
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
    )


def _payload(text: str = "Вітаю на парі.") -> TranscriptionPayload:
    return TranscriptionPayload.model_validate(
        {
            "transcript": [
                {
                    "id": 0,
                    "start": 1.25,
                    "end": 3.5,
                    "text": text,
                    "words": [
                        {
                            "start": 1.25,
                            "end": 1.8,
                            "word": "Вітаю",
                            "probability": 0.99,
                        }
                    ],
                }
            ],
            "duration_seconds": 3.5,
            "detected_language": "uk",
            "language_probability": 0.999,
        }
    )


def _create_legacy_recordings_table(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE recordings (
                id TEXT PRIMARY KEY,
                lesson_key TEXT NOT NULL,
                lesson_title TEXT NOT NULL,
                scope_label TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                duration_seconds REAL,
                detected_language TEXT,
                language_probability REAL,
                transcript_json TEXT,
                error TEXT,
                recorded_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def test_initialize_migrates_recordings_and_creates_community_tables(settings: Settings) -> None:
    _create_legacy_recordings_table(settings.database_path)
    legacy_id = "00000000-0000-0000-0000-000000000001"
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO recordings (
                id, lesson_key, lesson_title, scope_label, original_filename,
                stored_filename, content_type, size_bytes, status, attempts,
                created_at, updated_at
            ) VALUES (?, 'lesson', 'Title', 'Scope', 'lecture.mp4', ?,
                      'video/mp4', 10, 'queued', 0, ?, ?)
            """,
            (legacy_id, f"{legacy_id}.mp4", INITIAL_TIME, INITIAL_TIME),
        )

    repository = RecordingRepository(settings.database_path)
    repository.initialize()

    with sqlite3.connect(settings.database_path) as connection:
        recording_columns = {row[1] for row in connection.execute("PRAGMA table_info(recordings)")}
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {
        "result_id",
        "result_hash",
        "engine",
        "model",
        "engine_version",
        "client_version",
    } <= recording_columns
    assert {"community_workers", "transcription_leases", "recording_audio_artifacts"} <= tables
    artifact = repository.get_audio_artifact(legacy_id)
    assert artifact is not None
    assert artifact["state"] == "pending"


def test_remote_claim_waits_for_prepared_audio(settings: Settings) -> None:
    repository = _repository(settings)
    _create_worker(repository, "worker-one")
    recording_id = "00000000-0000-0000-0000-000000000001"
    repository.create(
        recording_id=recording_id,
        lesson_key="lesson",
        lesson_title="Title",
        scope_label="Scope",
        original_filename="lecture.webm",
        stored_filename=f"{recording_id}.webm",
        content_type="video/webm",
        size_bytes=10,
        recorded_at=None,
    )

    assert _claim(repository, "worker-one", 1) is None
    assert repository.get(recording_id)["status"] == "queued"
    assert repository.claim_audio_preparation(max_attempts=3)["id"] == recording_id
    assert repository.mark_audio_prepared(
        recording_id,
        stored_filename=f"{recording_id}.ogg",
        size_bytes=6,
        sha256="b" * 64,
    )
    assert _claim(repository, "worker-one", 1) is not None


def test_community_worker_lifecycle_and_revocation_requeues_jobs(
    settings: Settings,
    clock: dict[str, str],
) -> None:
    repository = _repository(settings)
    worker = _create_worker(repository, "worker-one")
    recording_id = _create_recording(repository, 1)
    claimed = _claim(repository, "worker-one", 1)
    assert claimed is not None

    assert worker == {
        "id": "worker-one",
        "label": "Worker worker-one",
        "token_hash": hashlib.sha256(b"worker-one").hexdigest(),
        "created_at": INITIAL_TIME,
        "last_seen_at": None,
        "revoked_at": None,
    }
    assert repository.get_community_worker("worker-one") is not None
    assert [item["id"] for item in repository.list_community_workers()] == ["worker-one"]

    clock["value"] = "2026-09-04T10:01:00.000+00:00"
    assert repository.touch_community_worker("worker-one") is True
    assert repository.get_community_worker("worker-one")["last_seen_at"] == clock["value"]

    clock["value"] = "2026-09-04T10:02:00.000+00:00"
    assert repository.revoke_community_worker("worker-one") == 1
    assert repository.get_community_worker("worker-one")["revoked_at"] == clock["value"]
    assert repository.get(recording_id)["status"] == "queued"
    assert repository.get_transcription_lease(recording_id) is None
    assert repository.touch_community_worker("worker-one") is False
    assert repository.revoke_community_worker("worker-one") == 0
    assert repository.revoke_community_worker("missing") is None


def test_remote_claim_is_fifo_and_limits_each_worker_to_one_active_job(
    settings: Settings,
    clock: dict[str, str],
) -> None:
    repository = _repository(settings)
    _create_worker(repository, "worker-one")
    _create_worker(repository, "worker-two")
    first_id = _create_recording(repository, 1)
    second_id = _create_recording(repository, 2)

    first = _claim(repository, "worker-one", 1)
    assert first is not None
    assert first["id"] == first_id
    assert first["status"] == "processing"
    assert first["attempts"] == 1
    assert first["lease"] == {
        "recording_id": first_id,
        "worker_id": "worker-one",
        "claim_id": "claim-worker-one-1",
        "leased_at": INITIAL_TIME,
        "lease_expires_at": "2026-09-04T10:05:00.000+00:00",
        "heartbeat_at": INITIAL_TIME,
    }
    assert repository.claim_remote(
        worker_id="worker-one",
        claim_id="another-claim",
        lease_token_hash="another-hash",
        lease_seconds=300,
        max_attempts=5,
    ) is None

    second = _claim(repository, "worker-two", 2)
    assert second is not None
    assert second["id"] == second_id
    assert second["attempts"] == 1
    assert clock["value"] == INITIAL_TIME


def test_concurrent_remote_claimers_cannot_claim_the_same_recording(
    settings: Settings,
    clock: dict[str, str],
) -> None:
    repository = _repository(settings)
    _create_worker(repository, "worker-one")
    _create_worker(repository, "worker-two")
    recording_id = _create_recording(repository, 1)

    def claim(worker_id: str) -> dict[str, object] | None:
        independent_repository = RecordingRepository(settings.database_path)
        return _claim(independent_repository, worker_id, 1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, ("worker-one", "worker-two")))

    successful = [claim for claim in claims if claim is not None]
    assert len(successful) == 1
    assert successful[0]["id"] == recording_id
    assert repository.get(recording_id)["attempts"] == 1
    assert clock["value"] == INITIAL_TIME


def test_recover_interrupted_preserves_valid_remote_lease_and_reaps_expired_one(
    settings: Settings,
    clock: dict[str, str],
) -> None:
    repository = _repository(settings)
    _create_worker(repository, "worker-one")
    remote_id = _create_recording(repository, 1)
    local_id = _create_recording(repository, 2)
    assert _claim(repository, "worker-one", 1, lease_seconds=300) is not None
    assert repository.claim_next()["id"] == local_id

    assert repository.recover_interrupted() == 1
    assert repository.get(remote_id)["status"] == "processing"
    assert repository.get(local_id)["status"] == "queued"

    clock["value"] = "2026-09-04T10:05:01.000+00:00"
    assert repository.recover_interrupted() == 1
    assert repository.get(remote_id)["status"] == "queued"
    assert repository.get_transcription_lease(remote_id) is None


def test_validate_and_heartbeat_require_the_current_unexpired_lease(
    settings: Settings,
    clock: dict[str, str],
) -> None:
    repository = _repository(settings)
    _create_worker(repository, "worker-one")
    recording_id = _create_recording(repository, 1)
    assert _claim(repository, "worker-one", 1, lease_seconds=120) is not None
    lease_arguments = {
        "recording_id": recording_id,
        "worker_id": "worker-one",
        "claim_id": "claim-worker-one-1",
        "lease_token_hash": "lease-hash-worker-one-1",
    }

    assert repository.validate_remote_lease(**lease_arguments) is not None
    assert repository.validate_remote_lease(**{**lease_arguments, "lease_token_hash": "wrong"}) is None

    clock["value"] = "2026-09-04T10:01:00.000+00:00"
    renewed = repository.heartbeat_remote(**lease_arguments, lease_seconds=300)
    assert renewed is not None
    assert renewed["heartbeat_at"] == clock["value"]
    assert renewed["lease_expires_at"] == "2026-09-04T10:06:00.000+00:00"

    clock["value"] = "2026-09-04T10:06:01.000+00:00"
    assert repository.heartbeat_remote(**lease_arguments, lease_seconds=300) is None
    assert repository.get(recording_id)["status"] == "queued"


def test_remote_release_requeues_until_max_attempts_then_fails(
    settings: Settings,
    clock: dict[str, str],
) -> None:
    repository = _repository(settings)
    _create_worker(repository, "worker-one")
    recording_id = _create_recording(repository, 1)
    assert _claim(repository, "worker-one", 1) is not None
    first_lease = {
        "recording_id": recording_id,
        "worker_id": "worker-one",
        "claim_id": "claim-worker-one-1",
        "lease_token_hash": "lease-hash-worker-one-1",
    }

    assert repository.release_remote(
        **{**first_lease, "lease_token_hash": "wrong"},
        error="temporary",
        retryable=True,
        max_attempts=2,
    ) is None
    assert repository.get(recording_id)["status"] == "processing"
    assert repository.release_remote(
        **first_lease,
        error="temporary",
        retryable=True,
        max_attempts=2,
    ) == "queued"

    clock["value"] = "2026-09-04T10:01:00.000+00:00"
    assert _claim(repository, "worker-one", 2) is not None
    assert repository.release_remote(
        recording_id=recording_id,
        worker_id="worker-one",
        claim_id="claim-worker-one-2",
        lease_token_hash="lease-hash-worker-one-2",
        error="CUDA failure",
        retryable=True,
        max_attempts=2,
    ) == "failed"
    failed = repository.get(recording_id)
    assert failed["attempts"] == 2
    assert failed["error"] == "CUDA failure"
    assert repository.get_transcription_lease(recording_id) is None

    retried = repository.retry(recording_id)
    assert retried is not None
    assert retried["attempts"] == 0
    assert _claim(repository, "worker-one", 3, max_attempts=2) is not None


def test_expired_remote_leases_honor_the_claim_attempt_limit(
    settings: Settings,
    clock: dict[str, str],
) -> None:
    repository = _repository(settings)
    _create_worker(repository, "worker-one")
    _create_worker(repository, "worker-two")
    recording_id = _create_recording(repository, 1)

    assert _claim(repository, "worker-one", 1, lease_seconds=60, max_attempts=2) is not None
    clock["value"] = "2026-09-04T10:01:01.000+00:00"
    assert _claim(repository, "worker-two", 2, lease_seconds=60, max_attempts=2) is not None
    clock["value"] = "2026-09-04T10:02:02.000+00:00"

    assert _claim(repository, "worker-one", 3, lease_seconds=60, max_attempts=2) is None
    failed = repository.get(recording_id)
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["attempts"] == 2
    assert failed["error"] == "Community transcription attempt limit reached"
    assert repository.get_transcription_lease(recording_id) is None


def test_remote_completion_is_atomic_validated_and_idempotent(
    settings: Settings,
    clock: dict[str, str],
) -> None:
    repository = _repository(settings)
    _create_worker(repository, "worker-one")
    recording_id = _create_recording(repository, 1)
    second_id = _create_recording(repository, 2)
    assert _claim(repository, "worker-one", 1, lease_seconds=60) is not None
    completion = {
        "recording_id": recording_id,
        "worker_id": "worker-one",
        "claim_id": "claim-worker-one-1",
        "lease_token_hash": "lease-hash-worker-one-1",
        "result_id": "submission-1",
        "payload": _payload(),
        "engine": "faster-whisper",
        "model": "large-v3",
        "engine_version": "1.2.1",
        "client_version": "0.1.0",
    }

    assert repository.complete_remote(**completion) == "completed"
    ready = repository.get(recording_id)
    assert ready["status"] == "ready"
    assert ready["result_id"] == "submission-1"
    assert len(ready["result_hash"]) == 64
    assert ready["engine"] == "faster-whisper"
    assert ready["model"] == "large-v3"
    assert ready["engine_version"] == "1.2.1"
    assert ready["client_version"] == "0.1.0"
    assert ready["transcript"][0]["text"] == "Вітаю на парі."

    clock["value"] = "2026-09-04T10:10:00.000+00:00"
    assert repository.complete_remote(**completion) == "duplicate"
    assert repository.complete_remote(**{**completion, "payload": _payload("Інший текст")}) == "conflict"

    next_claim = _claim(repository, "worker-one", 2)
    assert next_claim is not None
    assert next_claim["id"] == second_id


def test_expired_remote_completion_is_rejected_and_job_is_requeued(
    settings: Settings,
    clock: dict[str, str],
) -> None:
    repository = _repository(settings)
    _create_worker(repository, "worker-one")
    recording_id = _create_recording(repository, 1)
    assert _claim(repository, "worker-one", 1, lease_seconds=60) is not None

    clock["value"] = "2026-09-04T10:01:01.000+00:00"
    outcome = repository.complete_remote(
        recording_id=recording_id,
        worker_id="worker-one",
        claim_id="claim-worker-one-1",
        lease_token_hash="lease-hash-worker-one-1",
        result_id="submission-1",
        payload=_payload(),
        engine="faster-whisper",
        model="large-v3",
        engine_version=None,
        client_version=None,
    )

    assert outcome == "lease_lost"
    assert repository.get(recording_id)["status"] == "queued"
    assert repository.get_transcription_lease(recording_id) is None


def test_stale_worker_cannot_invalidate_audio_after_another_worker_reclaims(
    settings: Settings,
    clock: dict[str, str],
) -> None:
    repository = _repository(settings)
    _create_worker(repository, "worker-one")
    _create_worker(repository, "worker-two")
    recording_id = _create_recording(repository, 1)
    assert _claim(repository, "worker-one", 1, lease_seconds=60) is not None
    clock["value"] = "2026-09-04T10:01:01.000+00:00"
    assert _claim(repository, "worker-two", 2, lease_seconds=300) is not None

    stale_invalidation = repository.release_remote(
        recording_id=recording_id,
        worker_id="worker-one",
        claim_id="claim-worker-one-1",
        lease_token_hash="lease-hash-worker-one-1",
        error="stale download",
        retryable=True,
        max_attempts=5,
        count_attempt=False,
        invalidate_audio=True,
    )

    assert stale_invalidation is None
    assert repository.get_audio_artifact(recording_id)["state"] == "ready"
    assert repository.get_transcription_lease(recording_id)["worker_id"] == "worker-two"


def test_local_completion_cannot_overwrite_a_remote_lease(
    settings: Settings,
    clock: dict[str, str],
) -> None:
    repository = _repository(settings)
    _create_worker(repository, "worker-one")
    recording_id = _create_recording(repository, 1)
    assert _claim(repository, "worker-one", 1) is not None

    payload = _payload()
    local_result = TranscriptionResult(
        transcript=[segment.model_dump(mode="json") for segment in payload.transcript],
        duration_seconds=payload.duration_seconds,
        detected_language=payload.detected_language,
        language_probability=payload.language_probability,
    )
    assert repository.mark_ready(recording_id, local_result) is False
    assert repository.mark_failed(recording_id, "local failure") is False
    assert repository.get(recording_id)["status"] == "processing"
    assert clock["value"] == INITIAL_TIME
