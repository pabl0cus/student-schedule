from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from app.schemas import (
    MAX_TRANSCRIPT_SEGMENT_TEXT_LENGTH,
    MAX_TRANSCRIPT_SEGMENTS,
    MAX_TRANSCRIPT_WORD_LENGTH,
    MAX_TRANSCRIPT_WORDS,
    CommunityJobClaim,
    CommunityJobFailure,
    CommunityJobLease,
    CommunityJobRelease,
    CommunityJobResult,
    CommunityWorkerCreate,
    CommunityWorkerCreated,
    TranscriptionPayload,
    TranscriptSegment,
    TranscriptWord,
)
from pydantic import ValidationError


def _segment(*, segment_id: int = 0, start: float = 1.0, end: float = 2.0) -> dict[str, object]:
    return {
        "id": segment_id,
        "start": start,
        "end": end,
        "text": "Вітаю на парі.",
        "words": [
            {"start": start, "end": end, "word": "Вітаю", "probability": 0.99},
        ],
    }


def test_transcription_payload_accepts_bounded_monotonic_timestamps() -> None:
    payload = TranscriptionPayload(
        transcript=[_segment(), _segment(segment_id=1, start=2.5, end=3.0)],
        duration_seconds=3.0,
        detected_language="uk",
        language_probability=0.99,
    )

    assert len(payload.transcript) == 2
    assert payload.transcript[0].text == "Вітаю на парі."


@pytest.mark.parametrize(
    "payload, error",
    (
        (
            {"transcript": [_segment(), _segment(segment_id=1, start=0.5, end=3.0)]},
            "segment timestamps must be monotonic",
        ),
        (
            {"transcript": [_segment()], "duration_seconds": 1.5},
            "segment timestamps must not exceed duration_seconds",
        ),
        (
            {"transcript": [_segment(), _segment(segment_id=0, start=2.5, end=3.0)]},
            "segment ids must be strictly increasing",
        ),
    ),
)
def test_transcription_payload_rejects_invalid_global_bounds(payload: dict[str, object], error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        TranscriptionPayload.model_validate(payload)


def test_transcript_word_timestamps_must_be_monotonic_and_inside_segment() -> None:
    outside = _segment()
    outside["words"] = [{"start": 0.5, "end": 1.5, "word": "слово"}]
    with pytest.raises(ValidationError, match="within their segment"):
        TranscriptSegment.model_validate(outside)

    non_monotonic = _segment()
    non_monotonic["words"] = [
        {"start": 1.5, "end": 1.7, "word": "перше"},
        {"start": 1.4, "end": 1.8, "word": "друге"},
    ]
    with pytest.raises(ValidationError, match="word timestamps must be monotonic"):
        TranscriptSegment.model_validate(non_monotonic)


def test_transcript_shape_and_text_are_bounded_and_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="at most 256 characters"):
        TranscriptWord(start=0, end=1, word="x" * (MAX_TRANSCRIPT_WORD_LENGTH + 1))
    with pytest.raises(ValidationError, match="at most 8192 characters"):
        TranscriptSegment(id=0, start=0, end=1, text="x" * (MAX_TRANSCRIPT_SEGMENT_TEXT_LENGTH + 1))
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TranscriptionPayload.model_validate({"transcript": [], "unexpected": True})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TranscriptSegment.model_validate({**_segment(), "unexpected": True})


def test_transcript_segment_and_total_word_counts_are_bounded() -> None:
    segment = TranscriptSegment.model_validate(_segment())
    with pytest.raises(ValidationError, match=rf"at most {MAX_TRANSCRIPT_SEGMENTS} items"):
        TranscriptionPayload(transcript=[segment] * (MAX_TRANSCRIPT_SEGMENTS + 1))

    first_word = TranscriptWord(start=1, end=2, word="слово")
    second_word = TranscriptWord(start=3, end=4, word="слово")
    first = TranscriptSegment(
        id=0,
        start=1,
        end=2,
        text="Перший сегмент",
        words=[first_word] * (MAX_TRANSCRIPT_WORDS // 2 + 1),
    )
    second = TranscriptSegment(
        id=1,
        start=3,
        end=4,
        text="Другий сегмент",
        words=[second_word] * (MAX_TRANSCRIPT_WORDS // 2 + 1),
    )
    with pytest.raises(ValidationError, match=rf"at most {MAX_TRANSCRIPT_WORDS} words"):
        TranscriptionPayload(transcript=[first, second], duration_seconds=4)


def test_community_worker_schemas_are_strict_and_token_is_flat() -> None:
    request = CommunityWorkerCreate(label="  Main RTX 3090  ")
    created = CommunityWorkerCreated(
        id="A" * 16,
        label=request.label,
        created_at=datetime.now(timezone.utc),
        token=f"sst_wk_v1_{'A' * 16}.{'B' * 43}",
    )

    assert created.label == "Main RTX 3090"
    assert created.token.startswith("sst_wk_v1_")
    assert "token" in created.model_dump()
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CommunityWorkerCreate.model_validate({"label": "GPU", "admin": True})


def test_community_job_contract_accepts_only_relative_media_paths() -> None:
    job_id = uuid4()
    lease = CommunityJobLease.model_validate(
        {
            "protocol_version": 1,
            "job_id": str(job_id),
            "lease_token": "L" * 43,
            "lease_expires_at": datetime.now(timezone.utc).isoformat(),
            "media_path": f"/community/v1/jobs/{job_id}/media",
            "media": {"size_bytes": 1024, "content_type": "audio/ogg", "sha256": "a" * 64},
            "transcription": {
                "model": "large-v3",
                "language": "uk",
                "beam_size": 5,
                "vad_filter": True,
                "word_timestamps": True,
                "initial_prompt": "Українська лекція",
            },
        }
    )

    assert lease.job_id == job_id
    assert lease.media.content_type == "audio/ogg"
    assert CommunityJobClaim(client_id=uuid4(), client_version="0.1.0").protocol_version == 1

    for unsafe_path in (
        "https://attacker.example/audio",
        "//attacker.example/audio",
        "/community/v1/../admin",
        "/community/v1/jobs\\outside",
    ):
        with pytest.raises(ValidationError, match="safe relative community API path"):
            CommunityJobLease.model_validate({**lease.model_dump(mode="json"), "media_path": unsafe_path})

    for unsupported_options in ({"beam_size": 4}, {"vad_filter": False}):
        with pytest.raises(ValidationError):
            CommunityJobLease.model_validate(
                {
                    **lease.model_dump(mode="json"),
                    "transcription": {
                        **lease.transcription.model_dump(mode="json"),
                        **unsupported_options,
                    },
                }
            )


def test_community_result_failure_and_release_are_strict() -> None:
    result = CommunityJobResult.model_validate(
        {
            "protocol_version": 1,
            "submission_id": str(uuid4()),
            "engine": "faster-whisper",
            "model": "large-v3",
            "engine_version": "1.2.1",
            "client_version": "0.1.0",
            "transcript": [_segment()],
            "duration_seconds": 2.0,
            "detected_language": "uk",
            "language_probability": 0.99,
        }
    )
    failure = CommunityJobFailure(code="out_of_memory", message="GPU memory exhausted")
    release = CommunityJobRelease(code="outside_schedule")

    assert result.transcript[0].words[0].word == "Вітаю"
    assert failure.code == "out_of_memory"
    assert release.code == "outside_schedule"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CommunityJobFailure.model_validate({"code": "cancelled", "retryable": False})
