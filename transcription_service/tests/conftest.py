from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import hash_password
from app.config import Settings
from app.main import create_app
from app.middleware import LOCAL_REQUEST_HEADER, LOCAL_REQUEST_HEADER_VALUE


LOCAL_REQUEST_HEADERS = {
    "Origin": "http://localhost:3000",
    LOCAL_REQUEST_HEADER: LOCAL_REQUEST_HEADER_VALUE,
}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=32,
        max_attachment_bytes=16,
        upload_chunk_bytes=4,
        worker_poll_seconds=0.01,
        audio_preparation_enabled=False,
        whisper_model="mock",
        whisper_device="cpu",
        whisper_compute_type="int8",
        allowed_origins=("http://localhost:3000",),
        admin_username="local-admin",
        admin_password_hash=hash_password("correct horse battery staple", salt=b"0123456789abcdef"),
        admin_session_secret="test_session_secret_0123456789abcdefghijklmnopqrstuvwxyz",
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    application = create_app(settings=settings, start_worker=False)
    with TestClient(application) as test_client:
        test_client.headers.update(LOCAL_REQUEST_HEADERS)
        yield test_client
