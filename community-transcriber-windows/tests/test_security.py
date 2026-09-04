from __future__ import annotations

import pytest

from student_schedule_worker.api.errors import ProtocolError
from student_schedule_worker.api.security import normalized_server_origin, protocol_url


@pytest.mark.parametrize(
    "url",
    [
        "http://schedule.example",
        "ftp://schedule.example",
        "https://user:pass@schedule.example",
        "https://schedule.example/path",
        "https://schedule.example/?query=1",
    ],
)
def test_unsafe_server_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        normalized_server_origin(url)


def test_explicit_loopback_http_is_allowed() -> None:
    assert normalized_server_origin("http://127.0.0.1:3002/") == "http://127.0.0.1:3002"
    assert normalized_server_origin("http://[::1]:3002") == "http://[::1]:3002"


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/community/v1/jobs/a/media",
        "//evil.example/community/v1/jobs/a/media",
        "/recordings/a/media",
        "/community/v1/../admin",
        "/community/v1/jobs/a/media?token=secret",
    ],
)
def test_cross_origin_or_unsafe_paths_are_rejected(path: str) -> None:
    with pytest.raises(ProtocolError):
        protocol_url("https://schedule.example", path)

