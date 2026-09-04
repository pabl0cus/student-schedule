from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

COMMUNITY_MEDIA_PROTOCOL_MAX_BYTES = 500 * 1024 * 1024


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc

    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _positive_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc

    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int = 500 * 1024 * 1024
    max_attachment_bytes: int = 100 * 1024 * 1024
    max_json_bytes: int = 1024 * 1024
    max_worker_result_bytes: int = 8 * 1024 * 1024
    upload_chunk_bytes: int = 1024 * 1024
    worker_poll_seconds: float = 1.0
    local_worker_enabled: bool = False
    worker_token_pepper: str | None = None
    community_lease_seconds: int = 15 * 60
    community_max_attempts: int = 3
    audio_preparation_enabled: bool = True
    audio_preparation_poll_seconds: float = 1.0
    audio_preparation_max_attempts: int = 3
    audio_preparation_timeout_seconds: float = 6 * 60 * 60
    max_prepared_audio_bytes: int = 500 * 1024 * 1024
    ffmpeg_binary: str = "ffmpeg"
    whisper_model: str = "large-v3"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "float16"
    allowed_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    )
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    admin_username: str | None = None
    admin_password_hash: str | None = None
    admin_session_secret: str | None = None
    secure_cookies: bool = False

    def __post_init__(self) -> None:
        if self.max_prepared_audio_bytes > COMMUNITY_MEDIA_PROTOCOL_MAX_BYTES:
            raise ValueError(
                "TRANSCRIPTION_MAX_PREPARED_AUDIO_BYTES must not exceed the 500 MiB community protocol limit"
            )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "transcriptions.sqlite3"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def prepared_audio_dir(self) -> Path:
        return self.data_dir / "prepared-audio"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def worker_lock_path(self) -> Path:
        return self.data_dir / "transcription-worker.lock"

    @property
    def audio_preparation_lock_path(self) -> Path:
        return self.data_dir / "audio-preparation-worker.lock"

    @property
    def max_request_bytes(self) -> int:
        # Multipart boundaries and the small metadata fields are not part of the media size limit.
        return self.max_upload_bytes + 1024 * 1024

    @property
    def max_attachment_request_bytes(self) -> int:
        # Keep the attachment request guard separate from the larger recording upload allowance.
        return self.max_attachment_bytes + 1024 * 1024

    @classmethod
    def from_env(cls) -> Settings:
        default_data_dir = Path(__file__).resolve().parents[1] / "data"
        origins = tuple(
            origin.strip()
            for origin in os.getenv(
                "TRANSCRIPTION_ALLOWED_ORIGINS",
                (
                    "http://localhost:3000,http://127.0.0.1:3000,"
                    "http://localhost:3001,http://127.0.0.1:3001"
                ),
            ).split(",")
            if origin.strip()
        )
        hosts = tuple(
            host.strip()
            for host in os.getenv(
                "TRANSCRIPTION_ALLOWED_HOSTS",
                "localhost,127.0.0.1,testserver",
            ).split(",")
            if host.strip()
        )

        return cls(
            data_dir=Path(os.getenv("TRANSCRIPTION_DATA_DIR", default_data_dir)).expanduser().resolve(),
            max_upload_bytes=_positive_int("TRANSCRIPTION_MAX_UPLOAD_BYTES", 500 * 1024 * 1024),
            max_attachment_bytes=_positive_int(
                "TRANSCRIPTION_MAX_ATTACHMENT_BYTES",
                100 * 1024 * 1024,
            ),
            max_json_bytes=_positive_int("TRANSCRIPTION_MAX_JSON_BYTES", 1024 * 1024),
            max_worker_result_bytes=_positive_int(
                "TRANSCRIPTION_MAX_WORKER_RESULT_BYTES",
                8 * 1024 * 1024,
            ),
            upload_chunk_bytes=_positive_int("TRANSCRIPTION_UPLOAD_CHUNK_BYTES", 1024 * 1024),
            worker_poll_seconds=_positive_float("TRANSCRIPTION_WORKER_POLL_SECONDS", 1.0),
            local_worker_enabled=_boolean("TRANSCRIPTION_LOCAL_WORKER_ENABLED", False),
            worker_token_pepper=os.getenv("TRANSCRIPTION_WORKER_TOKEN_PEPPER") or None,
            community_lease_seconds=_positive_int("TRANSCRIPTION_COMMUNITY_LEASE_SECONDS", 15 * 60),
            community_max_attempts=_positive_int("TRANSCRIPTION_COMMUNITY_MAX_ATTEMPTS", 3),
            audio_preparation_enabled=_boolean("TRANSCRIPTION_AUDIO_PREPARATION_ENABLED", True),
            audio_preparation_poll_seconds=_positive_float(
                "TRANSCRIPTION_AUDIO_PREPARATION_POLL_SECONDS", 1.0
            ),
            audio_preparation_max_attempts=_positive_int(
                "TRANSCRIPTION_AUDIO_PREPARATION_MAX_ATTEMPTS", 3
            ),
            audio_preparation_timeout_seconds=_positive_float(
                "TRANSCRIPTION_AUDIO_PREPARATION_TIMEOUT_SECONDS", 6 * 60 * 60
            ),
            max_prepared_audio_bytes=_positive_int(
                "TRANSCRIPTION_MAX_PREPARED_AUDIO_BYTES", 500 * 1024 * 1024
            ),
            ffmpeg_binary=os.getenv("TRANSCRIPTION_FFMPEG_BINARY", "ffmpeg").strip() or "ffmpeg",
            whisper_model=os.getenv("WHISPER_MODEL", "large-v3"),
            whisper_device=os.getenv("WHISPER_DEVICE", "cuda"),
            whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "float16"),
            allowed_origins=origins,
            allowed_hosts=hosts,
            admin_username=os.getenv("TRANSCRIPTION_ADMIN_USERNAME") or None,
            admin_password_hash=os.getenv("TRANSCRIPTION_ADMIN_PASSWORD_HASH") or None,
            admin_session_secret=os.getenv("TRANSCRIPTION_ADMIN_SESSION_SECRET") or None,
            secure_cookies=_boolean("TRANSCRIPTION_SECURE_COOKIES", False),
        )
