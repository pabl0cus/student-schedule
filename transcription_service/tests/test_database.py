from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest

from app.config import Settings
from app.database import RecordingRepository


def test_repository_closes_each_connection(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = RecordingRepository(settings.database_path)
    repository.initialize()
    connection = sqlite3.connect(settings.database_path)
    monkeypatch.setattr(repository, "_connect", lambda: connection)

    repository.ping()

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")


def test_schedule_snapshot_table_is_append_only(settings: Settings) -> None:
    repository = RecordingRepository(settings.database_path)
    repository.initialize()
    repository.put_schedule_snapshot(
        scope_type="group",
        scope_id="6352",
        scope_label="Група ІП-01",
        week_start="2026-08-31",
        week_end="2026-09-06",
        schedule={"scheduleWeek": "firstWeek", "days": []},
    )

    with sqlite3.connect(settings.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="schedule snapshots are immutable"):
            connection.execute(
                """
                UPDATE schedule_snapshots
                SET scope_label = 'Changed'
                WHERE scope_type = 'group' AND scope_id = '6352' AND week_start = '2026-08-31'
                """
            )

        with pytest.raises(sqlite3.IntegrityError, match="schedule snapshots are immutable"):
            connection.execute(
                """
                DELETE FROM schedule_snapshots
                WHERE scope_type = 'group' AND scope_id = '6352' AND week_start = '2026-08-31'
                """
            )


def test_attachment_repository_persists_and_filters_by_lesson(settings: Settings) -> None:
    repository = RecordingRepository(settings.database_path)
    repository.initialize()
    first_id = str(uuid4())
    second_id = str(uuid4())

    created = repository.create_attachment(
        attachment_id=first_id,
        lesson_key="lesson-one",
        lesson_title="First",
        scope_label="Scope",
        original_filename="notes.pdf",
        stored_filename=first_id,
        content_type="application/pdf",
        size_bytes=5,
        recorded_at=None,
    )
    repository.create_attachment(
        attachment_id=second_id,
        lesson_key="lesson-two",
        lesson_title="Second",
        scope_label="Scope",
        original_filename="notes.docx",
        stored_filename=second_id,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=7,
        recorded_at="2026-09-03",
    )

    assert created["id"] == first_id
    assert created["recorded_at"] == created["created_at"][:10]
    assert repository.get_attachment(first_id) == created
    assert [item["id"] for item in repository.list_attachments(lesson_key="lesson-one", limit=100, offset=0)] == [
        first_id
    ]
