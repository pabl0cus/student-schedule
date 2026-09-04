from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from ..constants import WHISPER_LANGUAGE, WHISPER_MODEL
from .errors import ProtocolError


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{field} must be an object")
    return value


def _required_string(value: object, field: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ProtocolError(f"{field} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class MediaDescriptor:
    size_bytes: int
    content_type: str
    sha256: str

    @classmethod
    def from_dict(cls, raw: object) -> MediaDescriptor:
        value = _object(raw, "media")
        size = value.get("size_bytes")
        if type(size) is not int or size <= 0:
            raise ProtocolError("media.size_bytes is invalid")
        content_type = _required_string(value.get("content_type"), "media.content_type", maximum=255)
        if content_type.lower() != "audio/ogg":
            raise ProtocolError("worker accepts normalized Ogg audio only")
        sha256 = _required_string(value.get("sha256"), "media.sha256", maximum=64)
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ProtocolError("media.sha256 is invalid")
        return cls(size_bytes=size, content_type=content_type, sha256=sha256)


@dataclass(frozen=True, slots=True)
class TranscriptionOptions:
    model: str
    language: str
    beam_size: int
    vad_filter: bool
    word_timestamps: bool
    initial_prompt: str | None

    @classmethod
    def from_dict(cls, raw: object) -> TranscriptionOptions:
        value = _object(raw, "transcription")
        model = _required_string(value.get("model"), "transcription.model", maximum=128)
        language = _required_string(value.get("language"), "transcription.language", maximum=32)
        beam_size = value.get("beam_size")
        vad_filter = value.get("vad_filter")
        word_timestamps = value.get("word_timestamps")
        prompt = value.get("initial_prompt")
        if model != WHISPER_MODEL or language != WHISPER_LANGUAGE:
            raise ProtocolError("server requested an unsupported model or language")
        if beam_size != 5 or type(vad_filter) is not bool or type(word_timestamps) is not bool:
            raise ProtocolError("server returned invalid transcription options")
        if not vad_filter or not word_timestamps:
            raise ProtocolError("VAD and word timestamps must stay enabled")
        if prompt is not None and (not isinstance(prompt, str) or len(prompt) > 512):
            raise ProtocolError("transcription.initial_prompt is invalid")
        return cls(
            model=model,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            word_timestamps=word_timestamps,
            initial_prompt=prompt,
        )


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: str
    lease_token: str
    lease_expires_at: datetime
    media_path: str
    media: MediaDescriptor
    transcription: TranscriptionOptions

    @classmethod
    def from_dict(cls, raw: object) -> JobLease:
        value = _object(raw, "job")
        if value.get("protocol_version") != 1 or type(value.get("protocol_version")) is not int:
            raise ProtocolError("unsupported job protocol version")
        job_id = _required_string(value.get("job_id"), "job_id", maximum=64)
        try:
            UUID(job_id)
        except ValueError as exc:
            raise ProtocolError("job_id must be a UUID") from exc
        lease_token = _required_string(value.get("lease_token"), "lease_token", maximum=128)
        if len(lease_token) < 43 or not all(character.isalnum() or character in "_-" for character in lease_token):
            raise ProtocolError("lease_token is invalid")
        expires_raw = _required_string(value.get("lease_expires_at"), "lease_expires_at", maximum=64)
        try:
            expires = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProtocolError("lease_expires_at is invalid") from exc
        if expires.tzinfo is None:
            raise ProtocolError("lease_expires_at must contain a timezone")
        media_path = _required_string(value.get("media_path"), "media_path", maximum=1024)
        return cls(
            job_id=job_id,
            lease_token=lease_token,
            lease_expires_at=expires,
            media_path=media_path,
            media=MediaDescriptor.from_dict(value.get("media")),
            transcription=TranscriptionOptions.from_dict(value.get("transcription")),
        )
