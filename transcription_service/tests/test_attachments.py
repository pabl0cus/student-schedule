from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.database import RecordingRepository
from app.main import MAX_LESSON_KEY_LENGTH, MAX_LIST_PAGE_SIZE, create_app
from app.middleware import LOCAL_REQUEST_HEADER, LOCAL_REQUEST_HEADER_VALUE


def upload_attachment(
    client: TestClient,
    *,
    content: bytes = b"lecture notes",
    filename: str = "notes.pdf",
    content_type: str = "application/pdf",
    lesson_key: str = "group:ІП-01:1:mon",
    headers: dict[str, str] | None = None,
):
    return client.post(
        "/attachments",
        data={
            "lesson_key": lesson_key,
            "lesson_title": "Алгоритми та структури даних",
            "scope_label": "ІП-01 · понеділок · 08:30",
            "recorded_at": "2026-09-03",
        },
        files={"file": (filename, content, content_type)},
        headers=headers,
    )


def _multipart_body(*, boundary: str, content: bytes) -> bytes:
    fields = {
        "lesson_key": "group:ІП-01:1:mon",
        "lesson_title": "Алгоритми",
        "scope_label": "ІП-01",
        "recorded_at": "2026-09-03",
    }
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="file"; filename="notes.bin"\r\n')
    body.extend(b"Content-Type: application/x-custom\r\n\r\n")
    body.extend(content)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return bytes(body)


def test_upload_list_and_download_arbitrary_attachment(client: TestClient, settings: Settings) -> None:
    response = upload_attachment(
        client,
        content=b"<h1>notes</h1>",
        filename="../../notes.html",
        content_type="text/html",
        headers={"X-Forwarded-Prefix": "/recordings-api"},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["lesson_title"] == "Алгоритми та структури даних"
    assert created["original_filename"] == "notes.html"
    assert created["file_name"] == "notes.html"
    assert created["content_type"] == "text/html"
    assert created["mime_type"] == "text/html"
    assert created["size_bytes"] == 14
    assert created["recorded_at"] == "2026-09-03"
    assert created["content_url"] == f"/recordings-api/attachments/{created['id']}/content"

    stored_files = list(settings.attachments_dir.iterdir())
    assert len(stored_files) == 1
    assert stored_files[0].name == created["id"]

    other = upload_attachment(client, content=b"other", lesson_key="another-lesson")
    assert other.status_code == 201

    listed = client.get("/attachments", params={"lesson_key": "group:ІП-01:1:mon"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created["id"]]
    assert "stored_filename" not in listed.json()[0]

    downloaded = client.get(f"/attachments/{created['id']}/content")
    assert downloaded.status_code == 200
    assert downloaded.content == b"<h1>notes</h1>"
    assert downloaded.headers["content-type"].startswith("text/html")
    assert downloaded.headers["content-disposition"].lower().startswith("attachment;")
    assert 'filename="notes.html"' in downloaded.headers["content-disposition"]
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert downloaded.headers["cross-origin-resource-policy"] == "same-origin"


def test_attachment_size_boundary_and_spoofed_content_length(client: TestClient, settings: Settings) -> None:
    accepted = upload_attachment(client, content=b"x" * settings.max_attachment_bytes)
    assert accepted.status_code == 201

    rejected = upload_attachment(
        client,
        content=b"x" * (settings.max_attachment_bytes + 1),
        headers={"Content-Length": "1"},
    )
    assert rejected.status_code == 413
    assert rejected.json()["detail"] == "Uploaded attachment exceeds the configured size limit"

    empty = upload_attachment(client, content=b"")
    assert empty.status_code == 422
    assert list(settings.temp_dir.iterdir()) == []
    assert len(list(settings.attachments_dir.iterdir())) == 1


def test_attachment_limit_does_not_rely_on_content_length(client: TestClient, settings: Settings) -> None:
    boundary = "kpi-attachment-boundary"
    body = _multipart_body(
        boundary=boundary,
        content=b"x" * (settings.max_attachment_bytes + 1),
    )
    response = client.post(
        "/attachments",
        content=iter([body]),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.json()["detail"] == "Uploaded attachment exceeds the configured size limit"
    assert list(settings.temp_dir.iterdir()) == []
    assert list(settings.attachments_dir.iterdir()) == []


def test_attachment_request_guard_uses_its_own_declared_limit(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/attachments",
        content=b"not parsed",
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(settings.max_attachment_request_bytes + 1),
        },
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "Request body exceeds the configured size limit"


def test_attachment_upload_and_lookup_support_long_v3_lesson_keys(client: TestClient) -> None:
    prefix = '{"version":3,"name":"'
    suffix = '"}'
    lesson_key = prefix + ("k" * (MAX_LESSON_KEY_LENGTH - len(prefix) - len(suffix))) + suffix
    assert len(lesson_key) == MAX_LESSON_KEY_LENGTH

    created = upload_attachment(client, content=b"notes", lesson_key=lesson_key)
    assert created.status_code == 201

    listed = client.get("/attachments", params={"lesson_key": lesson_key})
    assert listed.status_code == 200
    assert [attachment["id"] for attachment in listed.json()] == [created.json()["id"]]

    too_long_key = lesson_key + "x"
    rejected_upload = upload_attachment(client, content=b"notes", lesson_key=too_long_key)
    assert rejected_upload.status_code == 422
    rejected_lookup = client.get("/attachments", params={"lesson_key": too_long_key})
    assert rejected_lookup.status_code == 422


def test_attachment_listing_keeps_bounded_offset_pagination(client: TestClient) -> None:
    created_ids = {
        upload_attachment(client, content=f"file-{index}".encode()).json()["id"]
        for index in range(3)
    }

    first_page = client.get("/attachments", params={"limit": 2, "offset": 0})
    second_page = client.get("/attachments", params={"limit": 2, "offset": 2})
    maximum_page = client.get("/attachments", params={"limit": MAX_LIST_PAGE_SIZE})
    oversized_page = client.get("/attachments", params={"limit": MAX_LIST_PAGE_SIZE + 1})

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert maximum_page.status_code == 200
    assert oversized_page.status_code == 422
    first_ids = {attachment["id"] for attachment in first_page.json()}
    second_ids = {attachment["id"] for attachment in second_page.json()}
    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == created_ids
    assert {attachment["id"] for attachment in maximum_page.json()} == created_ids


def test_attachment_file_is_removed_when_database_insert_fails(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RecordingRepository(settings.database_path)
    application = create_app(settings=settings, repository=repository, start_worker=False)

    def fail_create_attachment(**_: object) -> dict[str, object]:
        raise sqlite3.OperationalError("simulated insert failure")

    monkeypatch.setattr(repository, "create_attachment", fail_create_attachment)
    with TestClient(application, raise_server_exceptions=False) as failure_client:
        failure_client.headers.update(
            {
                "Origin": "http://localhost:3000",
                LOCAL_REQUEST_HEADER: LOCAL_REQUEST_HEADER_VALUE,
            }
        )
        response = upload_attachment(failure_client)

    assert response.status_code == 500
    assert list(settings.temp_dir.iterdir()) == []
    assert list(settings.attachments_dir.iterdir()) == []


def test_attachment_download_rejects_unsafe_stored_path(client: TestClient, settings: Settings) -> None:
    attachment_id = str(uuid4())
    outside_path = settings.data_dir / "outside.bin"
    outside_path.write_bytes(b"must not be served")
    repository: RecordingRepository = client.app.state.repository
    repository.create_attachment(
        attachment_id=attachment_id,
        lesson_key="key",
        lesson_title="Title",
        scope_label="Scope",
        original_filename="outside.bin",
        stored_filename="../outside.bin",
        content_type="application/octet-stream",
        size_bytes=outside_path.stat().st_size,
        recorded_at="2026-09-03",
    )

    response = client.get(f"/attachments/{attachment_id}/content")
    assert response.status_code == 404
    assert response.json()["detail"] == "Attachment file not found"


def test_attachment_upload_requires_allowed_origin_and_marker(settings: Settings) -> None:
    application = create_app(settings=settings, start_worker=False)
    with TestClient(application) as unsafe_client:
        missing_headers = upload_attachment(unsafe_client)
        assert missing_headers.status_code == 403

        missing_marker = upload_attachment(
            unsafe_client,
            headers={"Origin": "http://localhost:3000"},
        )
        assert missing_marker.status_code == 403

        wrong_origin = upload_attachment(
            unsafe_client,
            headers={
                "Origin": "https://example.invalid",
                LOCAL_REQUEST_HEADER: LOCAL_REQUEST_HEADER_VALUE,
            },
        )
        assert wrong_origin.status_code == 403
