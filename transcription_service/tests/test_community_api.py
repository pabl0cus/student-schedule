from __future__ import annotations

import hashlib
from dataclasses import replace
from uuid import uuid4

import app.main as main_module
from app.config import Settings
from app.main import create_app
from app.middleware import LOCAL_REQUEST_HEADER, LOCAL_REQUEST_HEADER_VALUE
from fastapi.testclient import TestClient

BROWSER_HEADERS = {
    "Origin": "http://localhost:3000",
    LOCAL_REQUEST_HEADER: LOCAL_REQUEST_HEADER_VALUE,
}
ADMIN_USERNAME = "local-admin"
ADMIN_PASSWORD = "correct horse battery staple"
TOKEN_PEPPER = "community_worker_test_pepper_0123456789abcdef"
PREPARED_AUDIO = b"OggS-normalized-audio"


def _upload(
    client: TestClient,
    suffix: str = "",
    content_type: str = "video/webm",
) -> dict[str, object]:
    response = client.post(
        "/recordings",
        headers=BROWSER_HEADERS,
        data={
            "lesson_key": f"lesson{suffix}",
            "lesson_title": "Алгоритми",
            "scope_label": "Група ІП-01",
            "recorded_at": "2026-09-04",
        },
        files={"file": (f"lecture{suffix}.webm", b"mock-media", content_type)},
    )
    assert response.status_code == 201
    recording = response.json()
    repository = client.app.state.repository  # type: ignore[attr-defined]
    preparation = repository.claim_audio_preparation(max_attempts=3)
    assert preparation is not None
    assert preparation["id"] == recording["id"]
    filename = f"{recording['id']}.ogg"
    client.app.state.settings.prepared_audio_dir.joinpath(filename).write_bytes(PREPARED_AUDIO)  # type: ignore[attr-defined]
    assert repository.mark_audio_prepared(
        recording["id"],
        stored_filename=filename,
        size_bytes=len(PREPARED_AUDIO),
        sha256=hashlib.sha256(PREPARED_AUDIO).hexdigest(),
    )
    return recording


def _issue_worker(client: TestClient, label: str = "RTX 3090") -> dict[str, object]:
    login = client.post(
        "/admin/session",
        headers=BROWSER_HEADERS,
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert login.status_code == 200
    issued = client.post(
        "/admin/community-workers",
        headers=BROWSER_HEADERS,
        json={"label": label},
    )
    assert issued.status_code == 201
    return issued.json()


def _claim(client: TestClient, token: str) -> dict[str, object]:
    response = client.post(
        "/community/v1/jobs/claim",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "protocol_version": 1,
            "client_id": str(uuid4()),
            "client_version": "0.1.0-test",
        },
    )
    assert response.status_code == 200
    return response.json()


def _result(submission_id: str) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "submission_id": submission_id,
        "engine": "faster-whisper",
        "model": "large-v3",
        "engine_version": "1.2.1",
        "client_version": "0.1.0-test",
        "duration_seconds": 2.0,
        "detected_language": "uk",
        "language_probability": 0.99,
        "transcript": [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.5,
                "text": "Вітаю на парі.",
                "words": [
                    {
                        "start": 0.0,
                        "end": 0.6,
                        "word": "Вітаю",
                        "probability": 0.98,
                    }
                ],
            }
        ],
    }


def test_admin_issues_revokes_and_never_lists_worker_secret(settings: Settings) -> None:
    application = create_app(
        settings=replace(settings, worker_token_pepper=TOKEN_PEPPER),
        start_worker=False,
    )
    with TestClient(application) as client:
        issued = _issue_worker(client)
        assert str(issued["token"]).startswith("sst_wk_v1_")

        listed = client.get("/admin/community-workers")
        assert listed.status_code == 200
        assert listed.json() == [
            {
                "id": issued["id"],
                "label": "RTX 3090",
                "created_at": issued["created_at"],
                "last_seen_at": None,
                "revoked_at": None,
            }
        ]
        assert "token" not in listed.text

        worker_headers = {"Authorization": f"Bearer {issued['token']}"}
        assert client.get("/community/v1/me", headers=worker_headers).status_code == 200

        revoked = client.delete(
            f"/admin/community-workers/{issued['id']}",
            headers=BROWSER_HEADERS,
        )
        assert revoked.status_code == 204
        unauthorized = client.get("/community/v1/me", headers=worker_headers)
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"


def test_community_worker_claims_media_heartbeats_and_submits_idempotently(settings: Settings) -> None:
    application = create_app(
        settings=replace(settings, worker_token_pepper=TOKEN_PEPPER),
        start_worker=False,
    )
    with TestClient(application) as client:
        recording = _upload(client, content_type="video/webm; codecs=vp9,opus")
        issued = _issue_worker(client)
        token = str(issued["token"])
        authorization = {"Authorization": f"Bearer {token}"}

        # The native client has neither a browser Origin nor the browser mutation marker.
        no_token = client.post(
            "/community/v1/jobs/claim",
            json={
                "protocol_version": 1,
                "client_id": str(uuid4()),
                "client_version": "0.1.0-test",
            },
        )
        assert no_token.status_code == 401

        lease = _claim(client, token)
        assert lease["job_id"] == recording["id"]
        assert lease["media"] == {
            "size_bytes": len(PREPARED_AUDIO),
            "content_type": "audio/ogg",
            "sha256": hashlib.sha256(PREPARED_AUDIO).hexdigest(),
        }
        assert lease["transcription"] == {
            "model": "large-v3",
            "language": "uk",
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps": True,
            "initial_prompt": "Українська університетська лекція. Дисципліна: Алгоритми. Контекст: Група ІП-01.",
        }
        lease_headers = {
            **authorization,
            "X-Transcription-Lease": str(lease["lease_token"]),
        }

        partial_media = client.get(
            str(lease["media_path"]),
            headers={**lease_headers, "Range": "bytes=0-3"},
        )
        assert partial_media.status_code == 206
        assert partial_media.content == PREPARED_AUDIO[:4]
        assert partial_media.headers["content-range"] == f"bytes 0-3/{len(PREPARED_AUDIO)}"

        media = client.get(str(lease["media_path"]), headers=lease_headers)
        assert media.status_code == 200
        assert media.content == PREPARED_AUDIO
        assert media.headers["etag"] == f'"{hashlib.sha256(PREPARED_AUDIO).hexdigest()}"'
        assert media.headers["cache-control"] == "private, no-store"

        heartbeat = client.post(
            f"/community/v1/jobs/{recording['id']}/heartbeat",
            headers=lease_headers,
            json={"protocol_version": 1, "progress": 25},
        )
        assert heartbeat.status_code == 200
        assert heartbeat.json()["lease_expires_at"]

        submission_id = str(uuid4())
        result_url = f"/community/v1/jobs/{recording['id']}/result"
        accepted = client.put(result_url, headers=lease_headers, json=_result(submission_id))
        assert accepted.status_code == 200
        assert accepted.json() == {"status": "accepted", "disposition": "completed"}

        duplicate = client.put(result_url, headers=lease_headers, json=_result(submission_id))
        assert duplicate.status_code == 200
        assert duplicate.json() == {"status": "accepted", "disposition": "duplicate"}

        ready = client.get(f"/recordings/{recording['id']}")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"
        assert ready.json()["transcript"][0]["words"][0]["word"] == "Вітаю"

        empty = client.post(
            "/community/v1/jobs/claim",
            headers=authorization,
            json={
                "protocol_version": 1,
                "client_id": str(uuid4()),
                "client_version": "0.1.0-test",
            },
        )
        assert empty.status_code == 204
        assert empty.headers["retry-after"] == "30"


def test_benign_release_requeues_job_and_invalid_lease_is_rejected(settings: Settings) -> None:
    application = create_app(
        settings=replace(settings, worker_token_pepper=TOKEN_PEPPER),
        start_worker=False,
    )
    with TestClient(application) as client:
        recording = _upload(client)
        issued = _issue_worker(client)
        token = str(issued["token"])
        lease = _claim(client, token)
        lease_headers = {
            "Authorization": f"Bearer {token}",
            "X-Transcription-Lease": str(lease["lease_token"]),
        }

        bad_lease = client.post(
            f"/community/v1/jobs/{recording['id']}/heartbeat",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Transcription-Lease": "A" * 43,
            },
            json={"protocol_version": 1},
        )
        assert bad_lease.status_code == 409

        released = client.post(
            f"/community/v1/jobs/{recording['id']}/release",
            headers=lease_headers,
            json={"protocol_version": 1, "code": "outside_schedule"},
        )
        assert released.status_code == 204
        assert client.get(f"/recordings/{recording['id']}").json()["status"] == "queued"

        next_lease = _claim(client, token)
        assert next_lease["job_id"] == recording["id"]
        assert next_lease["lease_token"] != lease["lease_token"]
        assert client.get(f"/recordings/{recording['id']}").json()["attempts"] == 1


def test_worker_token_issuance_is_disabled_without_pepper(client: TestClient) -> None:
    assert client.post(
        "/admin/session",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    ).status_code == 200
    response = client.post("/admin/community-workers", json={"label": "GPU PC"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Community worker authentication is unavailable"}


def test_missing_prepared_audio_is_invalidated_during_download(settings: Settings) -> None:
    application = create_app(
        settings=replace(settings, worker_token_pepper=TOKEN_PEPPER),
        start_worker=False,
    )
    with TestClient(application) as client:
        recording = _upload(client)
        issued = _issue_worker(client)
        lease = _claim(client, str(issued["token"]))
        prepared_path = settings.prepared_audio_dir / f"{recording['id']}.ogg"
        prepared_path.unlink()

        response = client.get(
            str(lease["media_path"]),
            headers={
                "Authorization": f"Bearer {issued['token']}",
                "X-Transcription-Lease": str(lease["lease_token"]),
            },
        )

        assert response.status_code == 409
        assert client.app.state.repository.get_audio_artifact(recording["id"])["state"] == "pending"  # type: ignore[attr-defined]
        assert client.app.state.repository.get(recording["id"])["status"] == "queued"  # type: ignore[attr-defined]
        assert client.app.state.repository.get(recording["id"])["attempts"] == 0  # type: ignore[attr-defined]


def test_invalid_media_release_regenerates_only_when_server_copy_is_corrupt(settings: Settings) -> None:
    application = create_app(
        settings=replace(settings, worker_token_pepper=TOKEN_PEPPER),
        start_worker=False,
    )
    with TestClient(application) as client:
        recording = _upload(client)
        issued = _issue_worker(client)
        lease = _claim(client, str(issued["token"]))
        prepared_path = settings.prepared_audio_dir / f"{recording['id']}.ogg"
        prepared_path.write_bytes(b"X" * len(PREPARED_AUDIO))
        headers = {
            "Authorization": f"Bearer {issued['token']}",
            "X-Transcription-Lease": str(lease["lease_token"]),
        }

        response = client.post(
            f"/community/v1/jobs/{recording['id']}/release",
            headers=headers,
            json={"protocol_version": 1, "code": "invalid_media"},
        )

        assert response.status_code == 204
        artifact = client.app.state.repository.get_audio_artifact(recording["id"])  # type: ignore[attr-defined]
        assert artifact["state"] == "pending"
        # The background preparer owns removal to avoid racing with a newly published replacement.
        assert prepared_path.exists()
        stored = client.app.state.repository.get(recording["id"])  # type: ignore[attr-defined]
        assert stored["status"] == "queued"
        assert stored["attempts"] == 0


def test_invalid_media_release_does_not_regenerate_valid_server_audio(settings: Settings) -> None:
    application = create_app(
        settings=replace(settings, worker_token_pepper=TOKEN_PEPPER),
        start_worker=False,
    )
    with TestClient(application) as client:
        recording = _upload(client)
        issued = _issue_worker(client)
        lease = _claim(client, str(issued["token"]))
        response = client.post(
            f"/community/v1/jobs/{recording['id']}/release",
            headers={
                "Authorization": f"Bearer {issued['token']}",
                "X-Transcription-Lease": str(lease["lease_token"]),
            },
            json={"protocol_version": 1, "code": "invalid_media"},
        )

        assert response.status_code == 204
        assert client.app.state.repository.get_audio_artifact(recording["id"])["state"] == "ready"  # type: ignore[attr-defined]
        stored = client.app.state.repository.get(recording["id"])  # type: ignore[attr-defined]
        assert stored["status"] == "queued"
        assert stored["attempts"] == 1


def test_invalid_lease_is_rejected_before_server_hashes_audio(
    settings: Settings,
    monkeypatch,
) -> None:
    application = create_app(
        settings=replace(settings, worker_token_pepper=TOKEN_PEPPER),
        start_worker=False,
    )
    with TestClient(application) as client:
        recording = _upload(client)
        issued = _issue_worker(client)
        _claim(client, str(issued["token"]))

        def unexpected_hash(_path):
            raise AssertionError("invalid lease must be rejected before hashing")

        monkeypatch.setattr(main_module, "_file_sha256", unexpected_hash)
        response = client.post(
            f"/community/v1/jobs/{recording['id']}/release",
            headers={
                "Authorization": f"Bearer {issued['token']}",
                "X-Transcription-Lease": "A" * 43,
            },
            json={"protocol_version": 1, "code": "invalid_media"},
        )
        assert response.status_code == 409
