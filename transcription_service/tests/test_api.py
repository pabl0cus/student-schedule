from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import MAX_LESSON_KEY_LENGTH, create_app
from app.middleware import LOCAL_REQUEST_HEADER, LOCAL_REQUEST_HEADER_VALUE


def upload(
    client: TestClient,
    *,
    content: bytes = b"0123456789",
    lesson_key: str = "group:ІП-01:1:mon",
    headers: dict[str, str] | None = None,
):
    return client.post(
        "/recordings",
        data={
            "lesson_key": lesson_key,
            "lesson_title": "Алгоритми та структури даних",
            "scope_label": "ІП-01 · понеділок · 08:30",
            "recorded_at": "2026-09-03",
        },
        files={"file": ("lecture.mp4", content, "video/mp4")},
        headers=headers,
    )


def test_upload_list_detail_and_media_range(client: TestClient) -> None:
    response = upload(client)
    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "queued"
    assert created["lesson_title"] == "Алгоритми та структури даних"
    assert created["size_bytes"] == 10
    assert created["recorded_at"] == "2026-09-03"
    assert created["file_name"] == "lecture.mp4"
    assert created["mime_type"] == "video/mp4"
    assert created["progress"] == 0
    assert created["media_url"] == f"/recordings/{created['id']}/media"

    other_response = upload(client, lesson_key="another-lesson")
    assert other_response.status_code == 201

    listed = client.get("/recordings", params={"lesson_key": "group:ІП-01:1:mon"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created["id"]]
    assert "transcript" not in listed.json()[0]

    detail = client.get(f"/recordings/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["transcript"] == []

    full_media = client.get(created["media_url"])
    assert full_media.status_code == 200
    assert full_media.content == b"0123456789"
    assert full_media.headers["accept-ranges"] == "bytes"

    media_range = client.get(created["media_url"], headers={"Range": "bytes=2-5"})
    assert media_range.status_code == 206
    assert media_range.content == b"2345"
    assert media_range.headers["content-range"] == "bytes 2-5/10"

    media_suffix = client.get(created["media_url"], headers={"Range": "bytes=-3"})
    assert media_suffix.status_code == 206
    assert media_suffix.content == b"789"

    invalid_range = client.get(created["media_url"], headers={"Range": "bytes=99-100"})
    assert invalid_range.status_code == 416
    assert invalid_range.headers["content-range"] == "bytes */10"

    download = client.get(f"/recordings/{created['id']}/download")
    assert download.status_code == 200
    assert download.content == b"0123456789"
    assert download.headers["content-disposition"] == 'attachment; filename="lecture.mp4"'
    assert download.headers["cache-control"] == "private, no-store"
    assert download.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert download.headers["cross-origin-resource-policy"] == "same-origin"
    assert download.headers["x-content-type-options"] == "nosniff"


def test_upload_rejects_unsupported_empty_and_oversized_files(client: TestClient, settings: Settings) -> None:
    unsupported = client.post(
        "/recordings",
        data={"lesson_key": "key", "lesson_title": "Title", "scope_label": "Scope"},
        files={"file": ("notes.exe", b"payload", "application/octet-stream")},
    )
    assert unsupported.status_code == 415

    empty = upload(client, content=b"")
    assert empty.status_code == 422

    oversized = upload(client, content=b"x" * 33)
    assert oversized.status_code == 413

    invalid_date = client.post(
        "/recordings",
        data={
            "lesson_key": "key",
            "lesson_title": "Title",
            "scope_label": "Scope",
            "recorded_at": "2026-02-30",
        },
        files={"file": ("lecture.mp4", b"payload", "video/mp4")},
    )
    assert invalid_date.status_code == 422
    assert list(settings.temp_dir.iterdir()) == []
    assert list(settings.media_dir.iterdir()) == []


def test_upload_does_not_use_client_path_as_storage_path(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/recordings",
        data={"lesson_key": "key", "lesson_title": "Title", "scope_label": "Scope"},
        files={"file": ("../../Небезпечна назва.mp4", b"video", "video/mp4")},
    )
    assert response.status_code == 201
    assert response.json()["original_filename"] == "Небезпечна назва.mp4"

    download = client.get(f"/recordings/{response.json()['id']}/download")
    disposition = download.headers["content-disposition"]
    assert disposition.startswith("attachment; filename*=utf-8''")
    assert unquote(disposition.split("''", maxsplit=1)[1]) == "Небезпечна назва.mp4"

    stored_files = list(settings.media_dir.iterdir())
    assert len(stored_files) == 1
    assert stored_files[0].suffix == ".mp4"
    assert "Небезпечна" not in stored_files[0].name


def test_recording_download_returns_not_found_for_unknown_id(client: TestClient) -> None:
    assert client.get(f"/recordings/{uuid4()}/download").status_code == 404
    assert client.get("/recordings/not-a-recording/download").status_code == 404


def test_retry_requires_failed_state(client: TestClient) -> None:
    created = upload(client).json()
    response = client.post(f"/recordings/{created['id']}/retry")
    assert response.status_code == 409


def test_health_reports_database_and_queue(client: TestClient) -> None:
    upload(client)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "worker": "stopped",
        "queue": {"queued": 1, "processing": 0, "ready": 0, "failed": 0},
    }


def test_recorded_at_defaults_to_created_date(client: TestClient) -> None:
    response = client.post(
        "/recordings",
        data={"lesson_key": "key", "lesson_title": "Title", "scope_label": "Scope"},
        files={"file": ("lecture.mp4", b"payload", "video/mp4")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["recorded_at"] == body["created_at"][:10]


def test_unsafe_requests_require_allowed_origin_and_custom_header(settings: Settings) -> None:
    application = create_app(settings=settings, start_worker=False)
    with TestClient(application) as unsafe_client:
        missing_headers = upload(unsafe_client)
        assert missing_headers.status_code == 403

        missing_marker = upload(unsafe_client, headers={"Origin": "http://localhost:3000"})
        assert missing_marker.status_code == 403

        wrong_origin = upload(
            unsafe_client,
            headers={
                "Origin": "https://example.invalid",
                LOCAL_REQUEST_HEADER: LOCAL_REQUEST_HEADER_VALUE,
            },
        )
        assert wrong_origin.status_code == 403


def test_trusted_host_rejects_non_local_host(client: TestClient) -> None:
    response = client.get("/health", headers={"Host": "schedule.example.invalid"})
    assert response.status_code == 400


def test_request_size_guard_runs_before_multipart_parsing(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/recordings",
        content=b"x" * (settings.max_request_bytes + 1),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "Request body exceeds the configured size limit"


def test_json_routes_use_the_smaller_request_limit(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/admin/session",
        content=b"x" * (settings.max_json_bytes + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Request body exceeds the configured size limit"


def test_media_url_honors_the_trusted_proxy_prefix(client: TestClient) -> None:
    response = upload(client, headers={"X-Forwarded-Prefix": "/recordings-api"})
    assert response.status_code == 201
    body = response.json()
    assert body["media_url"] == f"/recordings-api/recordings/{body['id']}/media"


def test_recording_upload_and_lookup_support_long_v3_lesson_keys(client: TestClient) -> None:
    prefix = '{"version":3,"name":"'
    suffix = '"}'
    lesson_key = prefix + ("k" * (MAX_LESSON_KEY_LENGTH - len(prefix) - len(suffix))) + suffix
    assert len(lesson_key) == MAX_LESSON_KEY_LENGTH

    created = upload(client, lesson_key=lesson_key)
    assert created.status_code == 201

    listed = client.get("/recordings", params={"lesson_key": lesson_key})
    assert listed.status_code == 200
    assert [recording["id"] for recording in listed.json()] == [created.json()["id"]]

    too_long_key = lesson_key + "x"
    rejected_upload = upload(client, lesson_key=too_long_key)
    assert rejected_upload.status_code == 422
    rejected_lookup = client.get("/recordings", params={"lesson_key": too_long_key})
    assert rejected_lookup.status_code == 422


def test_startup_removes_only_stale_upload_artifacts(settings: Settings) -> None:
    settings.temp_dir.mkdir(parents=True)
    stale_path = settings.temp_dir / f".{uuid4()}.uploading"
    recent_path = settings.temp_dir / f".{uuid4()}.uploading"
    unrelated_path = settings.temp_dir / "keep-me.uploading"
    for path in (stale_path, recent_path, unrelated_path):
        path.write_bytes(b"partial")
    os.utime(stale_path, (0, 0))

    create_app(settings=settings, start_worker=False)

    assert not stale_path.exists()
    assert recent_path.exists()
    assert unrelated_path.exists()


def schedule_snapshot_payload(*, lesson_name: str = "Алгоритми та структури даних") -> dict[str, object]:
    return {
        "scope_label": "Група ІП-01",
        "schedule": {
            "scheduleWeek": "firstWeek",
            "days": [
                {
                    "day": "Понеділок",
                    "pairs": [
                        {
                            "name": lesson_name,
                            "time": "08:30:00",
                            "type": "Лек",
                            "tag": "lec",
                            "dates": ["2026-08-31"],
                            "lecturer": {"id": "abc", "name": "Викладач"},
                        }
                    ],
                }
            ],
        },
    }


def test_schedule_snapshot_create_get_list_and_idempotency(client: TestClient) -> None:
    path = "/schedule-snapshots/group/6352/2026-08-31"
    payload = schedule_snapshot_payload()

    created_response = client.put(path, json=payload)
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["scope_type"] == "group"
    assert created["scope_id"] == "6352"
    assert created["scope_key"] == "group:6352"
    assert created["scope_label"] == "Група ІП-01"
    assert created["week_start"] == "2026-08-31"
    assert created["week_end"] == "2026-09-06"
    assert created["schedule"] == payload["schedule"]
    assert len(created["content_hash"]) == 64

    # Object key order does not affect idempotency and the original creation time is retained.
    reordered_payload = {
        "schedule": {
            "days": payload["schedule"]["days"],  # type: ignore[index]
            "scheduleWeek": "firstWeek",
        },
        "scope_label": "Група ІП-01",
    }
    repeated_response = client.put(path, json=reordered_payload)
    assert repeated_response.status_code == 200
    assert repeated_response.json() == created

    exact_response = client.get(path)
    assert exact_response.status_code == 200
    assert exact_response.json() == created

    listed_response = client.get(
        "/schedule-snapshots/group/6352",
        params={"from_week": "2026-08-31", "to_week": "2026-08-31"},
    )
    assert listed_response.status_code == 200
    listed = listed_response.json()
    assert len(listed) == 1
    assert listed[0]["week_start"] == "2026-08-31"
    assert listed[0]["created_at"] == created["created_at"]
    assert "schedule" not in listed[0]


def test_schedule_snapshot_is_immutable(client: TestClient) -> None:
    path = "/schedule-snapshots/group/6352/2026-08-31"
    assert client.put(path, json=schedule_snapshot_payload()).status_code == 201

    conflicting_response = client.put(path, json=schedule_snapshot_payload(lesson_name="Змінена назва"))
    assert conflicting_response.status_code == 409
    assert "already archived" in conflicting_response.json()["detail"]

    stored_response = client.get(path)
    assert stored_response.status_code == 200
    assert stored_response.json()["schedule"]["days"][0]["pairs"][0]["name"] == (
        "Алгоритми та структури даних"
    )


def test_schedule_snapshot_validates_calendar_week_and_shape(client: TestClient) -> None:
    not_monday = client.put(
        "/schedule-snapshots/group/6352/2026-09-01",
        json=schedule_snapshot_payload(),
    )
    assert not_monday.status_code == 422
    assert not_monday.json()["detail"] == "week_start must be a Monday"

    invalid_shape = client.put(
        "/schedule-snapshots/group/6352/2026-08-31",
        json={"scope_label": "Група ІП-01", "schedule": {"days": []}},
    )
    assert invalid_shape.status_code == 422

    invalid_scope = client.put(
        "/schedule-snapshots/group/not%3Avalid/2026-08-31",
        json=schedule_snapshot_payload(),
    )
    assert invalid_scope.status_code == 422

    missing = client.get("/schedule-snapshots/group/9999/2026-08-31")
    assert missing.status_code == 404


def test_schedule_snapshot_put_uses_the_local_request_guard(settings: Settings) -> None:
    application = create_app(settings=settings, start_worker=False)
    with TestClient(application) as unsafe_client:
        response = unsafe_client.put(
            "/schedule-snapshots/group/6352/2026-08-31",
            json=schedule_snapshot_payload(),
        )
    assert response.status_code == 403
