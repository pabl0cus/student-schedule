from __future__ import annotations

from pathlib import Path
from threading import Event

import httpx
import pytest
from test_api_client import job_payload

from student_schedule_worker.api.dto import JobLease
from student_schedule_worker.api.errors import ApiError, MediaIntegrityError, OperationCancelled
from student_schedule_worker.engine.whisper import EngineResult, TranscriptionCancelled
from student_schedule_worker.runtime.job_runner import JobOutcome, JobRunner


class FakeHeartbeat:
    def __init__(self, _api, _lease):
        self.lost = Event()
        self.stages: list[str] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def set_stage(self, stage: str) -> None:
        self.stages.append(stage)


class FakeApi:
    def __init__(self, *, fail_first_upload: bool = False):
        self.releases: list[str] = []
        self.submissions: list[dict[str, object]] = []
        self.fail_first_upload = fail_first_upload

    def download_media(self, _lease, destination: Path, *, should_continue) -> None:
        if not should_continue():
            raise OperationCancelled("closed")
        destination.write_bytes(b"audio")

    def submit_result(self, _lease, payload) -> None:
        self.submissions.append(dict(payload))
        if self.fail_first_upload and len(self.submissions) == 1:
            raise httpx.ConnectError(
                "temporary network failure",
                request=httpx.Request("PUT", "https://schedule.example/community/v1/jobs/id/result"),
            )

    def release(self, _lease, *, code: str, message: str | None = None) -> None:
        self.releases.append(code)


class SuccessfulEngine:
    engine_version = "1.2.1"

    def transcribe(self, _path, _options, *, should_continue):
        assert should_continue()
        return EngineResult([], 12.0, "uk", 0.99)


def lease() -> JobLease:
    return JobLease.from_dict(job_payload())


def test_completed_result_retries_with_same_submission_id(tmp_path: Path) -> None:
    api = FakeApi(fail_first_upload=True)
    statuses = []
    runner = JobRunner(
        api=api,
        engine=SuccessfulEngine(),
        jobs_dir=tmp_path,
        status_callback=statuses.append,
        heartbeat_factory=FakeHeartbeat,
        upload_retry_seconds=0,
    )
    outcome = runner.run(lease(), stop_event=Event(), rendering_allowed=lambda: True)
    assert outcome is JobOutcome.COMPLETED
    assert len(api.submissions) == 2
    assert api.submissions[0]["submission_id"] == api.submissions[1]["submission_id"]
    assert api.submissions[0]["protocol_version"] == 1
    assert api.submissions[0]["engine"] == "faster-whisper"
    assert api.submissions[0]["model"] == "large-v3"
    assert api.submissions[0]["engine_version"] == "1.2.1"
    assert api.submissions[0]["client_version"] == "0.1.0"
    assert not api.releases
    assert not (tmp_path / lease().job_id).exists()


def test_schedule_closure_aborts_and_releases_partial_job(tmp_path: Path) -> None:
    class CancelledEngine:
        engine_version = "1.2.1"

        def transcribe(self, _path, _options, *, should_continue):
            raise TranscriptionCancelled("closed")

    api = FakeApi()
    runner = JobRunner(
        api=api,
        engine=CancelledEngine(),
        jobs_dir=tmp_path,
        status_callback=lambda _status: None,
        heartbeat_factory=FakeHeartbeat,
    )
    assert runner.run(lease(), stop_event=Event(), rendering_allowed=lambda: True) is JobOutcome.ABORTED
    assert api.releases == ["outside_schedule"]
    assert not api.submissions


def test_schedule_closure_after_result_does_not_discard_upload(tmp_path: Path) -> None:
    allowed = True

    class ClosingEngine:
        engine_version = "1.2.1"

        def transcribe(self, _path, _options, *, should_continue):
            nonlocal allowed
            assert should_continue()
            allowed = False
            return EngineResult([], 12.0, "uk", 0.99)

    api = FakeApi()
    runner = JobRunner(
        api=api,
        engine=ClosingEngine(),
        jobs_dir=tmp_path,
        status_callback=lambda _status: None,
        heartbeat_factory=FakeHeartbeat,
    )
    assert runner.run(lease(), stop_event=Event(), rendering_allowed=lambda: allowed) is JobOutcome.COMPLETED
    assert len(api.submissions) == 1
    assert not api.releases


def test_app_shutdown_uses_shutdown_release_code(tmp_path: Path) -> None:
    api = FakeApi()
    runner = JobRunner(
        api=api,
        engine=SuccessfulEngine(),
        jobs_dir=tmp_path,
        status_callback=lambda _status: None,
        heartbeat_factory=FakeHeartbeat,
    )
    stopped = Event()
    stopped.set()
    assert runner.run(lease(), stop_event=stopped, rendering_allowed=lambda: True) is JobOutcome.ABORTED
    assert api.releases == ["shutdown"]


def test_engine_exception_uses_transcription_failed_code(tmp_path: Path) -> None:
    class FailingEngine:
        engine_version = "1.2.1"

        def transcribe(self, _path, _options, *, should_continue):
            raise RuntimeError("decoder failed")

    api = FakeApi()
    runner = JobRunner(
        api=api,
        engine=FailingEngine(),
        jobs_dir=tmp_path,
        status_callback=lambda _status: None,
        heartbeat_factory=FakeHeartbeat,
    )
    assert runner.run(lease(), stop_event=Event(), rendering_allowed=lambda: True) is JobOutcome.FAILED
    assert api.releases == ["transcription_failed"]


def test_media_hash_mismatch_releases_invalid_media_without_running_whisper(tmp_path: Path) -> None:
    class CorruptApi(FakeApi):
        def download_media(self, _lease, destination: Path, *, should_continue) -> None:
            raise MediaIntegrityError("hash mismatch")

    class UnexpectedEngine:
        engine_version = "1.2.1"

        def transcribe(self, _path, _options, *, should_continue):
            raise AssertionError("Whisper must not run for corrupt media")

    api = CorruptApi()
    runner = JobRunner(
        api=api,
        engine=UnexpectedEngine(),
        jobs_dir=tmp_path,
        status_callback=lambda _status: None,
        heartbeat_factory=FakeHeartbeat,
    )

    assert runner.run(lease(), stop_event=Event(), rendering_allowed=lambda: True) is JobOutcome.FAILED
    assert api.releases == ["invalid_media"]
    assert not api.submissions


def test_permanent_result_rejection_is_not_retried(tmp_path: Path) -> None:
    class RejectedApi(FakeApi):
        def submit_result(self, _lease, payload) -> None:
            self.submissions.append(dict(payload))
            raise ApiError("invalid result", status_code=422)

    api = RejectedApi()
    runner = JobRunner(
        api=api,
        engine=SuccessfulEngine(),
        jobs_dir=tmp_path,
        status_callback=lambda _status: None,
        heartbeat_factory=FakeHeartbeat,
        upload_retry_seconds=0,
    )
    assert runner.run(lease(), stop_event=Event(), rendering_allowed=lambda: True) is JobOutcome.FAILED
    assert len(api.submissions) == 1
    assert api.releases == ["internal_error"]


def test_transient_upload_retries_are_bounded_and_idempotent(tmp_path: Path) -> None:
    class OfflineApi(FakeApi):
        def submit_result(self, _lease, payload) -> None:
            self.submissions.append(dict(payload))
            raise httpx.ReadTimeout(
                "server did not answer",
                request=httpx.Request("PUT", "https://schedule.example/community/v1/jobs/id/result"),
            )

    api = OfflineApi()
    runner = JobRunner(
        api=api,
        engine=SuccessfulEngine(),
        jobs_dir=tmp_path,
        status_callback=lambda _status: None,
        heartbeat_factory=FakeHeartbeat,
        upload_retry_seconds=0,
        max_upload_attempts=3,
    )
    assert runner.run(lease(), stop_event=Event(), rendering_allowed=lambda: True) is JobOutcome.FAILED
    assert len(api.submissions) == 3
    assert len({submission["submission_id"] for submission in api.submissions}) == 1
    assert api.releases == ["internal_error"]


def test_shutdown_breaks_transient_upload_retry(tmp_path: Path) -> None:
    stop_event = Event()

    class StoppingApi(FakeApi):
        def submit_result(self, _lease, payload) -> None:
            self.submissions.append(dict(payload))
            stop_event.set()
            raise httpx.ConnectError(
                "network lost during shutdown",
                request=httpx.Request("PUT", "https://schedule.example/community/v1/jobs/id/result"),
            )

    api = StoppingApi()
    runner = JobRunner(
        api=api,
        engine=SuccessfulEngine(),
        jobs_dir=tmp_path,
        status_callback=lambda _status: None,
        heartbeat_factory=FakeHeartbeat,
        upload_retry_seconds=30,
    )
    assert runner.run(lease(), stop_event=stop_event, rendering_allowed=lambda: True) is JobOutcome.ABORTED
    assert len(api.submissions) == 1
    assert api.releases == ["shutdown"]


def test_upload_attempt_count_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        JobRunner(
            api=FakeApi(),
            engine=SuccessfulEngine(),
            jobs_dir=tmp_path,
            status_callback=lambda _status: None,
            heartbeat_factory=FakeHeartbeat,
            max_upload_attempts=0,
        )
