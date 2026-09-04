from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from student_schedule_worker.api.client import CommunityApiClient
from student_schedule_worker.api.errors import MediaIntegrityError, ProtocolError

TOKEN = "community-test-token"
WORKER_ID = "d22a125d-3fb2-4229-811b-bf850286679b"
JOB_ID = "414a0e31-9e5c-4665-9967-303576b1e1f9"
AUDIO = b"audio"
AUDIO_SHA256 = hashlib.sha256(AUDIO).hexdigest()


def job_payload(*, media_path: str | None = None) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "job_id": JOB_ID,
        "lease_token": "lease-secret-value-abcdefghijklmnopqrstuvwxyz",
        "lease_expires_at": "2026-09-04T12:02:00Z",
        "media_path": media_path or f"/community/v1/jobs/{JOB_ID}/media",
        "media": {"size_bytes": len(AUDIO), "content_type": "audio/ogg", "sha256": AUDIO_SHA256},
        "transcription": {
            "model": "large-v3",
            "language": "uk",
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps": True,
            "initial_prompt": "Українська лекція",
        },
    }


def test_claim_204_returns_none_and_sends_bearer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/community/v1/jobs/claim"
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert json.loads(request.content) == {
            "protocol_version": 1,
            "client_id": WORKER_ID,
            "client_version": "0.1.0",
        }
        return httpx.Response(204, headers={"Retry-After": "30"})

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, follow_redirects=False)
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    assert client.claim() is None
    assert client.claim_retry_after_seconds == 30
    http.close()


def test_claim_parses_expected_contract() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=job_payload()))
    http = httpx.Client(transport=transport, follow_redirects=False)
    lease = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http).claim()
    assert lease is not None
    assert UUID(lease.job_id)
    assert lease.transcription.model == "large-v3"
    assert lease.transcription.language == "uk"
    http.close()


def test_claim_rejects_cross_origin_media_path() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json=job_payload(media_path="https://evil.example/audio.flac"))
    )
    http = httpx.Client(transport=transport, follow_redirects=False)
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    with pytest.raises(ProtocolError):
        client.claim()
    http.close()


def test_result_uses_lease_header_and_put() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=job_payload())
        if request.url.path.endswith("/result"):
            return httpx.Response(200, json={"status": "accepted", "disposition": "completed"})
        return httpx.Response(204)

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    lease = client.claim()
    assert lease is not None
    assert client.submit_result(lease, {"submission_id": "fixed"}) == "completed"
    result_request = requests[-1]
    assert result_request.method == "PUT"
    assert result_request.headers["X-Transcription-Lease"] == "lease-secret-value-abcdefghijklmnopqrstuvwxyz"
    assert result_request.url.path == f"/community/v1/jobs/{JOB_ID}/result"
    http.close()


@pytest.mark.parametrize(
    "response_body",
    [
        {},
        {"status": "accepted", "disposition": "unknown"},
        {"status": "rejected", "disposition": "completed"},
        [],
    ],
)
def test_result_rejects_malformed_success_response(response_body) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=job_payload())
        return httpx.Response(200, json=response_body)

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    lease = client.claim()
    assert lease is not None
    with pytest.raises(ProtocolError):
        client.submit_result(lease, {"submission_id": "fixed"})
    http.close()


def test_heartbeat_and_release_match_server_contract_exactly() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=job_payload())
        if request.url.path.endswith("/heartbeat"):
            return httpx.Response(200, json={"lease_expires_at": "2026-09-04T12:10:00Z"})
        return httpx.Response(204)

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    lease = client.claim()
    assert lease is not None
    renewed_until = client.heartbeat(lease, progress=50)
    client.release(lease, code="outside_schedule")
    assert json.loads(requests[-2].content) == {"protocol_version": 1, "progress": 50}
    assert json.loads(requests[-1].content) == {"protocol_version": 1, "code": "outside_schedule"}
    assert renewed_until.isoformat() == "2026-09-04T12:10:00+00:00"
    http.close()


def test_non_normalized_video_container_is_rejected() -> None:
    payload = job_payload()
    payload["media"] = {"size_bytes": 5, "content_type": "video/mp4", "sha256": AUDIO_SHA256}
    http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
        follow_redirects=False,
    )
    with pytest.raises(ProtocolError, match="normalized Ogg"):
        CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http).claim()
    http.close()


def test_media_descriptor_requires_sha256() -> None:
    payload = job_payload()
    payload["media"] = {"size_bytes": 5, "content_type": "audio/ogg"}
    http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
        follow_redirects=False,
    )
    with pytest.raises(ProtocolError, match="sha256"):
        CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http).claim()
    http.close()


@pytest.mark.parametrize(
    "media",
    [
        {"size_bytes": len(AUDIO), "content_type": "audio/ogg", "sha256": "A" * 64},
        {
            "size_bytes": 500 * 1024 * 1024 + 1,
            "content_type": "audio/ogg",
            "sha256": AUDIO_SHA256,
        },
    ],
)
def test_media_descriptor_rejects_noncanonical_hash_or_oversized_audio(media: dict[str, object]) -> None:
    payload = job_payload()
    payload["media"] = media
    http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
        follow_redirects=False,
    )

    with pytest.raises(ProtocolError):
        CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http).claim()
    http.close()


def test_download_verifies_hash_and_resumes_exact_range(tmp_path: Path) -> None:
    payload = job_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=payload)
        assert request.headers["Range"] == "bytes=2-"
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(
            206,
            content=AUDIO[2:],
            headers={
                "Content-Type": "audio/ogg",
                "Content-Range": f"bytes 2-4/{len(AUDIO)}",
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    lease = client.claim()
    assert lease is not None
    destination = tmp_path / "source.ogg"
    destination.with_suffix(".ogg.part").write_bytes(AUDIO[:2])

    client.download_media(lease, destination)

    assert destination.read_bytes() == AUDIO
    assert not destination.with_suffix(".ogg.part").exists()
    http.close()


def test_complete_partial_file_is_hashed_before_rename(tmp_path: Path) -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=job_payload())),
        follow_redirects=False,
    )
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    lease = client.claim()
    assert lease is not None
    destination = tmp_path / "source.ogg"
    partial = destination.with_suffix(".ogg.part")
    partial.write_bytes(b"wrong")

    with pytest.raises(MediaIntegrityError):
        client.download_media(lease, destination)

    assert not destination.exists()
    assert not partial.exists()
    http.close()


def test_streamed_hash_mismatch_never_publishes_destination(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=job_payload())
        return httpx.Response(200, content=b"wrong", headers={"Content-Type": "audio/ogg"})

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    lease = client.claim()
    assert lease is not None
    destination = tmp_path / "source.ogg"

    with pytest.raises(MediaIntegrityError):
        client.download_media(lease, destination)

    assert not destination.exists()
    assert not destination.with_suffix(".ogg.part").exists()
    http.close()


def test_range_ignored_with_200_restarts_partial_download_safely(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=job_payload())
        assert request.headers["Range"] == "bytes=2-"
        return httpx.Response(200, content=AUDIO, headers={"Content-Type": "audio/ogg"})

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    lease = client.claim()
    assert lease is not None
    destination = tmp_path / "source.ogg"
    destination.with_suffix(".ogg.part").write_bytes(AUDIO[:2])

    client.download_media(lease, destination)

    assert destination.read_bytes() == AUDIO
    http.close()


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "audio/mpeg", "Content-Length": str(len(AUDIO))},
        {"Content-Type": "audio/ogg", "Content-Length": str(len(AUDIO) - 1)},
        {
            "Content-Type": "audio/ogg",
            "Content-Length": str(len(AUDIO)),
            "Content-Encoding": "gzip",
        },
    ],
)
def test_download_rejects_unsafe_response_headers(
    tmp_path: Path,
    headers: dict[str, str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=job_payload())
        return httpx.Response(200, stream=httpx.ByteStream(AUDIO), headers=headers)

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    lease = client.claim()
    assert lease is not None
    destination = tmp_path / "source.ogg"

    with pytest.raises(ProtocolError):
        client.download_media(lease, destination)

    assert not destination.exists()
    http.close()


def test_download_requires_content_length_and_content_type(tmp_path: Path) -> None:
    responses = iter(
        [
            httpx.Response(200, stream=httpx.ByteStream(AUDIO), headers={"Content-Type": "audio/ogg"}),
            httpx.Response(200, content=AUDIO, headers={"Content-Length": str(len(AUDIO))}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=job_payload())
        return next(responses)

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    lease = client.claim()
    assert lease is not None

    with pytest.raises(ProtocolError, match="content length"):
        client.download_media(lease, tmp_path / "missing-length.ogg")
    with pytest.raises(ProtocolError, match="content type"):
        client.download_media(lease, tmp_path / "missing-type.ogg")
    http.close()


def test_job_requires_supported_protocol_version() -> None:
    payload = job_payload()
    payload["protocol_version"] = 2
    http = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload)),
        follow_redirects=False,
    )
    with pytest.raises(ProtocolError):
        CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http).claim()
    http.close()


def test_redirect_is_not_followed() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(307, headers={"Location": "https://evil.example"}))
    http = httpx.Client(transport=transport, follow_redirects=False)
    client = CommunityApiClient("https://schedule.example", TOKEN, WORKER_ID, client=http)
    with pytest.raises(ProtocolError):
        client.get_me()
    http.close()
