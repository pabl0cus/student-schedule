from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from .schemas import TranscriptionPayload
from .transcriber import TranscriptionResult

RECORDING_COLUMNS = """
    id,
    lesson_key,
    lesson_title,
    scope_label,
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

RemoteCompletionStatus = Literal["completed", "duplicate", "conflict", "lease_lost"]


class ScheduleSnapshotConflictError(Exception):
    pass


class ScheduleSnapshotTooLargeError(Exception):
    pass


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
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_recordings_queue ON recordings (status, created_at ASC)"
            )
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
            connection.execute("PRAGMA optimize")

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
    ) -> dict[str, Any]:
        timestamp = utc_now()
        effective_recorded_at = recorded_at or timestamp[:10]
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO recordings (
                    id, lesson_key, lesson_title, scope_label, original_filename,
                    stored_filename, content_type, size_bytes, status, attempts,
                    recorded_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)
                """,
                (
                    recording_id,
                    lesson_key,
                    lesson_title,
                    scope_label,
                    original_filename,
                    stored_filename,
                    content_type,
                    size_bytes,
                    effective_recorded_at,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO recording_audio_artifacts (
                    recording_id, state, attempts, created_at, updated_at
                ) VALUES (?, 'pending', 0, ?, ?)
                """,
                (recording_id, timestamp, timestamp),
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
        if Path(stored_filename).name != stored_filename or stored_filename in ("", ".", ".."):
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
                (stored_filename, content_type, size_bytes, sha256, timestamp, recording_id),
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
            next_state: Literal["pending", "failed"] = (
                "pending" if int(row["attempts"]) < max_attempts else "failed"
            )
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
        where_clause = ""
        if lesson_key is not None:
            where_clause = "WHERE lesson_key = ?"
            parameters.append(lesson_key)
        parameters.extend((limit, offset))

        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {RECORDING_SUMMARY_COLUMNS}
                FROM recordings
                {where_clause}
                ORDER BY COALESCE(recorded_at, substr(created_at, 1, 10)) DESC, created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        return [recording for row in rows if (recording := self._decode(row)) is not None]

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
    ) -> dict[str, Any]:
        timestamp = utc_now()
        effective_recorded_at = recorded_at or timestamp[:10]
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO attachments (
                    id, lesson_key, lesson_title, scope_label, original_filename,
                    stored_filename, content_type, size_bytes, recorded_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment_id,
                    lesson_key,
                    lesson_title,
                    scope_label,
                    original_filename,
                    stored_filename,
                    content_type,
                    size_bytes,
                    effective_recorded_at,
                    timestamp,
                    timestamp,
                ),
            )
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
        where_clause = ""
        if lesson_key is not None:
            where_clause = "WHERE lesson_key = ?"
            parameters.append(lesson_key)
        parameters.extend((limit, offset))

        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {ATTACHMENT_COLUMNS}
                FROM attachments
                {where_clause}
                ORDER BY recorded_at DESC, created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

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
                WHERE {' AND '.join(clauses)}
                ORDER BY week_start DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [snapshot for row in rows if (snapshot := self._decode_schedule_snapshot(row)) is not None]

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
                "queued"
                if invalidate_audio or (retryable and int(row["attempts"]) < max_attempts)
                else "failed"
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
                (next_status, persisted_error, count_attempt and not invalidate_audio, timestamp, recording_id),
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
            if not self._lease_row_matches(
                row,
                worker_id=worker_id,
                claim_id=claim_id,
                lease_token_hash=lease_token_hash,
            ) or row is None:
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

            if (
                row["status"] != "processing"
                or row["revoked_at"] is not None
                or row["lease_expires_at"] <= timestamp
            ):
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
