from __future__ import annotations

import hashlib
import os
import time
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from app.auth import (
    ADMIN_SESSION_COOKIE,
    PASSWORD_ITERATIONS,
    SESSION_TTL_SECONDS,
    AdminAuth,
    hash_password,
    verify_password,
)
from app.config import Settings
from app.database import RecordingRepository
from app.main import create_app
from app.middleware import LOCAL_REQUEST_HEADER, LOCAL_REQUEST_HEADER_VALUE
from fastapi.testclient import TestClient

ADMIN_USERNAME = "local-admin"
ADMIN_PASSWORD = "correct horse battery staple"


def upload(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/recordings",
        data={
            "lesson_key": "lesson-key",
            "lesson_title": "Алгоритми",
            "scope_label": "Група ІП-01",
            "recorded_at": "2026-09-03",
        },
        files={"file": ("lecture.mp4", b"video-data", "video/mp4")},
    )
    assert response.status_code == 201
    return response.json()


def login(client: TestClient):
    return client.post(
        "/admin/session",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )


def test_password_hash_format_and_verification() -> None:
    password_hash = hash_password(ADMIN_PASSWORD, salt=b"0123456789abcdef")
    scheme, iterations, salt, digest = password_hash.split("$")

    assert scheme == "pbkdf2_sha256"
    assert iterations == str(PASSWORD_ITERATIONS)
    assert "=" not in salt
    assert "=" not in digest
    assert verify_password(ADMIN_PASSWORD, password_hash)
    assert not verify_password("incorrect", password_hash)
    assert not verify_password(ADMIN_PASSWORD, "not-a-password-record")


def test_admin_settings_are_loaded_only_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRANSCRIPTION_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TRANSCRIPTION_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_ADMIN_SESSION_SECRET", raising=False)
    unconfigured = Settings.from_env()
    assert unconfigured.admin_username is None
    assert unconfigured.admin_password_hash is None
    assert unconfigured.admin_session_secret is None

    password_hash = hash_password(ADMIN_PASSWORD, salt=b"fedcba9876543210")
    monkeypatch.setenv("TRANSCRIPTION_ADMIN_USERNAME", ADMIN_USERNAME)
    monkeypatch.setenv("TRANSCRIPTION_ADMIN_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("TRANSCRIPTION_ADMIN_SESSION_SECRET", "urlsafe_session_secret_0123456789abcdefghijklmnop")
    configured = Settings.from_env()
    assert configured.admin_username == ADMIN_USERNAME
    assert configured.admin_password_hash == password_hash
    assert configured.admin_session_secret == "urlsafe_session_secret_0123456789abcdefghijklmnop"


def test_admin_list_and_delete_require_authentication(client: TestClient) -> None:
    list_response = client.get("/admin/recordings")
    delete_response = client.delete(f"/admin/recordings/{uuid4()}")

    assert list_response.status_code == 401
    assert list_response.json() == {"detail": "Admin authentication required"}
    assert delete_response.status_code == 401
    assert delete_response.json() == {"detail": "Admin authentication required"}


def test_bad_admin_login_uses_a_generic_response(client: TestClient) -> None:
    wrong_username = client.post(
        "/admin/session",
        json={"username": "someone-else", "password": ADMIN_PASSWORD},
    )
    wrong_password = client.post(
        "/admin/session",
        json={"username": ADMIN_USERNAME, "password": "incorrect"},
    )

    assert wrong_username.status_code == 401
    assert wrong_password.status_code == 401
    assert wrong_username.json() == wrong_password.json() == {"detail": "Invalid username or password"}


def test_admin_session_login_status_and_logout(client: TestClient) -> None:
    assert client.get("/admin/session").json() == {"authenticated": False}

    login_response = login(client)
    assert login_response.status_code == 200
    assert login_response.json() == {"authenticated": True, "username": ADMIN_USERNAME}
    set_cookie = login_response.headers["set-cookie"]
    assert "HttpOnly" in set_cookie
    assert "Max-Age=28800" in set_cookie
    assert "Path=/admin" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert client.get("/admin/session").json() == {
        "authenticated": True,
        "username": ADMIN_USERNAME,
    }
    assert client.get("/admin/recordings").status_code == 200
    public_response = client.get(f"/recordings/{uuid4()}")
    assert ADMIN_SESSION_COOKIE not in public_response.request.headers.get("cookie", "")

    logout_response = client.delete("/admin/session")
    assert logout_response.status_code == 204
    assert logout_response.content == b""
    assert "Path=/admin" in logout_response.headers["set-cookie"]
    assert client.get("/admin/session").json() == {"authenticated": False}
    assert client.get("/admin/recordings").status_code == 401


def test_admin_cookie_can_be_marked_secure(settings: Settings) -> None:
    application = create_app(settings=replace(settings, secure_cookies=True), start_worker=False)
    with TestClient(application) as client:
        client.headers.update(
            {
                "Origin": "http://localhost:3000",
                LOCAL_REQUEST_HEADER: LOCAL_REQUEST_HEADER_VALUE,
            }
        )
        response = login(client)

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


def test_tampered_and_expired_admin_sessions_are_rejected(client: TestClient) -> None:
    login_response = login(client)
    token = login_response.cookies[ADMIN_SESSION_COOKIE]
    payload, signature = token.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    client.cookies.clear()
    client.cookies.set(ADMIN_SESSION_COOKIE, f"{payload}.{replacement}{signature[1:]}", path="/admin")

    assert client.get("/admin/session").json() == {"authenticated": False}
    assert client.get("/admin/recordings").status_code == 401

    auth: AdminAuth = client.app.state.admin_auth  # type: ignore[attr-defined]
    expired_token = auth.issue_session(now=int(time.time()) - SESSION_TTL_SECONDS - 1)
    client.cookies.clear()
    client.cookies.set(ADMIN_SESSION_COOKIE, expired_token, path="/admin")

    assert client.get("/admin/session").json() == {"authenticated": False}
    assert client.get("/admin/recordings").status_code == 401


def test_admin_can_delete_finished_recording_and_media(client: TestClient, settings: Settings) -> None:
    created = upload(client)
    repository = client.app.state.repository  # type: ignore[attr-defined]
    claimed = repository.claim_next()
    assert claimed is not None
    assert repository.mark_failed(str(created["id"]), "mock failure")
    media_files = list(settings.media_dir.iterdir())
    assert len(media_files) == 1

    assert login(client).status_code == 200
    listed = client.get("/admin/recordings")
    assert listed.status_code == 200
    assert [recording["id"] for recording in listed.json()] == [created["id"]]

    delete_response = client.delete(f"/admin/recordings/{created['id']}")
    assert delete_response.status_code == 204
    assert delete_response.content == b""
    assert repository.get(str(created["id"])) is None
    assert not media_files[0].exists()
    assert client.get(f"/recordings/{created['id']}").status_code == 404
    assert client.delete(f"/admin/recordings/{created['id']}").status_code == 404


def test_admin_deletion_removes_original_and_prepared_audio(
    client: TestClient,
    settings: Settings,
) -> None:
    created = upload(client)
    repository = client.app.state.repository  # type: ignore[attr-defined]
    preparation = repository.claim_audio_preparation(max_attempts=3)
    assert preparation is not None
    prepared_path = settings.prepared_audio_dir / f"{created['id']}.ogg"
    prepared_path.write_bytes(b"OggS-prepared")
    assert repository.mark_audio_prepared(
        created["id"],
        stored_filename=prepared_path.name,
        size_bytes=prepared_path.stat().st_size,
        sha256=hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
    )
    assert repository.claim_next() is not None
    assert repository.mark_failed(str(created["id"]), "mock failure")
    original_path = settings.media_dir / f"{created['id']}.mp4"

    assert login(client).status_code == 200
    response = client.delete(f"/admin/recordings/{created['id']}")

    assert response.status_code == 204
    assert not original_path.exists()
    assert not prepared_path.exists()
    assert repository.get_audio_artifact(created["id"]) is None


def test_admin_deletion_restores_original_if_prepared_audio_staging_fails(
    client: TestClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = upload(client)
    repository = client.app.state.repository  # type: ignore[attr-defined]
    assert repository.claim_audio_preparation(max_attempts=3) is not None
    prepared_path = settings.prepared_audio_dir / f"{created['id']}.ogg"
    prepared_path.write_bytes(b"OggS-prepared")
    assert repository.mark_audio_prepared(
        created["id"],
        stored_filename=prepared_path.name,
        size_bytes=prepared_path.stat().st_size,
        sha256=hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
    )
    assert repository.claim_next() is not None
    assert repository.mark_failed(str(created["id"]), "mock failure")
    original_path = settings.media_dir / f"{created['id']}.mp4"
    real_replace = os.replace

    def fail_prepared_stage(source: Path, destination: Path) -> None:
        if Path(source) == prepared_path and Path(destination).name.endswith(".deleting"):
            raise PermissionError("prepared audio is locked")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_prepared_stage)
    assert login(client).status_code == 200

    response = client.delete(f"/admin/recordings/{created['id']}")

    assert response.status_code == 409
    assert original_path.exists()
    assert prepared_path.exists()
    assert repository.get(created["id"]) is not None
    assert not list(settings.media_dir.glob(".*.deleting"))


def test_admin_cannot_delete_processing_recording(client: TestClient, settings: Settings) -> None:
    created = upload(client)
    repository = client.app.state.repository  # type: ignore[attr-defined]
    claimed = repository.claim_next()
    assert claimed is not None
    assert claimed["status"] == "processing"
    media_path = next(settings.media_dir.iterdir())

    assert login(client).status_code == 200
    delete_response = client.delete(f"/admin/recordings/{created['id']}")

    assert delete_response.status_code == 409
    assert repository.get(str(created["id"]))["status"] == "processing"
    assert media_path.exists()


def test_missing_admin_configuration_fails_closed(settings: Settings) -> None:
    unconfigured = replace(
        settings,
        admin_username=None,
        admin_password_hash=None,
        admin_session_secret=None,
    )
    application = create_app(settings=unconfigured, start_worker=False)
    with TestClient(application) as client:
        client.headers.update(
            {
                "Origin": "http://localhost:3000",
                LOCAL_REQUEST_HEADER: LOCAL_REQUEST_HEADER_VALUE,
            }
        )
        assert client.get("/admin/session").json() == {"authenticated": False}
        login_response = client.post(
            "/admin/session",
            json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        )
        assert login_response.status_code == 503
        client.cookies.set(ADMIN_SESSION_COOKIE, "forged.token", path="/admin")
        assert client.get("/admin/recordings").status_code == 401


def test_admin_cookie_uses_the_external_proxy_admin_path(client: TestClient) -> None:
    proxy_headers = {"X-Forwarded-Prefix": "/recordings-api"}
    login_response = client.post(
        "/admin/session",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
        headers=proxy_headers,
    )

    assert login_response.status_code == 200
    assert "Path=/recordings-api/admin" in login_response.headers["set-cookie"]
    cookie = next(cookie for cookie in login_response.cookies.jar if cookie.name == ADMIN_SESSION_COOKIE)
    assert cookie.path == "/recordings-api/admin"

    logout_response = client.delete("/admin/session", headers=proxy_headers)
    assert logout_response.status_code == 204
    assert "Path=/recordings-api/admin" in logout_response.headers["set-cookie"]


def test_startup_removes_only_valid_orphaned_deletion_artifacts(settings: Settings) -> None:
    settings.media_dir.mkdir(parents=True)
    recording_id = str(uuid4())
    valid_orphan = settings.media_dir / f".{recording_id}.0123456789abcdef0123456789abcdef.deleting"
    invalid_token = settings.media_dir / f".{recording_id}.not-hex.deleting"
    unrelated = settings.media_dir / "lecture.mp4.deleting"
    for path in (valid_orphan, invalid_token, unrelated):
        path.write_bytes(b"media")

    create_app(settings=settings, start_worker=False)

    assert not valid_orphan.exists()
    assert invalid_token.exists()
    assert unrelated.exists()


def test_startup_restores_staged_media_when_database_row_still_exists(settings: Settings) -> None:
    settings.media_dir.mkdir(parents=True)
    repository = RecordingRepository(settings.database_path)
    repository.initialize()
    recording_id = str(uuid4())
    stored_filename = f"{recording_id}.mp4"
    repository.create(
        recording_id=recording_id,
        lesson_key="lesson-key",
        lesson_title="Алгоритми",
        scope_label="Група ІП-01",
        original_filename="lecture.mp4",
        stored_filename=stored_filename,
        content_type="video/mp4",
        size_bytes=5,
        recorded_at="2026-09-03",
    )
    staged_path = settings.media_dir / f".{recording_id}.fedcba9876543210fedcba9876543210.deleting"
    staged_path.write_bytes(b"media")

    create_app(settings=settings, repository=repository, start_worker=False)

    restored_path = settings.media_dir / stored_filename
    assert restored_path.read_bytes() == b"media"
    assert not staged_path.exists()


def test_startup_restores_staged_prepared_audio_when_database_row_exists(settings: Settings) -> None:
    settings.prepared_audio_dir.mkdir(parents=True)
    repository = RecordingRepository(settings.database_path)
    repository.initialize()
    recording_id = str(uuid4())
    repository.create(
        recording_id=recording_id,
        lesson_key="lesson-key",
        lesson_title="Алгоритми",
        scope_label="Група ІП-01",
        original_filename="lecture.mp4",
        stored_filename=f"{recording_id}.mp4",
        content_type="video/mp4",
        size_bytes=5,
        recorded_at="2026-09-03",
    )
    assert repository.claim_audio_preparation(max_attempts=3) is not None
    payload = b"OggS-prepared"
    assert repository.mark_audio_prepared(
        recording_id,
        stored_filename=f"{recording_id}.ogg",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    staged_path = settings.prepared_audio_dir / (
        f".{recording_id}.fedcba9876543210fedcba9876543210.deleting"
    )
    staged_path.write_bytes(payload)

    create_app(settings=settings, repository=repository, start_worker=False)

    restored_path = settings.prepared_audio_dir / f"{recording_id}.ogg"
    assert restored_path.read_bytes() == payload
    assert not staged_path.exists()


def test_failed_post_delete_unlink_is_cleaned_on_next_startup(
    client: TestClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = upload(client)
    repository = client.app.state.repository  # type: ignore[attr-defined]
    assert repository.claim_next() is not None
    assert repository.mark_failed(str(created["id"]), "mock failure")
    assert login(client).status_code == 200
    original_unlink = Path.unlink

    with monkeypatch.context() as patch:
        def fail_staged_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if path.name.endswith(".deleting"):
                raise PermissionError("mock media lock")
            original_unlink(path, *args, **kwargs)

        patch.setattr(Path, "unlink", fail_staged_unlink)
        response = client.delete(f"/admin/recordings/{created['id']}")

    assert response.status_code == 204
    staged_files = list(settings.media_dir.glob(".*.deleting"))
    assert len(staged_files) == 1
    assert repository.get(str(created["id"])) is None

    create_app(settings=settings, repository=repository, start_worker=False)
    assert not staged_files[0].exists()
