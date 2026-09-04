from __future__ import annotations

import unicodedata
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .config import COMMUNITY_MEDIA_PROTOCOL_MAX_BYTES

RecordingStatus = Literal["queued", "processing", "ready", "failed"]
ScheduleScopeType = Literal["group", "lecturer"]
RecordingSearchScope = Literal["all", "subject", "lecturer", "transcript"]
RecordingSearchField = Literal["lesson_title", "lecturer_name", "transcript"]
AttachmentSearchField = Literal["lesson_title", "file_name"]
MAX_TRANSCRIPT_DURATION_SECONDS = 24 * 60 * 60
MAX_TRANSCRIPT_SEGMENTS = 20_000
MAX_TRANSCRIPT_WORDS = 100_000
MAX_TRANSCRIPT_SEGMENT_TEXT_LENGTH = 8_192
MAX_TRANSCRIPT_WORD_LENGTH = 256
MAX_COMMUNITY_MEDIA_BYTES = COMMUNITY_MEDIA_PROTOCOL_MAX_BYTES
MAX_UPLOAD_LESSON_KEYS = 8
MAX_UPLOAD_LESSON_KEY_LENGTH = 4_096


def _normalized_text(value: str, *, field_name: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise ValueError(f"{field_name} contains control characters")
    return normalized


class TranscriptWord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0, le=MAX_TRANSCRIPT_DURATION_SECONDS, allow_inf_nan=False)
    end: float = Field(ge=0, le=MAX_TRANSCRIPT_DURATION_SECONDS, allow_inf_nan=False)
    word: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_WORD_LENGTH)
    probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @field_validator("word")
    @classmethod
    def normalize_word(cls, value: str) -> str:
        return _normalized_text(value, field_name="word")

    @model_validator(mode="after")
    def validate_time_range(self) -> TranscriptWord:
        if self.end < self.start:
            raise ValueError("word end must not be earlier than start")
        return self


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)
    start: float = Field(ge=0, le=MAX_TRANSCRIPT_DURATION_SECONDS, allow_inf_nan=False)
    end: float = Field(ge=0, le=MAX_TRANSCRIPT_DURATION_SECONDS, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_SEGMENT_TEXT_LENGTH)
    words: list[TranscriptWord] = Field(default_factory=list, max_length=MAX_TRANSCRIPT_WORDS)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return _normalized_text(value, field_name="segment text")

    @model_validator(mode="after")
    def validate_time_range(self) -> TranscriptSegment:
        if self.end < self.start:
            raise ValueError("segment end must not be earlier than start")

        previous_start = self.start
        previous_end = self.start
        for word in self.words:
            if word.start < self.start or word.end > self.end:
                raise ValueError("word timestamps must stay within their segment")
            if word.start < previous_start or word.end < previous_end:
                raise ValueError("word timestamps must be monotonic")
            previous_start = word.start
            previous_end = word.end
        return self


class TranscriptionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transcript: list[TranscriptSegment] = Field(default_factory=list, max_length=MAX_TRANSCRIPT_SEGMENTS)
    duration_seconds: float | None = Field(
        default=None,
        ge=0,
        le=MAX_TRANSCRIPT_DURATION_SECONDS,
        allow_inf_nan=False,
    )
    detected_language: str | None = Field(
        default=None,
        min_length=2,
        max_length=32,
        pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$",
    )
    language_probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_transcript_bounds(self) -> TranscriptionPayload:
        total_words = 0
        previous_id = -1
        previous_start = 0.0
        previous_end = 0.0

        for segment in self.transcript:
            if segment.id <= previous_id:
                raise ValueError("segment ids must be strictly increasing")
            if segment.start < previous_start or segment.end < previous_end:
                raise ValueError("segment timestamps must be monotonic")
            if self.duration_seconds is not None and segment.end > self.duration_seconds:
                raise ValueError("segment timestamps must not exceed duration_seconds")

            total_words += len(segment.words)
            if total_words > MAX_TRANSCRIPT_WORDS:
                raise ValueError(f"transcript must contain at most {MAX_TRANSCRIPT_WORDS} words")

            previous_id = segment.id
            previous_start = segment.start
            previous_end = segment.end
        return self


class CommunityWorkerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=128)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return _normalized_text(value, field_name="label")


class CommunityWorkerPublic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{16}$")
    label: str = Field(min_length=1, max_length=128)
    created_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None


class CommunityWorkerCreated(CommunityWorkerPublic):
    token: str = Field(
        min_length=70,
        max_length=70,
        pattern=r"^sst_wk_v1_[A-Za-z0-9_-]{16}\.[A-Za-z0-9_-]{43}$",
    )


class CommunityJobClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = 1
    client_id: UUID
    client_version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._+-]+$")


class CommunityJobMedia(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size_bytes: int = Field(gt=0, le=MAX_COMMUNITY_MEDIA_BYTES)
    content_type: str = Field(
        min_length=3,
        max_length=127,
        pattern=r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$",
    )
    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class CommunityTranscriptionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Literal["large-v3"] = "large-v3"
    language: Literal["uk"] = "uk"
    beam_size: Literal[5] = 5
    vad_filter: Literal[True] = True
    word_timestamps: Literal[True] = True
    initial_prompt: str | None = Field(default=None, max_length=512)

    @field_validator("initial_prompt")
    @classmethod
    def normalize_initial_prompt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalized_text(value, field_name="initial_prompt")


class CommunityJobLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = 1
    job_id: UUID
    lease_token: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    lease_expires_at: datetime
    media_path: str = Field(min_length=1, max_length=512)
    media: CommunityJobMedia
    transcription: CommunityTranscriptionOptions

    @field_validator("media_path")
    @classmethod
    def validate_media_path(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith("/community/v1/")
            or "\\" in parsed.path
            or "//" in parsed.path
            or any(part in (".", "..") for part in parsed.path.split("/"))
        ):
            raise ValueError("media_path must be a safe relative community API path")
        return parsed.path

    @field_validator("lease_expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("lease_expires_at must include a timezone")
        return value


class CommunityJobHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = 1
    progress: int | None = Field(default=None, ge=0, le=99)


class CommunityJobResult(TranscriptionPayload):
    protocol_version: Literal[1] = 1
    submission_id: UUID
    engine: Literal["faster-whisper"] = "faster-whisper"
    model: Literal["large-v3"] = "large-v3"
    engine_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._+-]+$",
    )
    client_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._+-]+$",
    )


CommunityJobStopCode = Literal[
    "outside_schedule",
    "shutdown",
    "cancelled",
    "download_failed",
    "invalid_media",
    "transcription_failed",
    "out_of_memory",
    "internal_error",
]


class CommunityJobFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = 1
    code: CommunityJobStopCode
    message: str | None = Field(default=None, max_length=2_000)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalized_text(value, field_name="message")


class CommunityJobRelease(CommunityJobFailure):
    pass


class RecordingSummary(BaseModel):
    id: str
    lesson_key: str
    lesson_title: str
    scope_label: str
    file_name: str
    mime_type: str
    original_filename: str
    content_type: str
    size_bytes: int
    status: RecordingStatus
    progress: int = Field(ge=0, le=100)
    attempts: int
    duration_seconds: float | None = None
    detected_language: str | None = None
    language: str | None = None
    language_probability: float | None = None
    error: str | None = None
    recorded_at: date
    created_at: datetime
    updated_at: datetime
    media_url: str


class RecordingDetail(RecordingSummary):
    transcript: list[TranscriptSegment] = Field(default_factory=list)


class RecordingContextGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    label: str = Field(min_length=1, max_length=200)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return _normalized_text(value, field_name="group label")


class RecordingUploadContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[RecordingContextGroup] = Field(default_factory=list, max_length=32)
    lecturer_name: str | None = Field(default=None, max_length=200)
    lesson_keys: list[str] = Field(default_factory=list, max_length=MAX_UPLOAD_LESSON_KEYS)

    @field_validator("lecturer_name")
    @classmethod
    def normalize_lecturer_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalized_text(value, field_name="lecturer name")

    @field_validator("lesson_keys")
    @classmethod
    def normalize_lesson_keys(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            lesson_key = _normalized_text(value, field_name="lesson key")
            if len(lesson_key) > MAX_UPLOAD_LESSON_KEY_LENGTH:
                raise ValueError("lesson key is too long")
            if lesson_key not in normalized:
                normalized.append(lesson_key)
        return normalized


class RecordingSearchSegment(BaseModel):
    id: int = Field(ge=0)
    start: float = Field(ge=0, le=MAX_TRANSCRIPT_DURATION_SECONDS, allow_inf_nan=False)
    end: float = Field(ge=0, le=MAX_TRANSCRIPT_DURATION_SECONDS, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=MAX_TRANSCRIPT_SEGMENT_TEXT_LENGTH)


class RecordingSearchResult(RecordingSummary):
    group_id: str
    group_label: str
    lecturer_name: str | None = None
    matched_fields: list[RecordingSearchField] = Field(default_factory=list)
    match_segment: RecordingSearchSegment | None = None


class RecordingSearchResponse(BaseModel):
    items: list[RecordingSearchResult]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class AttachmentSummary(BaseModel):
    id: str
    lesson_key: str
    lesson_title: str
    scope_label: str
    file_name: str
    mime_type: str
    original_filename: str
    content_type: str
    size_bytes: int = Field(gt=0)
    recorded_at: date
    created_at: datetime
    updated_at: datetime
    content_url: str


class AttachmentSearchResult(AttachmentSummary):
    group_id: str
    group_label: str
    matched_fields: list[AttachmentSearchField] = Field(default_factory=list)


class AttachmentSearchResponse(BaseModel):
    items: list[AttachmentSearchResult]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]
    worker: Literal["running", "stopped"]
    queue: dict[str, int]


class AdminLoginRequest(BaseModel):
    username: str = Field(max_length=128)
    password: str = Field(max_length=4096)


class AdminSessionResponse(BaseModel):
    authenticated: bool
    username: str | None = None


class ScheduleSnapshotDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: str = Field(min_length=1, max_length=32)
    pairs: list[dict[str, JsonValue]] = Field(max_length=64)


class ScheduleSnapshotSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schedule_week: Literal["firstWeek", "secondWeek"] = Field(alias="scheduleWeek")
    days: list[ScheduleSnapshotDay] = Field(max_length=7)


class ScheduleSnapshotWrite(BaseModel):
    scope_label: str = Field(min_length=1, max_length=200)
    schedule: ScheduleSnapshotSchedule

    @field_validator("scope_label")
    @classmethod
    def normalize_scope_label(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFC", value).strip()
        if not normalized:
            raise ValueError("scope_label must not be blank")
        if any(unicodedata.category(character) == "Cc" for character in normalized):
            raise ValueError("scope_label contains control characters")
        return normalized


class ScheduleSnapshotMetadata(BaseModel):
    scope_type: ScheduleScopeType
    scope_id: str
    scope_key: str
    scope_label: str
    week_start: date
    week_end: date
    content_hash: str
    created_at: datetime


class ScheduleSnapshot(ScheduleSnapshotMetadata):
    schedule: ScheduleSnapshotSchedule
