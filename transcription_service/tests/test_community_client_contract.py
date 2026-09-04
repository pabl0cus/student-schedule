from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from app.schemas import CommunityJobLease, CommunityJobResult

CLIENT_SOURCE = Path(__file__).resolve().parents[2] / "community-transcriber-windows" / "src"
if not CLIENT_SOURCE.is_dir():
    pytest.skip("standalone Windows client is not present in this checkout", allow_module_level=True)
sys.path.insert(0, str(CLIENT_SOURCE))

from student_schedule_worker import __version__ as client_version
from student_schedule_worker.api.dto import JobLease
from student_schedule_worker.constants import (
    PROTOCOL_VERSION,
    WHISPER_MODEL,
)


def test_windows_client_and_server_share_the_v1_job_contract() -> None:
    job_id = uuid4()
    server_lease = CommunityJobLease(
        job_id=job_id,
        lease_token="L" * 43,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        media_path=f"/community/v1/jobs/{job_id}/media",
        media={"size_bytes": 4096, "content_type": "audio/ogg", "sha256": "a" * 64},
        transcription={
            "model": "large-v3",
            "language": "uk",
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps": True,
            "initial_prompt": "Українська лекція",
        },
    )

    client_lease = JobLease.from_dict(server_lease.model_dump(mode="json"))
    assert client_lease.job_id == str(job_id)
    assert client_lease.media.sha256 == "a" * 64
    assert client_lease.transcription.language == "uk"

    client_result = {
        "protocol_version": PROTOCOL_VERSION,
        "submission_id": str(uuid4()),
        "engine": "faster-whisper",
        "model": WHISPER_MODEL,
        "engine_version": "1.2.1",
        "client_version": client_version,
        "transcript": [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.0,
                "text": "Вітаю.",
                "words": [],
            }
        ],
        "duration_seconds": 1.0,
        "detected_language": "uk",
        "language_probability": 0.99,
    }

    validated = CommunityJobResult.model_validate(client_result)
    assert validated.protocol_version == 1
    assert validated.engine == "faster-whisper"
    assert validated.model == "large-v3"
