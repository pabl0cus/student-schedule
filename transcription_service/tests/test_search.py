from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import RecordingRepository
from app.transcriber import TranscriptionResult


def _upload_ready_recording(
    client: TestClient,
    *,
    group_id: str,
    group_label: str,
    title: str,
    lecturer: str,
    transcript: list[dict[str, object]],
) -> dict[str, object]:
    response = client.post(
        "/recordings",
        data={
            "lesson_key": f"lesson-{group_id}-{title}",
            "lesson_title": title,
            "scope_label": f"Група {group_label}",
            "recorded_at": "2026-09-03",
            "context_json": json.dumps(
                {
                    "groups": [{"id": group_id, "label": group_label}],
                    "lecturer_name": lecturer,
                },
                ensure_ascii=False,
            ),
        },
        files={"file": ("lecture.mp3", b"audio", "audio/mpeg")},
    )
    assert response.status_code == 201
    recording = response.json()

    repository: RecordingRepository = client.app.state.repository  # type: ignore[attr-defined]
    claimed = repository.claim_next()
    assert claimed is not None
    assert claimed["id"] == recording["id"]
    assert repository.mark_ready(
        str(recording["id"]),
        TranscriptionResult(
            transcript=transcript,
            duration_seconds=90.0,
            detected_language="uk",
            language_probability=0.99,
        ),
    )
    return recording


def test_recording_search_is_group_scoped_ranked_and_returns_best_segment(
    client: TestClient,
) -> None:
    first = _upload_ready_recording(
        client,
        group_id="6639",
        group_label="ІМ-о61 (ФІОТ)",
        title="Алгоритми та структури даних",
        lecturer="Олександр Коваль",
        transcript=[
            {
                "id": 0,
                "start": 4.0,
                "end": 8.0,
                "text": "Починаємо заняття",
                "words": [],
            },
            {
                "id": 1,
                "start": 42.5,
                "end": 49.0,
                "text": "Бінарний пошук ділить відсортований масив навпіл",
                "words": [],
            },
        ],
    )
    _upload_ready_recording(
        client,
        group_id="other-group",
        group_label="Інша група",
        title="Алгоритми та структури даних",
        lecturer="Олександр Коваль",
        transcript=[
            {"id": 0, "start": 1.0, "end": 3.0, "text": "Бінарний пошук", "words": []},
        ],
    )

    transcript_response = client.get(
        "/recordings/search",
        params={"group_id": "6639", "q": "бінар", "scope": "transcript"},
    )
    assert transcript_response.status_code == 200
    transcript_page = transcript_response.json()
    assert transcript_page["total"] == 1
    assert transcript_page["items"][0]["id"] == first["id"]
    assert transcript_page["items"][0]["lecturer_name"] == "Олександр Коваль"
    assert transcript_page["items"][0]["matched_fields"] == ["transcript"]
    assert transcript_page["items"][0]["match_segment"] == {
        "id": 1,
        "start": 42.5,
        "end": 49.0,
        "text": "Бінарний пошук ділить відсортований масив навпіл",
    }

    lecturer_response = client.get(
        "/recordings/search",
        params={"group_id": "6639", "q": "ков", "scope": "lecturer"},
    )
    assert lecturer_response.status_code == 200
    assert lecturer_response.json()["items"][0]["matched_fields"] == ["lecturer_name"]

    combined_response = client.get(
        "/recordings/search",
        params={"group_id": "6639", "q": "алгор бінар", "scope": "all"},
    )
    assert combined_response.status_code == 200
    assert combined_response.json()["total"] == 1
    assert combined_response.json()["items"][0]["matched_fields"] == [
        "lesson_title",
        "transcript",
    ]

    wrong_scope = client.get(
        "/recordings/search",
        params={"group_id": "6639", "q": "бінар", "scope": "subject"},
    )
    assert wrong_scope.status_code == 200
    assert wrong_scope.json()["total"] == 0

    wrong_group = client.get(
        "/recordings/search",
        params={"group_id": "missing", "q": "бінар", "scope": "all"},
    )
    assert wrong_group.status_code == 200
    assert wrong_group.json()["total"] == 0


def test_material_search_uses_title_filename_group_and_pagination(
    client: TestClient,
) -> None:
    context = json.dumps({"groups": [{"id": "6639", "label": "ІМ-о61 (ФІОТ)"}]}, ensure_ascii=False)
    for file_name, title in (
        ("Конспект.pdf", "Алгоритми"),
        ("Графи.docx", "Дискретна математика"),
    ):
        response = client.post(
            "/attachments",
            data={
                "lesson_key": f"lesson-{file_name}",
                "lesson_title": title,
                "scope_label": "Група ІМ-о61 (ФІОТ)",
                "recorded_at": "2026-09-03",
                "context_json": context,
            },
            files={"file": (file_name, b"notes", "application/octet-stream")},
        )
        assert response.status_code == 201

    filename_result = client.get(
        "/attachments/search",
        params={"group_id": "6639", "q": "конс", "limit": 1, "offset": 0},
    )
    assert filename_result.status_code == 200
    filename_page = filename_result.json()
    assert filename_page["total"] == 1
    assert filename_page["limit"] == 1
    assert filename_page["items"][0]["matched_fields"] == ["file_name"]
    assert filename_page["items"][0]["content_url"].endswith(f"/attachments/{filename_page['items'][0]['id']}/content")

    title_result = client.get(
        "/attachments/search",
        params={"group_id": "6639", "q": "дискрет"},
    )
    assert title_result.status_code == 200
    assert title_result.json()["items"][0]["matched_fields"] == ["lesson_title"]

    assert client.get("/attachments/search", params={"group_id": "other", "q": "конс"}).json()["total"] == 0


def test_search_validates_context_and_non_searchable_queries(
    client: TestClient,
) -> None:
    invalid_context = client.post(
        "/recordings",
        data={
            "lesson_key": "lesson",
            "lesson_title": "Алгоритми",
            "scope_label": "Група ІМ-01",
            "context_json": '{"groups":[{"id":"bad id","label":"ІМ-01"}]}',
        },
        files={"file": ("lecture.mp3", b"audio", "audio/mpeg")},
    )
    assert invalid_context.status_code == 422
    assert invalid_context.json()["detail"] == "context_json is invalid"

    too_short = client.get("/recordings/search", params={"group_id": "6639", "q": "а"})
    assert too_short.status_code == 422
    assert "at least two" in too_short.json()["detail"]

    invalid_group = client.get("/recordings/search", params={"group_id": "bad group", "q": "тест"})
    assert invalid_group.status_code == 422


def test_upload_context_lesson_keys_allow_cross_scope_lookup(
    client: TestClient,
) -> None:
    context = json.dumps(
        {
            "groups": [{"id": "6639", "label": "ІМ-о61 (ФІОТ)"}],
            "lecturer_name": "Ірина Бондаренко",
            "lesson_keys": ["student-view-key", "shared-lesson-key"],
        },
        ensure_ascii=False,
    )
    recording = client.post(
        "/recordings",
        data={
            "lesson_key": "student-view-key",
            "lesson_title": "Основи програмування",
            "scope_label": "Група ІМ-о61 (ФІОТ)",
            "recorded_at": "2026-09-03",
            "context_json": context,
        },
        files={"file": ("lecture.mp3", b"audio", "audio/mpeg")},
    )
    attachment = client.post(
        "/attachments",
        data={
            "lesson_key": "student-view-key",
            "lesson_title": "Основи програмування",
            "scope_label": "Група ІМ-о61 (ФІОТ)",
            "recorded_at": "2026-09-03",
            "context_json": context,
        },
        files={"file": ("notes.pdf", b"notes", "application/pdf")},
    )

    assert recording.status_code == 201
    assert attachment.status_code == 201
    assert (
        client.get("/recordings", params={"lesson_key": "shared-lesson-key"}).json()[0]["id"] == recording.json()["id"]
    )
    assert (
        client.get("/attachments", params={"lesson_key": "shared-lesson-key"}).json()[0]["id"]
        == attachment.json()["id"]
    )


def test_snapshots_backfill_cross_scope_keys_groups_and_search(
    settings: Settings,
) -> None:
    repository = RecordingRepository(settings.database_path)
    repository.initialize()
    repository.put_schedule_snapshot(
        scope_type="group",
        scope_id="6639",
        scope_label="Група ІМ-о61 (ФІОТ)",
        week_start="2026-08-31",
        week_end="2026-09-06",
        schedule={
            "scheduleWeek": "firstWeek",
            "days": [
                {"day": "Пн", "pairs": []},
                {"day": "Вв", "pairs": []},
                {"day": "Ср", "pairs": []},
                {
                    "day": "Чт",
                    "pairs": [
                        {
                            "name": "Основи програмування",
                            "time": "08:30",
                            "type": "Лек",
                            "tag": "lec",
                            "dates": ["2026-09-03"],
                            "lecturer": {"id": "teacher-1", "name": "Ірина Бондаренко"},
                        }
                    ],
                },
            ],
        },
    )
    repository.create(
        recording_id="legacy-recording",
        lesson_key="opaque-v4-key",
        lesson_title="Основи програмування",
        scope_label="Група ІМ-о61 (ФІОТ)",
        original_filename="legacy.mp3",
        stored_filename="legacy.mp3",
        content_type="audio/mpeg",
        size_bytes=5,
        recorded_at="2026-09-03",
    )
    assert repository.claim_next() is not None
    assert repository.mark_ready(
        "legacy-recording",
        TranscriptionResult(
            transcript=[
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 4.0,
                    "text": "Розглянемо рекурсію",
                    "words": [],
                }
            ],
            duration_seconds=4.0,
            detected_language="uk",
            language_probability=0.98,
        ),
    )
    repository.create_attachment(
        attachment_id="legacy-attachment",
        lesson_key="opaque-v4-key",
        lesson_title="Основи програмування",
        scope_label="Група ІМ-о61 (ФІОТ)",
        original_filename="Рекурсія.pdf",
        stored_filename="legacy-attachment",
        content_type="application/pdf",
        size_bytes=5,
        recorded_at="2026-09-03",
    )

    repository.put_schedule_snapshot(
        scope_type="lecturer",
        scope_id="teacher-1",
        scope_label="Ірина Бондаренко",
        week_start="2026-08-31",
        week_end="2026-09-06",
        schedule={
            "scheduleWeek": "firstWeek",
            "days": [
                {"day": "Пн", "pairs": []},
                {"day": "Вв", "pairs": []},
                {"day": "Ср", "pairs": []},
                {
                    "day": "Чт",
                    "pairs": [
                        {
                            "name": "Основи програмування",
                            "time": "08:30",
                            "type": "Лек",
                            "tag": "lec",
                            "dates": ["2026-09-03"],
                            "groups": [
                                {"id": "6639", "name": "ІМ-о61 (ФІОТ)"},
                                {"id": "6148", "name": "ІМ-61"},
                            ],
                        }
                    ],
                },
            ],
        },
    )

    shared_lesson_key = json.dumps(
        {
            "version": 2,
            "owner": "lecturer:teacher-1",
            "week": "firstWeek",
            "day": "Чт",
            "time": "08:30",
            "name": "Основи програмування",
            "type": "Лек",
            "tag": "lec",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    assert repository.list(lesson_key=shared_lesson_key, limit=10, offset=0)[0]["id"] == "legacy-recording"
    assert repository.list_attachments(lesson_key=shared_lesson_key, limit=10, offset=0)[0]["id"] == "legacy-attachment"

    recording_page = repository.search_recordings(
        group_id="6148",
        query="бондар",
        scope="lecturer",
        limit=10,
        offset=0,
    )
    assert recording_page["total"] == 1
    assert recording_page["items"][0]["lecturer_name"] == "Ірина Бондаренко"
    assert (
        repository.search_attachments(
            group_id="6148",
            query="рекурс",
            limit=10,
            offset=0,
        )["total"]
        == 1
    )

    assert repository.delete_finished_recording("legacy-recording") == "deleted"
    assert (
        repository.search_recordings(
            group_id="6639",
            query="рекурс",
            scope="all",
            limit=10,
            offset=0,
        )["total"]
        == 0
    )
