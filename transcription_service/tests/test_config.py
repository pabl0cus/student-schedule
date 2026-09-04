from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Settings


def test_default_recording_and_attachment_limits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRANSCRIPTION_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TRANSCRIPTION_MAX_UPLOAD_BYTES", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_MAX_ATTACHMENT_BYTES", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_MAX_JSON_BYTES", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_MAX_WORKER_RESULT_BYTES", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_LOCAL_WORKER_ENABLED", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_WORKER_TOKEN_PEPPER", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_COMMUNITY_LEASE_SECONDS", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_COMMUNITY_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_AUDIO_PREPARATION_ENABLED", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_AUDIO_PREPARATION_POLL_SECONDS", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_AUDIO_PREPARATION_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_AUDIO_PREPARATION_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_MAX_PREPARED_AUDIO_BYTES", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_FFMPEG_BINARY", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_SECURE_COOKIES", raising=False)

    settings = Settings.from_env()

    assert settings.max_upload_bytes == 500 * 1024 * 1024
    assert settings.max_attachment_bytes == 100 * 1024 * 1024
    assert settings.max_json_bytes == 1024 * 1024
    assert settings.max_worker_result_bytes == 8 * 1024 * 1024
    assert settings.local_worker_enabled is False
    assert settings.worker_token_pepper is None
    assert settings.community_lease_seconds == 15 * 60
    assert settings.community_max_attempts == 3
    assert settings.audio_preparation_enabled is True
    assert settings.audio_preparation_poll_seconds == 1.0
    assert settings.audio_preparation_max_attempts == 3
    assert settings.audio_preparation_timeout_seconds == 6 * 60 * 60
    assert settings.max_prepared_audio_bytes == 500 * 1024 * 1024
    assert settings.ffmpeg_binary == "ffmpeg"
    assert settings.secure_cookies is False


def test_recording_and_attachment_limits_remain_configurable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRANSCRIPTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRANSCRIPTION_MAX_UPLOAD_BYTES", "123")
    monkeypatch.setenv("TRANSCRIPTION_MAX_ATTACHMENT_BYTES", "45")
    monkeypatch.setenv("TRANSCRIPTION_MAX_JSON_BYTES", "67")
    monkeypatch.setenv("TRANSCRIPTION_MAX_WORKER_RESULT_BYTES", "890")
    monkeypatch.setenv("TRANSCRIPTION_LOCAL_WORKER_ENABLED", "true")
    monkeypatch.setenv("TRANSCRIPTION_WORKER_TOKEN_PEPPER", "test-worker-token-pepper")
    monkeypatch.setenv("TRANSCRIPTION_COMMUNITY_LEASE_SECONDS", "321")
    monkeypatch.setenv("TRANSCRIPTION_COMMUNITY_MAX_ATTEMPTS", "7")
    monkeypatch.setenv("TRANSCRIPTION_AUDIO_PREPARATION_ENABLED", "false")
    monkeypatch.setenv("TRANSCRIPTION_AUDIO_PREPARATION_POLL_SECONDS", "2.5")
    monkeypatch.setenv("TRANSCRIPTION_AUDIO_PREPARATION_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("TRANSCRIPTION_AUDIO_PREPARATION_TIMEOUT_SECONDS", "123.5")
    monkeypatch.setenv("TRANSCRIPTION_MAX_PREPARED_AUDIO_BYTES", "456")
    monkeypatch.setenv("TRANSCRIPTION_FFMPEG_BINARY", "custom-ffmpeg")
    monkeypatch.setenv("TRANSCRIPTION_SECURE_COOKIES", "true")

    settings = Settings.from_env()

    assert settings.max_upload_bytes == 123
    assert settings.max_attachment_bytes == 45
    assert settings.max_json_bytes == 67
    assert settings.max_worker_result_bytes == 890
    assert settings.local_worker_enabled is True
    assert settings.worker_token_pepper == "test-worker-token-pepper"
    assert settings.community_lease_seconds == 321
    assert settings.community_max_attempts == 7
    assert settings.audio_preparation_enabled is False
    assert settings.audio_preparation_poll_seconds == 2.5
    assert settings.audio_preparation_max_attempts == 4
    assert settings.audio_preparation_timeout_seconds == 123.5
    assert settings.max_prepared_audio_bytes == 456
    assert settings.ffmpeg_binary == "custom-ffmpeg"
    assert settings.secure_cookies is True


def test_invalid_secure_cookie_setting_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRANSCRIPTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRANSCRIPTION_SECURE_COOKIES", "sometimes")

    with pytest.raises(ValueError, match="TRANSCRIPTION_SECURE_COOKIES must be a boolean"):
        Settings.from_env()


def test_invalid_local_worker_setting_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRANSCRIPTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRANSCRIPTION_LOCAL_WORKER_ENABLED", "sometimes")

    with pytest.raises(ValueError, match="TRANSCRIPTION_LOCAL_WORKER_ENABLED must be a boolean"):
        Settings.from_env()


def test_invalid_audio_preparation_setting_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRANSCRIPTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TRANSCRIPTION_AUDIO_PREPARATION_ENABLED", "sometimes")

    with pytest.raises(ValueError, match="TRANSCRIPTION_AUDIO_PREPARATION_ENABLED must be a boolean"):
        Settings.from_env()


def test_prepared_audio_limit_cannot_exceed_protocol_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="500 MiB community protocol limit"):
        Settings(data_dir=tmp_path, max_prepared_audio_bytes=500 * 1024 * 1024 + 1)


@pytest.mark.parametrize(
    "name",
    (
        "TRANSCRIPTION_MAX_WORKER_RESULT_BYTES",
        "TRANSCRIPTION_COMMUNITY_LEASE_SECONDS",
        "TRANSCRIPTION_COMMUNITY_MAX_ATTEMPTS",
        "TRANSCRIPTION_AUDIO_PREPARATION_MAX_ATTEMPTS",
        "TRANSCRIPTION_MAX_PREPARED_AUDIO_BYTES",
    ),
)
def test_invalid_community_positive_integer_setting_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
) -> None:
    monkeypatch.setenv("TRANSCRIPTION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValueError, match=rf"{name} must be greater than zero"):
        Settings.from_env()
