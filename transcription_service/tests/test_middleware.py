from __future__ import annotations

import asyncio

from app.middleware import LocalRequestGuardMiddleware, RequestSizeLimitMiddleware
from starlette.types import Message, Receive, Scope, Send


def _http_scope(*, method: str, path: str, headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8011),
        "state": {},
    }


def test_request_size_guard_counts_chunked_bodies_without_content_length() -> None:
    async def scenario() -> list[Message]:
        chunks = [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
        ]
        sent: list[Message] = []

        async def receive() -> Message:
            return chunks.pop(0)

        async def send(message: Message) -> None:
            sent.append(message)

        async def consume_body(scope: Scope, receive_body: Receive, _: Send) -> None:
            while True:
                message = await receive_body()
                if not message.get("more_body", False):
                    break

        middleware = RequestSizeLimitMiddleware(consume_body, max_bytes=7)
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/recordings",
            "raw_path": b"/recordings",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8011),
            "state": {},
        }

        await middleware(scope, receive, send)
        return sent

    messages = asyncio.run(scenario())
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 413


def test_request_size_guard_applies_route_specific_chunked_limit() -> None:
    async def scenario() -> tuple[list[Message], bool]:
        chunks = [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
        ]
        sent: list[Message] = []
        app_completed = False

        async def receive() -> Message:
            return chunks.pop(0)

        async def send(message: Message) -> None:
            sent.append(message)

        async def consume_body(scope: Scope, receive_body: Receive, _: Send) -> None:
            nonlocal app_completed
            while True:
                message = await receive_body()
                if not message.get("more_body", False):
                    break
            app_completed = True

        middleware = RequestSizeLimitMiddleware(
            consume_body,
            max_bytes=100,
            route_max_bytes={("POST", "/attachments"): 7},
        )
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/attachments",
            "raw_path": b"/attachments",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8011),
            "state": {},
        }

        await middleware(scope, receive, send)
        return sent, app_completed

    messages, app_completed = asyncio.run(scenario())
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 413
    assert not app_completed


def test_request_size_guard_applies_longest_matching_prefix_limit() -> None:
    async def scenario() -> tuple[list[Message], bool]:
        chunks = [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
        ]
        sent: list[Message] = []
        app_completed = False

        async def receive() -> Message:
            return chunks.pop(0)

        async def send(message: Message) -> None:
            sent.append(message)

        async def consume_body(scope: Scope, receive_body: Receive, _: Send) -> None:
            nonlocal app_completed
            while True:
                message = await receive_body()
                if not message.get("more_body", False):
                    break
            app_completed = True

        middleware = RequestSizeLimitMiddleware(
            consume_body,
            max_bytes=100,
            prefix_max_bytes={
                ("PUT", "/community/v1/"): 50,
                ("PUT", "/community/v1/jobs/"): 7,
            },
        )
        await middleware(
            _http_scope(method="PUT", path="/community/v1/jobs/00000000-0000-0000-0000-000000000001/result"),
            receive,
            send,
        )
        return sent, app_completed

    messages, app_completed = asyncio.run(scenario())
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 413
    assert not app_completed


def test_request_size_guard_prefers_exact_route_over_prefix() -> None:
    middleware = RequestSizeLimitMiddleware(
        lambda _scope, _receive, _send: None,  # type: ignore[arg-type]
        max_bytes=100,
        route_max_bytes={("POST", "/community/v1/jobs/claim"): 5},
        prefix_max_bytes={("POST", "/community/v1/"): 10},
    )

    assert middleware._request_max_bytes("POST", "/community/v1/jobs/claim") == 5
    assert middleware._request_max_bytes("POST", "/community/v1/jobs/release") == 10
    assert middleware._request_max_bytes("GET", "/community/v1/jobs/claim") == 100


def test_local_request_guard_bypasses_only_the_community_api_prefix() -> None:
    async def scenario(path: str) -> tuple[bool, list[Message]]:
        app_called = False
        sent: list[Message] = []

        async def app(_: Scope, __: Receive, ___: Send) -> None:
            nonlocal app_called
            app_called = True

        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        middleware = LocalRequestGuardMiddleware(app, allowed_origins=("http://localhost:3000",))
        await middleware(_http_scope(method="POST", path=path), receive, send)
        return app_called, sent

    community_called, community_messages = asyncio.run(scenario("/community/v1/jobs/claim"))
    lookalike_called, lookalike_messages = asyncio.run(scenario("/community/v1-malicious/jobs/claim"))
    recordings_called, recordings_messages = asyncio.run(scenario("/recordings"))

    assert community_called
    assert community_messages == []
    assert not lookalike_called
    assert lookalike_messages[0]["status"] == 403
    assert not recordings_called
    assert recordings_messages[0]["status"] == 403
