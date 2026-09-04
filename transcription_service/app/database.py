from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from .schemas import TranscriptionPayload
from .transcriber import TranscriptionResult

RECORDING_COLUMNS = """
    id,
    lesson_key,
    lesson_title,
    scope_label,
    lecturer_name,
    original_filename,
    stored_filename,
    content_type,
    size_bytes,
    status,
    attempts,
    duration_seconds,
    detected_language,
    language_probability,
    transcript_json,
    error,
    recorded_at,
    result_id,
    result_hash,
    engine,
    model,
    engine_version,
    client_version,
    created_at,
    updated_at
"""

RECORDING_SUMMARY_COLUMNS = """
    id,
    lesson_key,
    lesson_title,
    scope_label,
    lecturer_name,
    original_filename,
    stored_filename,
    content_type,
    size_bytes,
    status,
    attempts,
    duration_seconds,
    detected_language,
    language_probability,
    error,
    recorded_at,
    result_id,
    result_hash,
    engine,
    model,
    engine_version,
    client_version,
    created_at,
    updated_at
"""

ATTACHMENT_COLUMNS = """
    id,
    lesson_key,
    lesson_title,
    scope_label,
    original_filename,
    stored_filename,
    content_type,
    size_bytes,
    recorded_at,
    created_at,
    updated_at
"""

SCHEDULE_SNAPSHOT_COLUMNS = """
    scope_type,
    scope_id,
    scope_label,
    week_start,
    week_end,
    schedule_json,
    content_hash,
    created_at
"""

SCHEDULE_SNAPSHOT_METADATA_COLUMNS = """
    scope_type,
    scope_id,
    scope_label,
    week_start,
    week_end,
    content_hash,
    created_at
"""

COMMUNITY_WORKER_COLUMNS = """
    id,
    label,
    token_hash,
    created_at,
    last_seen_at,
    revoked_at
"""

COMMUNITY_WORKER_PUBLIC_COLUMNS = """
    id,
    label,
    created_at,
    last_seen_at,
    revoked_at
"""

TRANSCRIPTION_LEASE_COLUMNS = """
    recording_id,
    worker_id,
    claim_id,
    lease_token_hash,
    leased_at,
    lease_expires_at,
    heartbeat_at
"""

TRANSCRIPTION_LEASE_FIELD_NAMES = (
    "recording_id",
    "worker_id",
    "claim_id",
    "leased_at",
    "lease_expires_at",
    "heartbeat_at",
)

AUDIO_ARTIFACT_COLUMNS = """
    recording_id,
    state,
    stored_filename,
    content_type,
    size_bytes,
    sha256,
    attempts,
    error,
    created_at,
    updated_at
"""

MAX_SCHEDULE_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_COMMUNITY_METADATA_LENGTH = 200
MAX_RECORDING_SEARCH_TOKENS = 8
MAX_RECORDING_SEARCH_TOKEN_LENGTH = 64
MAX_LESSON_KEY_LENGTH = 4_096

RECORDING_SEARCH_SUMMARY_COLUMNS = ",\n".join(
    f"recording.{line.strip().rstrip(',')}" for line in RECORDING_SUMMARY_COLUMNS.strip().splitlines()
)
ATTACHMENT_SEARCH_SUMMARY_COLUMNS = ",\n".join(
    f"attachment.{line.strip().rstrip(',')}" for line in ATTACHMENT_COLUMNS.strip().splitlines()
)
SEARCH_TOKEN_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)

RemoteCompletionStatus = Literal["completed", "duplicate", "conflict", "lease_lost"]


class ScheduleSnapshotConflictError(Exception):
    pass


class ScheduleSnapshotTooLargeError(Exception):
    pass


def recording_search_tokens(query: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", query).casefold().replace("’", "'").replace("ʼ", "'")
    tokens: list[str] = []
    for token in SEARCH_TOKEN_PATTERN.findall(normalized):
        if len(token) < 2:
            continue
        shortened = token[:MAX_RECORDING_SEARCH_TOKEN_LENGTH]
        if shortened not in tokens:
            tokens.append(shortened)
        if len(tokens) == MAX_RECORDING_SEARCH_TOKENS:
            break
    return tuple(tokens)


def _fts_expression(tokens: tuple[str, ...], *, column: str | None = None, operator: str = "AND") -> str:
    terms = f" {operator} ".join(f'"{token.replace(chr(34), chr(34) * 2)}"*' for token in tokens)
    return f"{column} : ({terms})" if column else f"({terms})"


def _search_words(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("’", "'").replace("ʼ", "'")
    return tuple(SEARCH_TOKEN_PATTERN.findall(normalized))


def _contains_search_token(value: str, tokens: tuple[str, ...]) -> bool:
    words = _search_words(value)
    return any(word.startswith(token) for word in words for token in tokens)


def _normalized_groups(
    groups: Iterable[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    normalized: dict[str, str] = {}
    for raw_group_id, raw_group_label in groups:
        group_id = unicodedata.normalize("NFC", raw_group_id).strip()
        group_label = unicodedata.normalize("NFC", raw_group_label).strip()
        if not group_id or len(group_id) > 128 or re.fullmatch(r"[A-Za-z0-9._-]+", group_id) is None:
            raise ValueError("group_id contains unsupported characters")
        if not group_label or len(group_label) > 200:
            raise ValueError("group_label must contain between 1 and 200 characters")
        normalized[group_id] = group_label
    return tuple(normalized.items())


def _normalized_lesson_keys(primary_key: str, lesson_keys: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw_lesson_key in (primary_key, *lesson_keys):
        lesson_key = unicodedata.normalize("NFC", raw_lesson_key).strip()
        if not lesson_key or len(lesson_key) > MAX_LESSON_KEY_LENGTH:
            raise ValueError("lesson_key must contain between 1 and 4096 characters")
        if any(unicodedata.category(character) == "Cc" for character in lesson_key):
            raise ValueError("lesson_key contains control characters")
        if lesson_key not in normalized:
            normalized.append(lesson_key)
    return tuple(normalized)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _lease_expiration(timestamp: str, lease_seconds: int) -> str:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be greater than zero")
    return (datetime.fromisoformat(timestamp) + timedelta(seconds=lease_seconds)).isoformat(timespec="milliseconds")


def _community_metadata(value: str | None, field_name: str, *, required: bool = False) -> str | None:
    normalized = value.strip() if value is not None else None
    if required and not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if normalized is not None and len(normalized) > MAX_COMMUNITY_METADATA_LENGTH:
        raise ValueError(f"{field_name} must contain at most {MAX_COMMUNITY_METADATA_LENGTH} characters")
    return normalized


class RecordingRepository:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recordings (
                    id TEXT PRIMARY KEY,
                    lesson_key TEXT NOT NULL,
                    lesson_title TEXT NOT NULL,
                    scope_label TEXT NOT NULL,
                    lecturer_name TEXT,
                    original_filename TEXT NOT NULL,
                    stored_filename TEXT NOT NULL UNIQUE,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
                    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                    duration_seconds REAL,
                    detected_language TEXT,
                    language_probability REAL,
                    transcript_json TEXT,
                    error TEXT,
                    recorded_at TEXT,
                    result_id TEXT,
                    result_hash TEXT,
                    engine TEXT,
                    model TEXT,
                    engine_version TEXT,
                    client_version TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(recordings)").fetchall()}
            recording_migrations = {
                "lecturer_name": "TEXT",
                "recorded_at": "TEXT",
                "result_id": "TEXT",
                "result_hash": "TEXT",
                "engine": "TEXT",
                "model": "TEXT",
                "engine_version": "TEXT",
                "client_version": "TEXT",
            }
            for column_name, column_type in recording_migrations.items():
                if column_name not in columns:
                    connection.execute(f"ALTER TABLE recordings ADD COLUMN {column_name} {column_type}")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS community_workers (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT,
                    revoked_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transcription_leases (
                    recording_id TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
                    worker_id TEXT NOT NULL REFERENCES community_workers(id),
                    claim_id TEXT NOT NULL UNIQUE,
                    lease_token_hash TEXT NOT NULL,
                    leased_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recording_audio_artifacts (
                    recording_id TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'processing', 'ready', 'failed')),
                    stored_filename TEXT UNIQUE,
                    content_type TEXT,
                    size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes > 0),
                    sha256 TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (
                        state != 'ready'
                        OR (
                            stored_filename IS NOT NULL
                            AND content_type IS NOT NULL
                            AND size_bytes IS NOT NULL
                            AND length(sha256) = 64
                        )
                    )
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO recording_audio_artifacts (
                    recording_id, state, attempts, created_at, updated_at
                )
                SELECT id, 'pending', 0, created_at, updated_at
                FROM recordings
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transcription_leases_worker
                ON transcription_leases (worker_id)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transcription_leases_expiration
                ON transcription_leases (lease_expires_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recordings_lesson_key_recorded_at
                ON recordings (lesson_key, recorded_at DESC, created_at DESC)
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_recordings_queue ON recordings (status, created_at ASC)")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audio_artifacts_queue
                ON recording_audio_artifacts (state, created_at ASC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    lesson_key TEXT NOT NULL,
                    lesson_title TEXT NOT NULL,
                    scope_label TEXT NOT NULL,
                    lecturer_name TEXT,
                    original_filename TEXT NOT NULL,
                    stored_filename TEXT NOT NULL UNIQUE,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
                    recorded_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            attachment_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(attachments)").fetchall()
            }
            if "lecturer_name" not in attachment_columns:
                connection.execute("ALTER TABLE attachments ADD COLUMN lecturer_name TEXT")
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attachments_lesson_key_recorded_at
                ON attachments (lesson_key, recorded_at DESC, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_snapshots (
                    scope_type TEXT NOT NULL CHECK (scope_type IN ('group', 'lecturer')),
                    scope_id TEXT NOT NULL,
                    scope_label TEXT NOT NULL,
                    week_start TEXT NOT NULL,
                    week_end TEXT NOT NULL,
                    schedule_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (scope_type, scope_id, week_start)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_schedule_snapshots_scope_week
                ON schedule_snapshots (scope_type, scope_id, week_start DESC)
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS schedule_snapshots_prevent_update
                BEFORE UPDATE ON schedule_snapshots
                BEGIN
                    SELECT RAISE(ABORT, 'schedule snapshots are immutable');
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS schedule_snapshots_prevent_delete
                BEFORE DELETE ON schedule_snapshots
                BEGIN
                    SELECT RAISE(ABORT, 'schedule snapshots are immutable');
                END
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recording_groups (
                    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
                    group_id TEXT NOT NULL,
                    group_label TEXT NOT NULL,
                    PRIMARY KEY (recording_id, group_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recording_groups_group_recording
                ON recording_groups (group_id, recording_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recording_lesson_keys (
                    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
                    lesson_key TEXT NOT NULL,
                    PRIMARY KEY (recording_id, lesson_key)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recording_lesson_keys_key_recording
                ON recording_lesson_keys (lesson_key, recording_id)
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO recording_lesson_keys (recording_id, lesson_key)
                SELECT id, lesson_key FROM recordings
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recording_search_documents (
                    id INTEGER PRIMARY KEY,
                    recording_id TEXT NOT NULL UNIQUE REFERENCES recordings(id) ON DELETE CASCADE,
                    lesson_title TEXT NOT NULL,
                    lecturer_name TEXT NOT NULL,
                    transcript_text TEXT NOT NULL,
                    source_updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS recording_search_fts USING fts5(
                    lesson_title,
                    lecturer_name,
                    transcript_text,
                    content='recording_search_documents',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2',
                    prefix='2 3 4'
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS recording_search_documents_ai
                AFTER INSERT ON recording_search_documents
                BEGIN
                    INSERT INTO recording_search_fts (rowid, lesson_title, lecturer_name, transcript_text)
                    VALUES (new.id, new.lesson_title, new.lecturer_name, new.transcript_text);
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS recording_search_documents_ad
                AFTER DELETE ON recording_search_documents
                BEGIN
                    INSERT INTO recording_search_fts (
                        recording_search_fts, rowid, lesson_title, lecturer_name, transcript_text
                    ) VALUES (
                        'delete', old.id, old.lesson_title, old.lecturer_name, old.transcript_text
                    );
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS recording_search_documents_au
                AFTER UPDATE ON recording_search_documents
                BEGIN
                    INSERT INTO recording_search_fts (
                        recording_search_fts, rowid, lesson_title, lecturer_name, transcript_text
                    ) VALUES (
                        'delete', old.id, old.lesson_title, old.lecturer_name, old.transcript_text
                    );
                    INSERT INTO recording_search_fts (rowid, lesson_title, lecturer_name, transcript_text)
                    VALUES (new.id, new.lesson_title, new.lecturer_name, new.transcript_text);
                END
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recording_search_segments (
                    id INTEGER PRIMARY KEY,
                    recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
                    segment_id INTEGER NOT NULL,
                    start REAL NOT NULL,
                    end REAL NOT NULL,
                    text TEXT NOT NULL,
                    UNIQUE (recording_id, segment_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_recording_search_segments_recording
                ON recording_search_segments (recording_id, segment_id)
                """
            )
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS recording_segment_fts USING fts5(
                    text,
                    content='recording_search_segments',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2',
                    prefix='2 3 4'
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS recording_search_segments_ai
                AFTER INSERT ON recording_search_segments
                BEGIN
                    INSERT INTO recording_segment_fts (rowid, text) VALUES (new.id, new.text);
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS recording_search_segments_ad
                AFTER DELETE ON recording_search_segments
                BEGIN
                    INSERT INTO recording_segment_fts (recording_segment_fts, rowid, text)
                    VALUES ('delete', old.id, old.text);
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS recording_search_segments_au
                AFTER UPDATE ON recording_search_segments
                BEGIN
                    INSERT INTO recording_segment_fts (recording_segment_fts, rowid, text)
                    VALUES ('delete', old.id, old.text);
                    INSERT INTO recording_segment_fts (rowid, text) VALUES (new.id, new.text);
                END
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attachment_groups (
                    attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
                    group_id TEXT NOT NULL,
                    group_label TEXT NOT NULL,
                    PRIMARY KEY (attachment_id, group_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attachment_groups_group_attachment
                ON attachment_groups (group_id, attachment_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attachment_lesson_keys (
                    attachment_id TEXT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
                    lesson_key TEXT NOT NULL,
                    PRIMARY KEY (attachment_id, lesson_key)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attachment_lesson_keys_key_attachment
                ON attachment_lesson_keys (lesson_key, attachment_id)
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO attachment_lesson_keys (attachment_id, lesson_key)
                SELECT id, lesson_key FROM attachments
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attachment_search_documents (
                    id INTEGER PRIMARY KEY,
                    attachment_id TEXT NOT NULL UNIQUE REFERENCES attachments(id) ON DELETE CASCADE,
                    lesson_title TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    source_updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS attachment_search_fts USING fts5(
                    lesson_title,
                    file_name,
                    content='attachment_search_documents',
                    content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2',
                    prefix='2 3 4'
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS attachment_search_documents_ai
                AFTER INSERT ON attachment_search_documents
                BEGIN
                    INSERT INTO attachment_search_fts (rowid, lesson_title, file_name)
                    VALUES (new.id, new.lesson_title, new.file_name);
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS attachment_search_documents_ad
                AFTER DELETE ON attachment_search_documents
                BEGIN
                    INSERT INTO attachment_search_fts (
                        attachment_search_fts, rowid, lesson_title, file_name
                    ) VALUES ('delete', old.id, old.lesson_title, old.file_name);
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS attachment_search_documents_au
                AFTER UPDATE ON attachment_search_documents
                BEGIN
                    INSERT INTO attachment_search_fts (
                        attachment_search_fts, rowid, lesson_title, file_name
                    ) VALUES ('delete', old.id, old.lesson_title, old.file_name);
                    INSERT INTO attachment_search_fts (rowid, lesson_title, file_name)
                    VALUES (new.id, new.lesson_title, new.file_name);
                END
                """
            )
            self._backfill_recording_context_locked(connection)
            self._sync_recording_search_locked(connection)
            self._sync_attachment_search_locked(connection)
            connection.execute("PRAGMA optimize")

    @staticmethod
    def _lesson_key_context(lesson_key: str) -> dict[str, Any]:
        if not lesson_key.startswith("{"):
            return {}
        try:
            context = json.loads(lesson_key)
        except (TypeError, json.JSONDecodeError):
            return {}
        return context if isinstance(context, dict) else {}

    @classmethod
    def _matching_snapshot_pairs_locked(
        cls,
        connection: sqlite3.Connection,
        *,
        material_kind: Literal["recording", "attachment"],
        material_id: str,
        lesson_title: str,
        recorded_at: str,
        scope_label: str,
        lecturer_name: str | None,
        lesson_key: str,
    ) -> list[dict[str, Any]]:
        try:
            weekday = date.fromisoformat(recorded_at).weekday()
        except ValueError:
            return []
        if weekday > 5:
            return []

        key_context = cls._lesson_key_context(lesson_key)
        expected_time = key_context.get("time")
        expected_tag = key_context.get("tag")
        expected_lecturer_id = key_context.get("lecturerId")
        group_table, material_id_column = {
            "recording": ("recording_groups", "recording_id"),
            "attachment": ("attachment_groups", "attachment_id"),
        }[material_kind]
        lecturer_label = lecturer_name or scope_label
        snapshot_rows = connection.execute(
            f"""
            SELECT DISTINCT snapshot.scope_type, snapshot.scope_id, snapshot.scope_label,
                            snapshot.schedule_json
            FROM schedule_snapshots AS snapshot
            LEFT JOIN {group_table} AS material_group
              ON material_group.{material_id_column} = ?
             AND material_group.group_id = snapshot.scope_id
            WHERE snapshot.week_start <= ? AND snapshot.week_end >= ?
              AND (
                  (snapshot.scope_type = 'group' AND material_group.group_id IS NOT NULL)
                  OR (snapshot.scope_type = 'lecturer' AND snapshot.scope_label = ?)
              )
            """,
            (material_id, recorded_at, recorded_at, lecturer_label),
        ).fetchall()

        matches: list[dict[str, Any]] = []
        for row in snapshot_rows:
            try:
                schedule = json.loads(row["schedule_json"])
                schedule_days = schedule["days"]
            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                continue
            day_code = ("Пн", "Вв", "Ср", "Чт", "Пт", "Сб")[weekday]
            day = next(
                (
                    candidate
                    for candidate in schedule_days
                    if isinstance(candidate, dict) and candidate.get("day") == day_code
                ),
                schedule_days[weekday] if weekday < len(schedule_days) else None,
            )
            if not isinstance(day, dict):
                continue
            for pair in day.get("pairs", []):
                if not isinstance(pair, dict) or pair.get("name") != lesson_title:
                    continue
                pair_dates = pair.get("dates")
                if isinstance(pair_dates, list) and pair_dates and recorded_at not in pair_dates:
                    continue
                if expected_time and pair.get("time") != expected_time:
                    continue
                if expected_tag and pair.get("tag") != expected_tag:
                    continue
                lecturer = pair.get("lecturer")
                if (
                    lecturer_name
                    and isinstance(lecturer, dict)
                    and lecturer.get("name")
                    and str(lecturer["name"]).strip() != lecturer_name
                ):
                    continue
                resolved_lecturer_id = (
                    str(lecturer["id"])
                    if isinstance(lecturer, dict) and lecturer.get("id")
                    else str(row["scope_id"])
                    if row["scope_type"] == "lecturer"
                    else None
                )
                if expected_lecturer_id and resolved_lecturer_id != expected_lecturer_id:
                    continue
                matches.append(
                    {
                        "pair": pair,
                        "scope_type": str(row["scope_type"]),
                        "scope_id": str(row["scope_id"]),
                        "scope_label": str(row["scope_label"]),
                        "schedule_week": schedule.get("scheduleWeek"),
                        "day": day.get("day") or day_code,
                        "lecturer_id": resolved_lecturer_id,
                    }
                )

        identities = {
            (
                match["pair"].get("time"),
                match["pair"].get("type"),
                match["pair"].get("tag"),
                match["lecturer_id"],
            )
            for match in matches
        }
        if len(identities) > 1:
            return []
        return matches

    @staticmethod
    def _legacy_lesson_key_from_match(match: dict[str, Any]) -> str | None:
        pair = match["pair"]
        schedule_week = match.get("schedule_week")
        day = match.get("day")
        if not isinstance(pair, dict) or not schedule_week or not day:
            return None

        lecturer_id = match.get("lecturer_id")
        owner = f"lecturer:{lecturer_id}" if lecturer_id else f"{match['scope_type']}:{match['scope_id']}"
        payload: dict[str, Any] = {
            "version": 2,
            "owner": owner,
            "week": schedule_week,
            "day": day,
        }
        for field in ("time", "name", "type", "tag"):
            if field in pair:
                payload[field] = pair[field]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _material_rows_locked(
        connection: sqlite3.Connection,
        *,
        table: Literal["recordings", "attachments"],
        columns: str,
        material_ids: Iterable[str] | None,
    ) -> list[sqlite3.Row]:
        if material_ids is None:
            return connection.execute(f"SELECT {columns} FROM {table}").fetchall()

        selected_ids = tuple(dict.fromkeys(material_ids))
        if not selected_ids:
            return []
        placeholders = ", ".join("?" for _ in selected_ids)
        return connection.execute(
            f"SELECT {columns} FROM {table} WHERE id IN ({placeholders})",
            selected_ids,
        ).fetchall()

    @classmethod
    def _backfill_recording_context_locked(
        cls,
        connection: sqlite3.Connection,
        *,
        recording_ids: Iterable[str] | None = None,
        attachment_ids: Iterable[str] | None = None,
    ) -> None:
        snapshot_groups = connection.execute(
            """
            SELECT scope_id, scope_label
            FROM schedule_snapshots
            WHERE scope_type = 'group'
            GROUP BY scope_id, scope_label
            """
        ).fetchall()
        groups_by_scope_label: dict[str, list[tuple[str, str]]] = {}
        group_labels_by_id: dict[str, str] = {}
        for row in snapshot_groups:
            scope_label = str(row["scope_label"])
            group_label = scope_label.removeprefix("Група ").strip() or scope_label
            group_id = str(row["scope_id"])
            groups_by_scope_label.setdefault(scope_label, []).append((group_id, group_label))
            group_labels_by_id[group_id] = group_label

        recordings = cls._material_rows_locked(
            connection,
            table="recordings",
            columns="""
                id, lesson_key, lesson_title, scope_label, lecturer_name,
                COALESCE(recorded_at, substr(created_at, 1, 10)) AS recorded_at
            """,
            material_ids=recording_ids,
        )
        for recording in recordings:
            recording_id = str(recording["id"])
            inferred_groups = list(groups_by_scope_label.get(str(recording["scope_label"]), []))
            key_context = cls._lesson_key_context(str(recording["lesson_key"]))
            group_ids = key_context.get("groupIds")
            if isinstance(group_ids, list):
                for group_id_value in group_ids:
                    group_id = str(group_id_value)
                    group_label = group_labels_by_id.get(group_id)
                    if group_label and (group_id, group_label) not in inferred_groups:
                        inferred_groups.append((group_id, group_label))
            connection.executemany(
                """
                INSERT OR IGNORE INTO recording_groups (recording_id, group_id, group_label)
                VALUES (?, ?, ?)
                """,
                [(recording_id, group_id, group_label) for group_id, group_label in inferred_groups],
            )

            effective_lecturer_name = str(recording["lecturer_name"]) if recording["lecturer_name"] else None
            matches = cls._matching_snapshot_pairs_locked(
                connection,
                material_kind="recording",
                material_id=recording_id,
                lesson_title=str(recording["lesson_title"]),
                recorded_at=str(recording["recorded_at"]),
                scope_label=str(recording["scope_label"]),
                lecturer_name=effective_lecturer_name,
                lesson_key=str(recording["lesson_key"]),
            )
            lecturer_names = {
                str(lecturer["name"]).strip()
                for match in matches
                if isinstance((lecturer := match["pair"].get("lecturer")), dict) and lecturer.get("name")
            }
            lecturer_names.update(match["scope_label"] for match in matches if match["scope_type"] == "lecturer")
            if len(lecturer_names) == 1:
                effective_lecturer_name = lecturer_names.pop()
                connection.execute(
                    "UPDATE recordings SET lecturer_name = ? WHERE id = ? AND lecturer_name IS NULL",
                    (effective_lecturer_name, recording_id),
                )
                matches = cls._matching_snapshot_pairs_locked(
                    connection,
                    material_kind="recording",
                    material_id=recording_id,
                    lesson_title=str(recording["lesson_title"]),
                    recorded_at=str(recording["recorded_at"]),
                    scope_label=str(recording["scope_label"]),
                    lecturer_name=effective_lecturer_name,
                    lesson_key=str(recording["lesson_key"]),
                )

            inferred_pair_groups: dict[str, str] = {}
            for match in matches:
                pair_groups = match["pair"].get("groups")
                if isinstance(pair_groups, list):
                    for group in pair_groups:
                        if isinstance(group, dict) and group.get("id") and group.get("name"):
                            inferred_pair_groups[str(group["id"])] = str(group["name"]).strip()
                legacy_lesson_key = cls._legacy_lesson_key_from_match(match)
                if legacy_lesson_key:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO recording_lesson_keys (recording_id, lesson_key)
                        VALUES (?, ?)
                        """,
                        (recording_id, legacy_lesson_key),
                    )
            connection.executemany(
                """
                INSERT OR IGNORE INTO recording_groups (recording_id, group_id, group_label)
                VALUES (?, ?, ?)
                """,
                [(recording_id, group_id, group_label) for group_id, group_label in inferred_pair_groups.items()],
            )

        attachments = cls._material_rows_locked(
            connection,
            table="attachments",
            columns="id, lesson_key, lesson_title, scope_label, lecturer_name, recorded_at",
            material_ids=attachment_ids,
        )
        for attachment in attachments:
            attachment_id = str(attachment["id"])
            inferred_groups = list(groups_by_scope_label.get(str(attachment["scope_label"]), []))
            key_context = cls._lesson_key_context(str(attachment["lesson_key"]))
            group_ids = key_context.get("groupIds")
            if isinstance(group_ids, list):
                for group_id_value in group_ids:
                    group_id = str(group_id_value)
                    group_label = group_labels_by_id.get(group_id)
                    if group_label and (group_id, group_label) not in inferred_groups:
                        inferred_groups.append((group_id, group_label))
            connection.executemany(
                """
                INSERT OR IGNORE INTO attachment_groups (attachment_id, group_id, group_label)
                VALUES (?, ?, ?)
                """,
                [(attachment_id, group_id, group_label) for group_id, group_label in inferred_groups],
            )

            effective_lecturer_name = str(attachment["lecturer_name"]) if attachment["lecturer_name"] else None
            matches = cls._matching_snapshot_pairs_locked(
                connection,
                material_kind="attachment",
                material_id=attachment_id,
                lesson_title=str(attachment["lesson_title"]),
                recorded_at=str(attachment["recorded_at"]),
                scope_label=str(attachment["scope_label"]),
                lecturer_name=effective_lecturer_name,
                lesson_key=str(attachment["lesson_key"]),
            )
            lecturer_names = {
                str(lecturer["name"]).strip()
                for match in matches
                if isinstance((lecturer := match["pair"].get("lecturer")), dict) and lecturer.get("name")
            }
            lecturer_names.update(match["scope_label"] for match in matches if match["scope_type"] == "lecturer")
            if len(lecturer_names) == 1:
                effective_lecturer_name = lecturer_names.pop()
                connection.execute(
                    "UPDATE attachments SET lecturer_name = ? WHERE id = ? AND lecturer_name IS NULL",
                    (effective_lecturer_name, attachment_id),
                )
                matches = cls._matching_snapshot_pairs_locked(
                    connection,
                    material_kind="attachment",
                    material_id=attachment_id,
                    lesson_title=str(attachment["lesson_title"]),
                    recorded_at=str(attachment["recorded_at"]),
                    scope_label=str(attachment["scope_label"]),
                    lecturer_name=effective_lecturer_name,
                    lesson_key=str(attachment["lesson_key"]),
                )

            inferred_pair_groups = {}
            for match in matches:
                pair_groups = match["pair"].get("groups")
                if isinstance(pair_groups, list):
                    for group in pair_groups:
                        if isinstance(group, dict) and group.get("id") and group.get("name"):
                            inferred_pair_groups[str(group["id"])] = str(group["name"]).strip()
                legacy_lesson_key = cls._legacy_lesson_key_from_match(match)
                if legacy_lesson_key:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO attachment_lesson_keys (attachment_id, lesson_key)
                        VALUES (?, ?)
                        """,
                        (attachment_id, legacy_lesson_key),
                    )
            connection.executemany(
                """
                INSERT OR IGNORE INTO attachment_groups (attachment_id, group_id, group_label)
                VALUES (?, ?, ?)
                """,
                [(attachment_id, group_id, group_label) for group_id, group_label in inferred_pair_groups.items()],
            )

    @classmethod
    def _replace_recording_search_locked(cls, connection: sqlite3.Connection, recording_id: str) -> None:
        connection.execute(
            "DELETE FROM recording_search_segments WHERE recording_id = ?",
            (recording_id,),
        )
        connection.execute(
            "DELETE FROM recording_search_documents WHERE recording_id = ?",
            (recording_id,),
        )
        recording = connection.execute(
            """
            SELECT id, lesson_title, lecturer_name, transcript_json, updated_at
            FROM recordings
            WHERE id = ? AND status = 'ready'
            """,
            (recording_id,),
        ).fetchone()
        if recording is None:
            return

        try:
            raw_segments = json.loads(recording["transcript_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(raw_segments, list):
            return

        segments: list[tuple[str, int, float, float, str]] = []
        transcript_parts: list[str] = []
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, dict):
                continue
            try:
                segment_id = int(raw_segment["id"])
                start = float(raw_segment["start"])
                end = float(raw_segment["end"])
                text = str(raw_segment["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if not text:
                continue
            transcript_parts.append(text)
            segments.append((recording_id, segment_id, start, end, text))

        cursor = connection.execute(
            """
            INSERT INTO recording_search_documents (
                recording_id, lesson_title, lecturer_name, transcript_text, source_updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                recording_id,
                recording["lesson_title"],
                recording["lecturer_name"] or "",
                "\n".join(transcript_parts),
                recording["updated_at"],
            ),
        )
        if cursor.rowcount != 1:  # pragma: no cover - successful INSERT always changes one row
            raise RuntimeError("Could not populate recording search document")
        connection.executemany(
            """
            INSERT INTO recording_search_segments (recording_id, segment_id, start, end, text)
            VALUES (?, ?, ?, ?, ?)
            """,
            segments,
        )

    @classmethod
    def _sync_recording_search_locked(cls, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM recording_search_documents
            WHERE recording_id NOT IN (SELECT id FROM recordings WHERE status = 'ready')
            """
        )
        stale_rows = connection.execute(
            """
            SELECT recording.id
            FROM recordings AS recording
            LEFT JOIN recording_search_documents AS document
              ON document.recording_id = recording.id
            WHERE recording.status = 'ready'
              AND (
                  document.id IS NULL
                  OR document.source_updated_at != recording.updated_at
                  OR document.lesson_title != recording.lesson_title
                  OR document.lecturer_name != COALESCE(recording.lecturer_name, '')
              )
            """
        ).fetchall()
        for row in stale_rows:
            cls._replace_recording_search_locked(connection, str(row["id"]))

    @staticmethod
    def _replace_attachment_search_locked(connection: sqlite3.Connection, attachment_id: str) -> None:
        connection.execute(
            "DELETE FROM attachment_search_documents WHERE attachment_id = ?",
            (attachment_id,),
        )
        connection.execute(
            """
            INSERT INTO attachment_search_documents (
                attachment_id, lesson_title, file_name, source_updated_at
            )
            SELECT id, lesson_title, original_filename, updated_at
            FROM attachments
            WHERE id = ?
            """,
            (attachment_id,),
        )

    @classmethod
    def _sync_attachment_search_locked(cls, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM attachment_search_documents
            WHERE attachment_id NOT IN (SELECT id FROM attachments)
            """
        )
        stale_rows = connection.execute(
            """
            SELECT attachment.id
            FROM attachments AS attachment
            LEFT JOIN attachment_search_documents AS document
              ON document.attachment_id = attachment.id
            WHERE document.id IS NULL
               OR document.source_updated_at != attachment.updated_at
               OR document.lesson_title != attachment.lesson_title
               OR document.file_name != attachment.original_filename
            """
        ).fetchall()
        for row in stale_rows:
            cls._replace_attachment_search_locked(connection, str(row["id"]))

    def create_community_worker(
        self,
        *,
        worker_id: str,
        label: str,
        token_hash: str,
    ) -> dict[str, Any]:
        normalized_id = _community_metadata(worker_id, "worker_id", required=True)
        normalized_label = _community_metadata(label, "label", required=True)
        normalized_token_hash = _community_metadata(token_hash, "token_hash", required=True)
        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO community_workers (id, label, token_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (normalized_id, normalized_label, normalized_token_hash, timestamp),
            )
            row = connection.execute(
                f"SELECT {COMMUNITY_WORKER_COLUMNS} FROM community_workers WHERE id = ?",
                (normalized_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - guards an impossible successful insert state
            raise RuntimeError("Could not read the newly created community worker")
        return dict(row)

    def get_community_worker(self, worker_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {COMMUNITY_WORKER_COLUMNS} FROM community_workers WHERE id = ?",
                (worker_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_community_workers(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {COMMUNITY_WORKER_PUBLIC_COLUMNS}
                FROM community_workers
                ORDER BY created_at DESC, id ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def touch_community_worker(self, worker_id: str) -> bool:
        timestamp = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE community_workers
                SET last_seen_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (timestamp, worker_id),
            )
        return cursor.rowcount == 1

    def revoke_community_worker(self, worker_id: str) -> int | None:
        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            worker = connection.execute(
                "SELECT id FROM community_workers WHERE id = ?",
                (worker_id,),
            ).fetchone()
            if worker is None:
                return None

            connection.execute(
                """
                UPDATE community_workers
                SET revoked_at = COALESCE(revoked_at, ?)
                WHERE id = ?
                """,
                (timestamp, worker_id),
            )
            requeued = connection.execute(
                """
                UPDATE recordings
                SET status = 'queued', error = NULL, result_id = NULL, result_hash = NULL,
                    engine = NULL, model = NULL, engine_version = NULL, client_version = NULL,
                    updated_at = ?
                WHERE status = 'processing'
                  AND id IN (
                      SELECT recording_id
                      FROM transcription_leases
                      WHERE worker_id = ?
                  )
                """,
                (timestamp, worker_id),
            ).rowcount
            connection.execute(
                "DELETE FROM transcription_leases WHERE worker_id = ?",
                (worker_id,),
            )
        return requeued

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None

        recording = dict(row)
        raw_transcript = recording.pop("transcript_json", None)
        recording["transcript"] = json.loads(raw_transcript) if raw_transcript else []
        recording["recorded_at"] = recording.get("recorded_at") or str(recording["created_at"])[:10]
        return recording

    @staticmethod
    def _decode_lease(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {field_name: row[field_name] for field_name in TRANSCRIPTION_LEASE_FIELD_NAMES}

    @staticmethod
    def _decode_audio_artifact(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def create(
        self,
        *,
        recording_id: str,
        lesson_key: str,
        lesson_title: str,
        scope_label: str,
        original_filename: str,
        stored_filename: str,
        content_type: str,
        size_bytes: int,
        recorded_at: str | None,
        groups: Iterable[tuple[str, str]] = (),
        lecturer_name: str | None = None,
        lesson_keys: Iterable[str] = (),
    ) -> dict[str, Any]:
        timestamp = utc_now()
        effective_recorded_at = recorded_at or timestamp[:10]
        normalized_groups = _normalized_groups(groups)
        normalized_lesson_keys = _normalized_lesson_keys(lesson_key, lesson_keys)
        normalized_lecturer_name = (
            unicodedata.normalize("NFC", lecturer_name).strip() if lecturer_name is not None else None
        )
        if normalized_lecturer_name is not None and (
            not normalized_lecturer_name or len(normalized_lecturer_name) > MAX_COMMUNITY_METADATA_LENGTH
        ):
            raise ValueError("lecturer_name must contain between 1 and 200 characters")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO recordings (
                    id, lesson_key, lesson_title, scope_label, lecturer_name, original_filename,
                    stored_filename, content_type, size_bytes, status, attempts,
                    recorded_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)
                """,
                (
                    recording_id,
                    lesson_key,
                    lesson_title,
                    scope_label,
                    normalized_lecturer_name,
                    original_filename,
                    stored_filename,
                    content_type,
                    size_bytes,
                    effective_recorded_at,
                    timestamp,
                    timestamp,
                ),
            )
            connection.executemany(
                """
                INSERT INTO recording_groups (recording_id, group_id, group_label)
                VALUES (?, ?, ?)
                """,
                [(recording_id, group_id, group_label) for group_id, group_label in normalized_groups],
            )
            connection.executemany(
                """
                INSERT INTO recording_lesson_keys (recording_id, lesson_key)
                VALUES (?, ?)
                """,
                [(recording_id, alias) for alias in normalized_lesson_keys],
            )
            connection.execute(
                """
                INSERT INTO recording_audio_artifacts (
                    recording_id, state, attempts, created_at, updated_at
                ) VALUES (?, 'pending', 0, ?, ?)
                """,
                (recording_id, timestamp, timestamp),
            )
            self._backfill_recording_context_locked(
                connection,
                recording_ids=(recording_id,),
                attachment_ids=(),
            )
            row = connection.execute(
                f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE id = ?",
                (recording_id,),
            ).fetchone()
            recording = self._decode(row)
        if recording is None:  # pragma: no cover - guards an impossible successful insert state
            raise RuntimeError("Could not read the newly created recording")
        return recording

    def get(self, recording_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE id = ?",
                (recording_id,),
            ).fetchone()
        return self._decode(row)

    def get_audio_artifact(self, recording_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {AUDIO_ARTIFACT_COLUMNS} FROM recording_audio_artifacts WHERE recording_id = ?",
                (recording_id,),
            ).fetchone()
        return self._decode_audio_artifact(row)

    def list_audio_artifacts(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT {AUDIO_ARTIFACT_COLUMNS} FROM recording_audio_artifacts ORDER BY created_at, recording_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_audio_preparation(self, *, max_attempts: int) -> dict[str, Any] | None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")
        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exhausted = connection.execute(
                """
                SELECT recording_id
                FROM recording_audio_artifacts
                WHERE state = 'pending' AND attempts >= ?
                """,
                (max_attempts,),
            ).fetchall()
            exhausted_ids = [str(row["recording_id"]) for row in exhausted]
            if exhausted_ids:
                placeholders = ",".join("?" for _ in exhausted_ids)
                message = "Audio preparation attempt limit reached"
                connection.execute(
                    f"""
                    UPDATE recording_audio_artifacts
                    SET state = 'failed', error = ?, updated_at = ?
                    WHERE recording_id IN ({placeholders}) AND state = 'pending'
                    """,
                    (message, timestamp, *exhausted_ids),
                )
                connection.execute(
                    f"""
                    UPDATE recordings
                    SET status = 'failed', error = ?, updated_at = ?
                    WHERE id IN ({placeholders}) AND status = 'queued'
                    """,
                    (message, timestamp, *exhausted_ids),
                )

            row = connection.execute(
                """
                SELECT artifact.recording_id
                FROM recording_audio_artifacts AS artifact
                JOIN recordings AS recording ON recording.id = artifact.recording_id
                WHERE artifact.state = 'pending'
                  AND artifact.attempts < ?
                  AND recording.status = 'queued'
                  AND NOT EXISTS (
                      SELECT 1 FROM transcription_leases
                      WHERE recording_id = recording.id
                  )
                ORDER BY recording.created_at ASC, recording.id ASC
                LIMIT 1
                """,
                (max_attempts,),
            ).fetchone()
            if row is None:
                return None

            recording_id = str(row["recording_id"])
            cursor = connection.execute(
                """
                UPDATE recording_audio_artifacts
                SET state = 'processing', stored_filename = NULL, content_type = NULL,
                    size_bytes = NULL, sha256 = NULL, attempts = attempts + 1,
                    error = NULL, updated_at = ?
                WHERE recording_id = ? AND state = 'pending'
                """,
                (timestamp, recording_id),
            )
            if cursor.rowcount != 1:  # pragma: no cover - BEGIN IMMEDIATE serializes preparers
                return None
            recording_row = connection.execute(
                f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE id = ?",
                (recording_id,),
            ).fetchone()
            artifact_row = connection.execute(
                f"SELECT {AUDIO_ARTIFACT_COLUMNS} FROM recording_audio_artifacts WHERE recording_id = ?",
                (recording_id,),
            ).fetchone()

        recording = self._decode(recording_row)
        artifact = self._decode_audio_artifact(artifact_row)
        if recording is None or artifact is None:  # pragma: no cover - committed FK invariant
            raise RuntimeError("Could not read the claimed audio preparation job")
        recording["audio_artifact"] = artifact
        return recording

    def mark_audio_prepared(
        self,
        recording_id: str,
        *,
        stored_filename: str,
        size_bytes: int,
        sha256: str,
        content_type: str = "audio/ogg",
    ) -> bool:
        if Path(stored_filename).name != stored_filename or stored_filename in (
            "",
            ".",
            "..",
        ):
            raise ValueError("stored_filename must be a safe filename")
        if size_bytes <= 0:
            raise ValueError("size_bytes must be greater than zero")
        if content_type != "audio/ogg":
            raise ValueError("prepared community audio must use audio/ogg")
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")

        timestamp = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE recording_audio_artifacts
                SET state = 'ready', stored_filename = ?, content_type = ?, size_bytes = ?,
                    sha256 = ?, error = NULL, updated_at = ?
                WHERE recording_id = ? AND state = 'processing'
                """,
                (
                    stored_filename,
                    content_type,
                    size_bytes,
                    sha256,
                    timestamp,
                    recording_id,
                ),
            )
        return cursor.rowcount == 1

    def mark_audio_preparation_failed(self, recording_id: str, error: str) -> bool:
        message = error.strip()[:2000] or "Audio preparation failed"
        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE recording_audio_artifacts
                SET state = 'failed', stored_filename = NULL, content_type = NULL,
                    size_bytes = NULL, sha256 = NULL, error = ?, updated_at = ?
                WHERE recording_id = ? AND state = 'processing'
                """,
                (message, timestamp, recording_id),
            )
            if cursor.rowcount == 1:
                connection.execute(
                    """
                    UPDATE recordings
                    SET status = 'failed', error = ?, updated_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (message, timestamp, recording_id),
                )
        return cursor.rowcount == 1

    def release_audio_preparation(
        self,
        recording_id: str,
        *,
        error: str,
        max_attempts: int,
    ) -> Literal["pending", "failed"] | None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")
        message = error.strip()[:2000] or "Audio preparation failed"
        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT attempts
                FROM recording_audio_artifacts
                WHERE recording_id = ? AND state = 'processing'
                """,
                (recording_id,),
            ).fetchone()
            if row is None:
                return None
            next_state: Literal["pending", "failed"] = "pending" if int(row["attempts"]) < max_attempts else "failed"
            cursor = connection.execute(
                """
                UPDATE recording_audio_artifacts
                SET state = ?, stored_filename = NULL, content_type = NULL,
                    size_bytes = NULL, sha256 = NULL, error = ?, updated_at = ?
                WHERE recording_id = ? AND state = 'processing'
                """,
                (next_state, message, timestamp, recording_id),
            )
            if cursor.rowcount != 1:  # pragma: no cover - transaction owns the transition
                return None
            if next_state == "failed":
                connection.execute(
                    """
                    UPDATE recordings
                    SET status = 'failed', error = ?, updated_at = ?
                    WHERE id = ? AND status = 'queued'
                    """,
                    (message, timestamp, recording_id),
                )
        return next_state

    def recover_audio_preparation(self) -> int:
        timestamp = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE recording_audio_artifacts
                SET state = 'pending', stored_filename = NULL, content_type = NULL,
                    size_bytes = NULL, sha256 = NULL, attempts = MAX(attempts - 1, 0),
                    error = NULL, updated_at = ?
                WHERE state = 'processing'
                """,
                (timestamp,),
            )
        return cursor.rowcount

    def reset_audio_preparation(self, recording_id: str) -> bool:
        """Return an interrupted preparation claim to the pending queue."""

        timestamp = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE recording_audio_artifacts
                SET state = 'pending', stored_filename = NULL, content_type = NULL,
                    size_bytes = NULL, sha256 = NULL, attempts = MAX(attempts - 1, 0),
                    error = NULL, updated_at = ?
                WHERE recording_id = ? AND state = 'processing'
                """,
                (timestamp, recording_id),
            )
        return cursor.rowcount == 1

    def invalidate_audio_artifact(self, recording_id: str) -> bool:
        """Reset a corrupt/missing ready artifact and safely revoke any lease using it."""

        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE recording_audio_artifacts
                SET state = 'pending', stored_filename = NULL, content_type = NULL,
                    size_bytes = NULL, sha256 = NULL, attempts = 0,
                    error = NULL, updated_at = ?
                WHERE recording_id = ? AND state = 'ready'
                """,
                (timestamp, recording_id),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                """
                UPDATE recordings
                SET status = 'queued', attempts = MAX(attempts - 1, 0), error = NULL,
                    result_id = NULL, result_hash = NULL, engine = NULL, model = NULL,
                    engine_version = NULL, client_version = NULL, updated_at = ?
                WHERE id = ? AND status = 'processing'
                  AND EXISTS (
                      SELECT 1 FROM transcription_leases
                      WHERE recording_id = recordings.id
                  )
                """,
                (timestamp, recording_id),
            )
            connection.execute(
                "DELETE FROM transcription_leases WHERE recording_id = ?",
                (recording_id,),
            )
        return True

    def list(
        self,
        *,
        lesson_key: str | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = []
        from_clause = "FROM recordings AS recording"
        where_clause = ""
        if lesson_key is not None:
            from_clause = """
                FROM recording_lesson_keys AS alias
                JOIN recordings AS recording ON recording.id = alias.recording_id
            """
            where_clause = "WHERE alias.lesson_key = ?"
            parameters.append(lesson_key)
        parameters.extend((limit, offset))

        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {RECORDING_SEARCH_SUMMARY_COLUMNS}
                {from_clause}
                {where_clause}
                ORDER BY COALESCE(recording.recorded_at, substr(recording.created_at, 1, 10)) DESC,
                         recording.created_at DESC, recording.id DESC
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        return [recording for row in rows if (recording := self._decode(row)) is not None]

    def search_recordings(
        self,
        *,
        group_id: str,
        query: str,
        scope: Literal["all", "subject", "lecturer", "transcript"],
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        tokens = recording_search_tokens(query)
        column_by_scope = {
            "all": None,
            "subject": "lesson_title",
            "lecturer": "lecturer_name",
            "transcript": "transcript_text",
        }
        if scope not in column_by_scope:
            raise ValueError("Unsupported recording search scope")

        with self._connection() as connection:
            if tokens:
                expression = _fts_expression(tokens, column=column_by_scope[scope])
                base_from = """
                    FROM recording_search_fts
                    JOIN recording_search_documents AS document
                      ON document.id = recording_search_fts.rowid
                    JOIN recordings AS recording ON recording.id = document.recording_id
                    JOIN recording_groups AS recording_group
                      ON recording_group.recording_id = recording.id
                    WHERE recording_search_fts MATCH ?
                      AND recording_group.group_id = ?
                      AND recording.status = 'ready'
                """
                total = int(
                    connection.execute(
                        f"SELECT COUNT(*) {base_from}",
                        (expression, group_id),
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    f"""
                    SELECT {RECORDING_SEARCH_SUMMARY_COLUMNS},
                           recording_group.group_id,
                           recording_group.group_label,
                           bm25(recording_search_fts, 5.0, 4.0, 1.0) AS search_rank
                    {base_from}
                    ORDER BY search_rank,
                             COALESCE(recording.recorded_at, substr(recording.created_at, 1, 10)) DESC,
                             recording.created_at DESC,
                             recording.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (expression, group_id, limit, offset),
                ).fetchall()
            else:
                base_from = """
                    FROM recordings AS recording
                    JOIN recording_groups AS recording_group
                      ON recording_group.recording_id = recording.id
                    WHERE recording_group.group_id = ? AND recording.status = 'ready'
                """
                total = int(connection.execute(f"SELECT COUNT(*) {base_from}", (group_id,)).fetchone()[0])
                rows = connection.execute(
                    f"""
                    SELECT {RECORDING_SEARCH_SUMMARY_COLUMNS},
                           recording_group.group_id,
                           recording_group.group_label
                    {base_from}
                    ORDER BY COALESCE(recording.recorded_at, substr(recording.created_at, 1, 10)) DESC,
                             recording.created_at DESC,
                             recording.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (group_id, limit, offset),
                ).fetchall()

            segments_by_recording: dict[str, dict[str, Any]] = {}
            recording_ids = [str(row["id"]) for row in rows]
            if tokens and scope in ("all", "transcript") and recording_ids:
                placeholders = ", ".join("?" for _ in recording_ids)
                segment_rows = connection.execute(
                    f"""
                    SELECT segment.recording_id, segment.segment_id, segment.start,
                           segment.end, segment.text,
                           bm25(recording_segment_fts) AS search_rank
                    FROM recording_segment_fts
                    JOIN recording_search_segments AS segment
                      ON segment.id = recording_segment_fts.rowid
                    WHERE recording_segment_fts MATCH ?
                      AND segment.recording_id IN ({placeholders})
                    ORDER BY search_rank, segment.start, segment.segment_id
                    """,
                    (_fts_expression(tokens, operator="OR"), *recording_ids),
                ).fetchall()
                for segment in segment_rows:
                    segments_by_recording.setdefault(
                        str(segment["recording_id"]),
                        {
                            "id": int(segment["segment_id"]),
                            "start": float(segment["start"]),
                            "end": float(segment["end"]),
                            "text": str(segment["text"]),
                        },
                    )

        items: list[dict[str, Any]] = []
        for row in rows:
            item = self._decode(row)
            if item is None:  # pragma: no cover - a selected row always decodes
                continue
            item.pop("search_rank", None)
            matched_fields: list[str] = []
            if tokens and _contains_search_token(str(item["lesson_title"]), tokens):
                matched_fields.append("lesson_title")
            if tokens and _contains_search_token(str(item.get("lecturer_name") or ""), tokens):
                matched_fields.append("lecturer_name")
            segment = segments_by_recording.get(str(item["id"]))
            if segment is not None:
                matched_fields.append("transcript")
            item["matched_fields"] = matched_fields
            item["match_segment"] = segment
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def list_all_recordings(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {RECORDING_SUMMARY_COLUMNS}
                FROM recordings
                ORDER BY COALESCE(recorded_at, substr(created_at, 1, 10)) DESC, created_at DESC, id DESC
                """
            ).fetchall()
        return [recording for row in rows if (recording := self._decode(row)) is not None]

    def delete_finished_recording(
        self,
        recording_id: str,
    ) -> Literal["deleted", "not_found", "active"]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM recordings WHERE id = ?",
                (recording_id,),
            ).fetchone()
            if row is None:
                return "not_found"
            if row["status"] not in ("ready", "failed"):
                return "active"

            cursor = connection.execute(
                "DELETE FROM recordings WHERE id = ? AND status IN ('ready', 'failed')",
                (recording_id,),
            )
            if cursor.rowcount != 1:  # pragma: no cover - transaction lock makes this defensive
                return "active"
            return "deleted"

    def create_attachment(
        self,
        *,
        attachment_id: str,
        lesson_key: str,
        lesson_title: str,
        scope_label: str,
        original_filename: str,
        stored_filename: str,
        content_type: str,
        size_bytes: int,
        recorded_at: str | None,
        groups: Iterable[tuple[str, str]] = (),
        lecturer_name: str | None = None,
        lesson_keys: Iterable[str] = (),
    ) -> dict[str, Any]:
        timestamp = utc_now()
        effective_recorded_at = recorded_at or timestamp[:10]
        normalized_groups = _normalized_groups(groups)
        normalized_lesson_keys = _normalized_lesson_keys(lesson_key, lesson_keys)
        normalized_lecturer_name = (
            unicodedata.normalize("NFC", lecturer_name).strip() if lecturer_name is not None else None
        )
        if normalized_lecturer_name is not None and (
            not normalized_lecturer_name or len(normalized_lecturer_name) > MAX_COMMUNITY_METADATA_LENGTH
        ):
            raise ValueError("lecturer_name must contain between 1 and 200 characters")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO attachments (
                    id, lesson_key, lesson_title, scope_label, lecturer_name, original_filename,
                    stored_filename, content_type, size_bytes, recorded_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment_id,
                    lesson_key,
                    lesson_title,
                    scope_label,
                    normalized_lecturer_name,
                    original_filename,
                    stored_filename,
                    content_type,
                    size_bytes,
                    effective_recorded_at,
                    timestamp,
                    timestamp,
                ),
            )
            connection.executemany(
                """
                INSERT INTO attachment_groups (attachment_id, group_id, group_label)
                VALUES (?, ?, ?)
                """,
                [(attachment_id, group_id, group_label) for group_id, group_label in normalized_groups],
            )
            connection.executemany(
                """
                INSERT INTO attachment_lesson_keys (attachment_id, lesson_key)
                VALUES (?, ?)
                """,
                [(attachment_id, alias) for alias in normalized_lesson_keys],
            )
            self._backfill_recording_context_locked(
                connection,
                recording_ids=(),
                attachment_ids=(attachment_id,),
            )
            self._replace_attachment_search_locked(connection, attachment_id)
            row = connection.execute(
                f"SELECT {ATTACHMENT_COLUMNS} FROM attachments WHERE id = ?",
                (attachment_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - guards an impossible successful insert state
            raise RuntimeError("Could not read the newly created attachment")
        return dict(row)

    def get_attachment(self, attachment_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {ATTACHMENT_COLUMNS} FROM attachments WHERE id = ?",
                (attachment_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_attachments(
        self,
        *,
        lesson_key: str | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = []
        from_clause = "FROM attachments AS attachment"
        where_clause = ""
        if lesson_key is not None:
            from_clause = """
                FROM attachment_lesson_keys AS alias
                JOIN attachments AS attachment ON attachment.id = alias.attachment_id
            """
            where_clause = "WHERE alias.lesson_key = ?"
            parameters.append(lesson_key)
        parameters.extend((limit, offset))

        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {ATTACHMENT_SEARCH_SUMMARY_COLUMNS}
                {from_clause}
                {where_clause}
                ORDER BY attachment.recorded_at DESC, attachment.created_at DESC, attachment.id DESC
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def search_attachments(
        self,
        *,
        group_id: str,
        query: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        tokens = recording_search_tokens(query)
        with self._connection() as connection:
            if tokens:
                expression = _fts_expression(tokens)
                base_from = """
                    FROM attachment_search_fts
                    JOIN attachment_search_documents AS document
                      ON document.id = attachment_search_fts.rowid
                    JOIN attachments AS attachment ON attachment.id = document.attachment_id
                    JOIN attachment_groups AS attachment_group
                      ON attachment_group.attachment_id = attachment.id
                    WHERE attachment_search_fts MATCH ? AND attachment_group.group_id = ?
                """
                total = int(
                    connection.execute(
                        f"SELECT COUNT(*) {base_from}",
                        (expression, group_id),
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    f"""
                    SELECT {ATTACHMENT_SEARCH_SUMMARY_COLUMNS},
                           attachment_group.group_id,
                           attachment_group.group_label,
                           bm25(attachment_search_fts, 5.0, 3.0) AS search_rank
                    {base_from}
                    ORDER BY search_rank, attachment.recorded_at DESC,
                             attachment.created_at DESC, attachment.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (expression, group_id, limit, offset),
                ).fetchall()
            else:
                base_from = """
                    FROM attachments AS attachment
                    JOIN attachment_groups AS attachment_group
                      ON attachment_group.attachment_id = attachment.id
                    WHERE attachment_group.group_id = ?
                """
                total = int(connection.execute(f"SELECT COUNT(*) {base_from}", (group_id,)).fetchone()[0])
                rows = connection.execute(
                    f"""
                    SELECT {ATTACHMENT_SEARCH_SUMMARY_COLUMNS},
                           attachment_group.group_id,
                           attachment_group.group_label
                    {base_from}
                    ORDER BY attachment.recorded_at DESC, attachment.created_at DESC, attachment.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (group_id, limit, offset),
                ).fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("search_rank", None)
            matched_fields: list[str] = []
            if tokens and _contains_search_token(str(item["lesson_title"]), tokens):
                matched_fields.append("lesson_title")
            if tokens and _contains_search_token(str(item["original_filename"]), tokens):
                matched_fields.append("file_name")
            item["matched_fields"] = matched_fields
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @staticmethod
    def _decode_schedule_snapshot(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None

        snapshot = dict(row)
        raw_schedule = snapshot.pop("schedule_json", None)
        if raw_schedule is not None:
            snapshot["schedule"] = json.loads(raw_schedule)
        snapshot["scope_key"] = f"{snapshot['scope_type']}:{snapshot['scope_id']}"
        return snapshot

    def get_schedule_snapshot(
        self,
        *,
        scope_type: str,
        scope_id: str,
        week_start: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT {SCHEDULE_SNAPSHOT_COLUMNS}
                FROM schedule_snapshots
                WHERE scope_type = ? AND scope_id = ? AND week_start = ?
                """,
                (scope_type, scope_id, week_start),
            ).fetchone()
        return self._decode_schedule_snapshot(row)

    def list_schedule_snapshots(
        self,
        *,
        scope_type: str,
        scope_id: str,
        from_week: str | None,
        to_week: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        clauses = ["scope_type = ?", "scope_id = ?"]
        parameters: list[Any] = [scope_type, scope_id]
        if from_week is not None:
            clauses.append("week_start >= ?")
            parameters.append(from_week)
        if to_week is not None:
            clauses.append("week_start <= ?")
            parameters.append(to_week)
        parameters.append(limit)

        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {SCHEDULE_SNAPSHOT_METADATA_COLUMNS}
                FROM schedule_snapshots
                WHERE {" AND ".join(clauses)}
                ORDER BY week_start DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [snapshot for row in rows if (snapshot := self._decode_schedule_snapshot(row)) is not None]

    @staticmethod
    def _snapshot_material_ids_locked(
        connection: sqlite3.Connection,
        *,
        table: Literal["recordings", "attachments"],
        group_table: Literal["recording_groups", "attachment_groups"],
        material_id_column: Literal["recording_id", "attachment_id"],
        scope_type: str,
        scope_id: str,
        scope_label: str,
        week_start: str,
        week_end: str,
    ) -> tuple[str, ...]:
        material_date = (
            "COALESCE(material.recorded_at, substr(material.created_at, 1, 10))"
            if table == "recordings"
            else "material.recorded_at"
        )
        if scope_type == "group":
            scope_clause = f"""
                material.scope_label = ? OR EXISTS (
                    SELECT 1
                    FROM {group_table} AS material_group
                    WHERE material_group.{material_id_column} = material.id
                      AND material_group.group_id = ?
                )
            """
            scope_parameters = (scope_label, scope_id)
        else:
            scope_clause = "material.lecturer_name = ?"
            scope_parameters = (scope_label,)

        rows = connection.execute(
            f"""
            SELECT material.id
            FROM {table} AS material
            WHERE {material_date} BETWEEN ? AND ?
              AND ({scope_clause})
            """,
            (week_start, week_end, *scope_parameters),
        ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def put_schedule_snapshot(
        self,
        *,
        scope_type: str,
        scope_id: str,
        scope_label: str,
        week_start: str,
        week_end: str,
        schedule: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        schedule_json = json.dumps(
            schedule,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(schedule_json.encode("utf-8")) > MAX_SCHEDULE_SNAPSHOT_BYTES:
            raise ScheduleSnapshotTooLargeError
        content_hash = hashlib.sha256(f"{scope_label}\0{schedule_json}".encode()).hexdigest()
        timestamp = utc_now()

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = connection.execute(
                f"""
                SELECT {SCHEDULE_SNAPSHOT_COLUMNS}
                FROM schedule_snapshots
                WHERE scope_type = ? AND scope_id = ? AND week_start = ?
                """,
                (scope_type, scope_id, week_start),
            ).fetchone()
            if existing_row is not None:
                existing = self._decode_schedule_snapshot(existing_row)
                if existing is None:  # pragma: no cover - guards an impossible selected row state
                    raise RuntimeError("Could not decode the existing schedule snapshot")
                if existing["scope_label"] != scope_label or existing["schedule"] != schedule:
                    raise ScheduleSnapshotConflictError
                return existing, False

            connection.execute(
                """
                INSERT INTO schedule_snapshots (
                    scope_type, scope_id, scope_label, week_start, week_end,
                    schedule_json, content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scope_type,
                    scope_id,
                    scope_label,
                    week_start,
                    week_end,
                    schedule_json,
                    content_hash,
                    timestamp,
                ),
            )
            recording_ids = self._snapshot_material_ids_locked(
                connection,
                table="recordings",
                group_table="recording_groups",
                material_id_column="recording_id",
                scope_type=scope_type,
                scope_id=scope_id,
                scope_label=scope_label,
                week_start=week_start,
                week_end=week_end,
            )
            attachment_ids = self._snapshot_material_ids_locked(
                connection,
                table="attachments",
                group_table="attachment_groups",
                material_id_column="attachment_id",
                scope_type=scope_type,
                scope_id=scope_id,
                scope_label=scope_label,
                week_start=week_start,
                week_end=week_end,
            )
            self._backfill_recording_context_locked(
                connection,
                recording_ids=recording_ids,
                attachment_ids=attachment_ids,
            )
            for recording_id in recording_ids:
                self._replace_recording_search_locked(connection, recording_id)
            row = connection.execute(
                f"""
                SELECT {SCHEDULE_SNAPSHOT_COLUMNS}
                FROM schedule_snapshots
                WHERE scope_type = ? AND scope_id = ? AND week_start = ?
                """,
                (scope_type, scope_id, week_start),
            ).fetchone()
            snapshot = self._decode_schedule_snapshot(row)

        if snapshot is None:  # pragma: no cover - guards an impossible successful insert state
            raise RuntimeError("Could not read the newly created schedule snapshot")
        return snapshot, True

    @staticmethod
    def _reap_expired_remote_locked(connection: sqlite3.Connection, timestamp: str) -> int:
        cursor = connection.execute(
            """
            UPDATE recordings
            SET status = 'queued', error = NULL, result_id = NULL, result_hash = NULL,
                engine = NULL, model = NULL, engine_version = NULL, client_version = NULL,
                updated_at = ?
            WHERE status = 'processing'
              AND EXISTS (
                  SELECT 1
                  FROM transcription_leases AS lease
                  LEFT JOIN community_workers AS worker ON worker.id = lease.worker_id
                  WHERE lease.recording_id = recordings.id
                    AND (lease.lease_expires_at <= ? OR worker.revoked_at IS NOT NULL)
              )
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            DELETE FROM transcription_leases
            WHERE recording_id IN (
                SELECT id
                FROM recordings
                WHERE status IN ('queued', 'failed')
            )
            """
        )
        return cursor.rowcount

    @classmethod
    def _recover_interrupted_locked(cls, connection: sqlite3.Connection, timestamp: str) -> int:
        recovered = cls._reap_expired_remote_locked(connection, timestamp)
        cursor = connection.execute(
            """
            UPDATE recordings
            SET status = 'queued', error = NULL, result_id = NULL, result_hash = NULL,
                engine = NULL, model = NULL, engine_version = NULL, client_version = NULL,
                updated_at = ?
            WHERE status = 'processing'
              AND NOT EXISTS (
                  SELECT 1
                  FROM transcription_leases AS lease
                  JOIN community_workers AS worker ON worker.id = lease.worker_id
                  WHERE lease.recording_id = recordings.id
                    AND lease.lease_expires_at > ?
                    AND worker.revoked_at IS NULL
              )
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            DELETE FROM transcription_leases
            WHERE recording_id IN (
                SELECT id
                FROM recordings
                WHERE status IN ('queued', 'failed')
            )
            """
        )
        return recovered + cursor.rowcount

    def recover_interrupted(self) -> int:
        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._recover_interrupted_locked(connection, timestamp)

    def claim_next(self) -> dict[str, Any] | None:
        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id
                FROM recordings
                WHERE status = 'queued'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM transcription_leases
                      WHERE recording_id = recordings.id
                  )
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None

            cursor = connection.execute(
                """
                UPDATE recordings
                SET status = 'processing', attempts = attempts + 1, error = NULL,
                    result_id = NULL, result_hash = NULL, engine = NULL, model = NULL,
                    engine_version = NULL, client_version = NULL, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (timestamp, row["id"]),
            )
            if cursor.rowcount != 1:
                return None

            claimed = connection.execute(
                f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE id = ?",
                (row["id"],),
            ).fetchone()
            return self._decode(claimed)

    def mark_ready(self, recording_id: str, result: TranscriptionResult) -> bool:
        timestamp = utc_now()
        validated_result = TranscriptionPayload.model_validate(
            {
                "transcript": result.transcript,
                "duration_seconds": result.duration_seconds,
                "detected_language": result.detected_language,
                "language_probability": result.language_probability,
            }
        )
        transcript_json = json.dumps(
            [segment.model_dump(mode="json") for segment in validated_result.transcript],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE recordings
                SET status = 'ready', duration_seconds = ?, detected_language = ?,
                    language_probability = ?, transcript_json = ?, error = NULL,
                    result_id = NULL, result_hash = NULL, engine = NULL, model = NULL,
                    engine_version = NULL, client_version = NULL, updated_at = ?
                WHERE id = ? AND status = 'processing'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM transcription_leases
                      WHERE recording_id = recordings.id
                  )
                """,
                (
                    validated_result.duration_seconds,
                    validated_result.detected_language,
                    validated_result.language_probability,
                    transcript_json,
                    timestamp,
                    recording_id,
                ),
            )
            updated = cursor.rowcount == 1
            if updated:
                self._replace_recording_search_locked(connection, recording_id)
        return updated

    def mark_failed(self, recording_id: str, error: str) -> bool:
        timestamp = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE recordings
                SET status = 'failed', error = ?, updated_at = ?
                WHERE id = ? AND status = 'processing'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM transcription_leases
                      WHERE recording_id = recordings.id
                  )
                """,
                (error[:2000], timestamp, recording_id),
            )
            updated = cursor.rowcount == 1
        return updated

    def retry(self, recording_id: str) -> dict[str, Any] | None:
        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE recordings
                SET status = 'queued', attempts = 0, error = NULL, duration_seconds = NULL,
                    detected_language = NULL, language_probability = NULL,
                    transcript_json = NULL, result_id = NULL, result_hash = NULL,
                    engine = NULL, model = NULL, engine_version = NULL,
                    client_version = NULL, updated_at = ?
                WHERE id = ? AND status = 'failed'
                """,
                (timestamp, recording_id),
            )
            if cursor.rowcount != 1:
                return None
            connection.execute(
                "DELETE FROM transcription_leases WHERE recording_id = ?",
                (recording_id,),
            )
            connection.execute(
                """
                UPDATE recording_audio_artifacts
                SET state = CASE WHEN state = 'failed' THEN 'pending' ELSE state END,
                    stored_filename = CASE WHEN state = 'failed' THEN NULL ELSE stored_filename END,
                    content_type = CASE WHEN state = 'failed' THEN NULL ELSE content_type END,
                    size_bytes = CASE WHEN state = 'failed' THEN NULL ELSE size_bytes END,
                    sha256 = CASE WHEN state = 'failed' THEN NULL ELSE sha256 END,
                    attempts = CASE WHEN state = 'failed' THEN 0 ELSE attempts END,
                    error = CASE WHEN state = 'failed' THEN NULL ELSE error END,
                    updated_at = ?
                WHERE recording_id = ?
                """,
                (timestamp, recording_id),
            )
            row = connection.execute(
                f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE id = ?",
                (recording_id,),
            ).fetchone()
        return self._decode(row)

    def get_transcription_lease(self, recording_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT {TRANSCRIPTION_LEASE_COLUMNS} FROM transcription_leases WHERE recording_id = ?",
                (recording_id,),
            ).fetchone()
        return self._decode_lease(row)

    @staticmethod
    def _lease_row_matches(
        row: sqlite3.Row | None,
        *,
        worker_id: str,
        claim_id: str,
        lease_token_hash: str,
    ) -> bool:
        if row is None or row["worker_id"] != worker_id or row["claim_id"] != claim_id:
            return False
        try:
            return hmac.compare_digest(str(row["lease_token_hash"]), lease_token_hash)
        except (TypeError, UnicodeError):
            return False

    @staticmethod
    def _remote_lease_row_locked(
        connection: sqlite3.Connection,
        recording_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT lease.recording_id, lease.worker_id, lease.claim_id,
                   lease.lease_token_hash, lease.leased_at, lease.lease_expires_at,
                   lease.heartbeat_at, recording.status, recording.attempts,
                   recording.result_id, recording.result_hash, recording.engine,
                   recording.model, recording.engine_version, recording.client_version,
                   worker.revoked_at
            FROM transcription_leases AS lease
            JOIN recordings AS recording ON recording.id = lease.recording_id
            JOIN community_workers AS worker ON worker.id = lease.worker_id
            WHERE lease.recording_id = ?
            """,
            (recording_id,),
        ).fetchone()

    def validate_remote_lease(
        self,
        *,
        recording_id: str,
        worker_id: str,
        claim_id: str,
        lease_token_hash: str,
    ) -> dict[str, Any] | None:
        timestamp = utc_now()
        with self._connection() as connection:
            row = self._remote_lease_row_locked(connection, recording_id)
        if (
            not self._lease_row_matches(
                row,
                worker_id=worker_id,
                claim_id=claim_id,
                lease_token_hash=lease_token_hash,
            )
            or row is None
            or row["status"] != "processing"
            or row["revoked_at"] is not None
            or row["lease_expires_at"] <= timestamp
        ):
            return None
        return self._decode_lease(row)

    def claim_remote(
        self,
        *,
        worker_id: str,
        claim_id: str,
        lease_token_hash: str,
        lease_seconds: int,
        max_attempts: int = 5,
    ) -> dict[str, Any] | None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be greater than zero")
        normalized_worker_id = _community_metadata(worker_id, "worker_id", required=True)
        normalized_claim_id = _community_metadata(claim_id, "claim_id", required=True)
        normalized_lease_hash = _community_metadata(lease_token_hash, "lease_token_hash", required=True)
        timestamp = utc_now()
        lease_expires_at = _lease_expiration(timestamp, lease_seconds)

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reap_expired_remote_locked(connection, timestamp)
            connection.execute(
                """
                UPDATE recordings
                SET status = 'failed', error = 'Community transcription attempt limit reached', updated_at = ?
                WHERE status = 'queued' AND attempts >= ?
                """,
                (timestamp, max_attempts),
            )
            worker = connection.execute(
                """
                SELECT id
                FROM community_workers
                WHERE id = ? AND revoked_at IS NULL
                """,
                (normalized_worker_id,),
            ).fetchone()
            if worker is None:
                return None

            connection.execute(
                "UPDATE community_workers SET last_seen_at = ? WHERE id = ?",
                (timestamp, normalized_worker_id),
            )
            active_lease = connection.execute(
                """
                SELECT 1
                FROM transcription_leases AS lease
                JOIN recordings AS recording ON recording.id = lease.recording_id
                WHERE lease.worker_id = ?
                  AND recording.status = 'processing'
                  AND lease.lease_expires_at > ?
                LIMIT 1
                """,
                (normalized_worker_id, timestamp),
            ).fetchone()
            if active_lease is not None:
                return None

            queued = connection.execute(
                """
                SELECT recording.id
                FROM recordings AS recording
                JOIN recording_audio_artifacts AS artifact ON artifact.recording_id = recording.id
                WHERE recording.status = 'queued'
                  AND artifact.state = 'ready'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM transcription_leases
                      WHERE recording_id = recording.id
                  )
                ORDER BY recording.created_at ASC, recording.id ASC
                LIMIT 1
                """
            ).fetchone()
            if queued is None:
                return None

            cursor = connection.execute(
                """
                UPDATE recordings
                SET status = 'processing', attempts = attempts + 1, error = NULL,
                    result_id = NULL, result_hash = NULL, engine = NULL, model = NULL,
                    engine_version = NULL, client_version = NULL, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (timestamp, queued["id"]),
            )
            if cursor.rowcount != 1:  # pragma: no cover - BEGIN IMMEDIATE serializes claimers
                return None
            connection.execute(
                """
                INSERT INTO transcription_leases (
                    recording_id, worker_id, claim_id, lease_token_hash,
                    leased_at, lease_expires_at, heartbeat_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    queued["id"],
                    normalized_worker_id,
                    normalized_claim_id,
                    normalized_lease_hash,
                    timestamp,
                    lease_expires_at,
                    timestamp,
                ),
            )
            recording_row = connection.execute(
                f"SELECT {RECORDING_COLUMNS} FROM recordings WHERE id = ?",
                (queued["id"],),
            ).fetchone()
            lease_row = connection.execute(
                f"SELECT {TRANSCRIPTION_LEASE_COLUMNS} FROM transcription_leases WHERE recording_id = ?",
                (queued["id"],),
            ).fetchone()

        recording = self._decode(recording_row)
        lease = self._decode_lease(lease_row)
        if recording is None or lease is None:  # pragma: no cover - guards committed claim invariants
            raise RuntimeError("Could not read the newly claimed transcription job")
        artifact = self.get_audio_artifact(str(recording["id"]))
        if artifact is None or artifact["state"] != "ready":  # pragma: no cover - transaction selected ready only
            raise RuntimeError("Claimed transcription job has no prepared audio")
        recording["audio_artifact"] = artifact
        recording["lease"] = lease
        return recording

    def heartbeat_remote(
        self,
        *,
        recording_id: str,
        worker_id: str,
        claim_id: str,
        lease_token_hash: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        timestamp = utc_now()
        lease_expires_at = _lease_expiration(timestamp, lease_seconds)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reap_expired_remote_locked(connection, timestamp)
            row = self._remote_lease_row_locked(connection, recording_id)
            if (
                not self._lease_row_matches(
                    row,
                    worker_id=worker_id,
                    claim_id=claim_id,
                    lease_token_hash=lease_token_hash,
                )
                or row is None
                or row["status"] != "processing"
                or row["revoked_at"] is not None
                or row["lease_expires_at"] <= timestamp
            ):
                return None
            connection.execute(
                """
                UPDATE transcription_leases
                SET lease_expires_at = ?, heartbeat_at = ?
                WHERE recording_id = ?
                """,
                (lease_expires_at, timestamp, recording_id),
            )
            connection.execute(
                "UPDATE community_workers SET last_seen_at = ? WHERE id = ?",
                (timestamp, worker_id),
            )
            renewed = connection.execute(
                f"SELECT {TRANSCRIPTION_LEASE_COLUMNS} FROM transcription_leases WHERE recording_id = ?",
                (recording_id,),
            ).fetchone()
        return self._decode_lease(renewed)

    def release_remote(
        self,
        *,
        recording_id: str,
        worker_id: str,
        claim_id: str,
        lease_token_hash: str,
        error: str,
        retryable: bool,
        max_attempts: int,
        count_attempt: bool = True,
        invalidate_audio: bool = False,
    ) -> Literal["queued", "failed"] | None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be greater than zero")
        message = error.strip()[:2000] or "Community worker failed without an error message"
        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reap_expired_remote_locked(connection, timestamp)
            row = self._remote_lease_row_locked(connection, recording_id)
            if (
                not self._lease_row_matches(
                    row,
                    worker_id=worker_id,
                    claim_id=claim_id,
                    lease_token_hash=lease_token_hash,
                )
                or row is None
                or row["status"] != "processing"
                or row["revoked_at"] is not None
                or row["lease_expires_at"] <= timestamp
            ):
                return None

            next_status = (
                "queued" if invalidate_audio or (retryable and int(row["attempts"]) < max_attempts) else "failed"
            )
            persisted_error = None if next_status == "queued" else message
            cursor = connection.execute(
                """
                UPDATE recordings
                SET status = ?, error = ?,
                    attempts = CASE WHEN ? THEN attempts ELSE MAX(attempts - 1, 0) END,
                    result_id = NULL, result_hash = NULL,
                    engine = NULL, model = NULL, engine_version = NULL,
                    client_version = NULL, updated_at = ?
                WHERE id = ? AND status = 'processing'
                """,
                (
                    next_status,
                    persisted_error,
                    count_attempt and not invalidate_audio,
                    timestamp,
                    recording_id,
                ),
            )
            if cursor.rowcount != 1:  # pragma: no cover - transaction owns the state transition
                return None
            connection.execute(
                "DELETE FROM transcription_leases WHERE recording_id = ?",
                (recording_id,),
            )
            if invalidate_audio:
                connection.execute(
                    """
                    UPDATE recording_audio_artifacts
                    SET state = 'pending', stored_filename = NULL, content_type = NULL,
                        size_bytes = NULL, sha256 = NULL, attempts = 0,
                        error = NULL, updated_at = ?
                    WHERE recording_id = ?
                    """,
                    (timestamp, recording_id),
                )
            connection.execute(
                "UPDATE community_workers SET last_seen_at = ? WHERE id = ?",
                (timestamp, worker_id),
            )
        return next_status

    def complete_remote(
        self,
        *,
        recording_id: str,
        worker_id: str,
        claim_id: str,
        lease_token_hash: str,
        result_id: str,
        payload: TranscriptionPayload,
        engine: str,
        model: str,
        engine_version: str | None,
        client_version: str | None,
    ) -> RemoteCompletionStatus:
        normalized_result_id = _community_metadata(result_id, "result_id", required=True)
        normalized_engine = _community_metadata(engine, "engine", required=True)
        normalized_model = _community_metadata(model, "model", required=True)
        normalized_engine_version = _community_metadata(engine_version, "engine_version")
        normalized_client_version = _community_metadata(client_version, "client_version")
        validated_payload = TranscriptionPayload.model_validate(
            {
                "transcript": payload.transcript,
                "duration_seconds": payload.duration_seconds,
                "detected_language": payload.detected_language,
                "language_probability": payload.language_probability,
            }
        )
        payload_document = validated_payload.model_dump(mode="json")
        canonical_result = json.dumps(
            {
                "payload": payload_document,
                "engine": normalized_engine,
                "model": normalized_model,
                "engine_version": normalized_engine_version,
                "client_version": normalized_client_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        result_hash = hashlib.sha256(canonical_result.encode("utf-8")).hexdigest()
        transcript_json = json.dumps(
            payload_document["transcript"],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        timestamp = utc_now()

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._reap_expired_remote_locked(connection, timestamp)
            row = self._remote_lease_row_locked(connection, recording_id)
            if (
                not self._lease_row_matches(
                    row,
                    worker_id=worker_id,
                    claim_id=claim_id,
                    lease_token_hash=lease_token_hash,
                )
                or row is None
            ):
                return "lease_lost"

            if row["status"] == "ready":
                if (
                    row["result_id"] == normalized_result_id
                    and row["result_hash"] == result_hash
                    and row["engine"] == normalized_engine
                    and row["model"] == normalized_model
                    and row["engine_version"] == normalized_engine_version
                    and row["client_version"] == normalized_client_version
                ):
                    return "duplicate"
                return "conflict"

            if row["status"] != "processing" or row["revoked_at"] is not None or row["lease_expires_at"] <= timestamp:
                return "lease_lost"

            cursor = connection.execute(
                """
                UPDATE recordings
                SET status = 'ready', duration_seconds = ?, detected_language = ?,
                    language_probability = ?, transcript_json = ?, error = NULL,
                    result_id = ?, result_hash = ?, engine = ?, model = ?,
                    engine_version = ?, client_version = ?, updated_at = ?
                WHERE id = ? AND status = 'processing'
                """,
                (
                    validated_payload.duration_seconds,
                    validated_payload.detected_language,
                    validated_payload.language_probability,
                    transcript_json,
                    normalized_result_id,
                    result_hash,
                    normalized_engine,
                    normalized_model,
                    normalized_engine_version,
                    normalized_client_version,
                    timestamp,
                    recording_id,
                ),
            )
            if cursor.rowcount != 1:  # pragma: no cover - transaction owns the state transition
                return "lease_lost"
            self._replace_recording_search_locked(connection, recording_id)
            connection.execute(
                "UPDATE community_workers SET last_seen_at = ? WHERE id = ?",
                (timestamp, worker_id),
            )
        return "completed"

    def status_counts(self) -> dict[str, int]:
        counts = {status: 0 for status in ("queued", "processing", "ready", "failed")}
        with self._connection() as connection:
            rows: Iterable[sqlite3.Row] = connection.execute(
                "SELECT status, COUNT(*) AS amount FROM recordings GROUP BY status"
            ).fetchall()
        for row in rows:
            counts[row["status"]] = row["amount"]
        return counts

    def ping(self) -> None:
        with self._connection() as connection:
            connection.execute("SELECT 1").fetchone()
