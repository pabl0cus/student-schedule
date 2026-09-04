from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.middleware import LOCAL_REQUEST_HEADER, LOCAL_REQUEST_HEADER_VALUE
from app.transcriber import TranscriptionResult


LOCAL_REQUEST_HEADERS = {
    "Origin": "http://localhost:3000",
    LOCAL_REQUEST_HEADER: LOCAL_REQUEST_HEADER_VALUE,
}


class FakeTranscriber:
    def transcribe(self, media_path: Path, *, initial_prompt: str | None = None) -> TranscriptionResult:
        assert media_path.read_bytes() == b"mock-media"
        assert initial_prompt == "Українська університетська лекція. Дисципліна: Title. Контекст: Scope."
        return TranscriptionResult(
            transcript=[
                {
                    "id": 0,
                    "start": 1.25,
                    "end": 3.5,
                    "text": "Вітаю на парі.",
                    "words": [
                        {"start": 1.25, "end": 1.8, "word": "Вітаю", "probability": 0.99},
                        {"start": 1.9, "end": 2.1, "word": "на", "probability": 0.98},
                        {"start": 2.2, "end": 3.5, "word": "парі.", "probability": 0.97},
                    ],
                }
            ],
            duration_seconds=3.5,
            detected_language="uk",
            language_probability=0.999,
        )


class FailingTranscriber:
    def transcribe(self, _: Path, *, initial_prompt: str | None = None) -> TranscriptionResult:
        assert initial_prompt
        raise RuntimeError("mock CUDA failure")


class InvalidTimestampTranscriber:
    def transcribe(self, _: Path, *, initial_prompt: str | None = None) -> TranscriptionResult:
        assert initial_prompt
        return TranscriptionResult(
            transcript=[{"id": 0, "start": 5.0, "end": 1.0, "text": "Некоректний фрагмент", "words": []}],
            duration_seconds=5.0,
            detected_language="uk",
            language_probability=0.99,
        )


def _upload(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/recordings",
        data={"lesson_key": "lesson", "lesson_title": "Title", "scope_label": "Scope"},
        files={"file": ("lecture.webm", b"mock-media", "video/webm")},
    )
    assert response.status_code == 201
    return response.json()


def _wait_for_status(client: TestClient, recording_id: str, expected: str) -> dict[str, object]:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        detail = client.get(f"/recordings/{recording_id}").json()
        if detail["status"] == expected:
            return detail
        time.sleep(0.01)
    raise AssertionError(f"Recording did not reach {expected!r}")


def test_worker_persists_word_timestamps(settings: Settings) -> None:
    application = create_app(
        settings=settings,
        transcriber_factory=FakeTranscriber,
        start_worker=True,
    )
    with TestClient(application) as client:
        client.headers.update(LOCAL_REQUEST_HEADERS)
        created = _upload(client)
        ready = _wait_for_status(client, str(created["id"]), "ready")

    assert ready["detected_language"] == "uk"
    assert ready["language"] == "uk"
    assert ready["progress"] == 100
    assert ready["duration_seconds"] == 3.5
    assert ready["attempts"] == 1
    assert ready["transcript"][0]["text"] == "Вітаю на парі."
    assert ready["transcript"][0]["words"][0] == {
        "start": 1.25,
        "end": 1.8,
        "word": "Вітаю",
        "probability": 0.99,
    }


def test_failed_recording_can_be_retried(settings: Settings) -> None:
    application = create_app(
        settings=settings,
        transcriber_factory=FailingTranscriber,
        start_worker=True,
    )
    with TestClient(application) as client:
        client.headers.update(LOCAL_REQUEST_HEADERS)
        created = _upload(client)
        failed = _wait_for_status(client, str(created["id"]), "failed")
        assert failed["error"] == "mock CUDA failure"

        retry = client.post(f"/recordings/{created['id']}/retry")
        assert retry.status_code == 200
        assert retry.json()["status"] == "queued"
        assert retry.json()["error"] is None


def test_invalid_transcript_is_persisted_as_failure(settings: Settings) -> None:
    application = create_app(
        settings=settings,
        transcriber_factory=InvalidTimestampTranscriber,
        start_worker=True,
    )
    with TestClient(application) as client:
        client.headers.update(LOCAL_REQUEST_HEADERS)
        created = _upload(client)
        failed = _wait_for_status(client, str(created["id"]), "failed")

    assert "segment end must not be earlier than start" in str(failed["error"])


def test_worker_recovers_interrupted_job(settings: Settings) -> None:
    first_application = create_app(settings=settings, start_worker=False)
    repository = first_application.state.repository
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = "00000000-0000-0000-0000-000000000001.webm"
    (settings.media_dir / stored_filename).write_bytes(b"mock-media")
    repository.create(
        recording_id="00000000-0000-0000-0000-000000000001",
        lesson_key="lesson",
        lesson_title="Title",
        scope_label="Scope",
        original_filename="lecture.webm",
        stored_filename=stored_filename,
        content_type="video/webm",
        size_bytes=10,
        recorded_at=None,
    )
    claimed = repository.claim_next()
    assert claimed is not None
    assert repository.get(str(claimed["id"]))["status"] == "processing"

    recovered_application = create_app(
        settings=settings,
        transcriber_factory=FakeTranscriber,
        start_worker=True,
    )
    with TestClient(recovered_application) as client:
        client.headers.update(LOCAL_REQUEST_HEADERS)
        ready = _wait_for_status(client, str(claimed["id"]), "ready")
    assert ready["attempts"] == 2


def test_worker_survives_a_transient_repository_error(settings: Settings) -> None:
    application = create_app(
        settings=settings,
        transcriber_factory=FakeTranscriber,
        start_worker=True,
    )
    repository = application.state.repository
    original_claim_next = repository.claim_next
    attempts = 0

    def flaky_claim_next() -> dict[str, object] | None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary SQLite failure")
        return original_claim_next()

    repository.claim_next = flaky_claim_next
    with TestClient(application) as client:
        client.headers.update(LOCAL_REQUEST_HEADERS)
        created = _upload(client)
        ready = _wait_for_status(client, str(created["id"]), "ready")

    assert attempts >= 2
    assert ready["status"] == "ready"


def test_health_is_unavailable_after_expected_worker_stops(settings: Settings) -> None:
    application = create_app(
        settings=settings,
        transcriber_factory=FakeTranscriber,
        start_worker=True,
    )
    with TestClient(application) as client:
        application.state.worker.stop(timeout=1)
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["detail"] == "Transcription worker is not running"


def test_only_one_worker_can_own_a_data_directory(settings: Settings) -> None:
    first_application = create_app(
        settings=settings,
        transcriber_factory=FakeTranscriber,
        start_worker=True,
    )
    second_application = create_app(
        settings=settings,
        transcriber_factory=FakeTranscriber,
        start_worker=True,
    )

    with TestClient(first_application):
        with pytest.raises(RuntimeError, match="Another transcription worker"):
            with TestClient(second_application):
                pass
