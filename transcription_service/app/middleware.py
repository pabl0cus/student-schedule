from __future__ import annotations

from collections.abc import Mapping

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

LOCAL_REQUEST_HEADER = "X-KPI-Local-Request"
LOCAL_REQUEST_HEADER_VALUE = "1"
COMMUNITY_API_PREFIX = "/community/v1/"
UNSAFE_METHODS = frozenset({"DELETE", "PATCH", "POST", "PUT"})


class _RequestTooLarge(Exception):
    pass


class RequestSizeLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        route_max_bytes: Mapping[tuple[str, str], int] | None = None,
        prefix_max_bytes: Mapping[tuple[str, str], int] | None = None,
    ):
        self.app = app
        self.max_bytes = max_bytes
        self.route_max_bytes = {
            (method.upper(), path): route_max
            for (method, path), route_max in (route_max_bytes or {}).items()
        }
        self.prefix_max_bytes = tuple(
            sorted(
                (
                    ((method.upper(), prefix), prefix_max)
                    for (method, prefix), prefix_max in (prefix_max_bytes or {}).items()
                ),
                key=lambda item: len(item[0][1]),
                reverse=True,
            )
        )

    def _request_max_bytes(self, method: str, path: str) -> int:
        exact_limit = self.route_max_bytes.get((method, path))
        if exact_limit is not None:
            return exact_limit

        for (prefix_method, prefix), prefix_limit in self.prefix_max_bytes:
            if method == prefix_method and path.startswith(prefix):
                return prefix_limit
        return self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_max_bytes = self._request_max_bytes(scope["method"].upper(), scope["path"])
        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > request_max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > request_max_bytes:
                    raise _RequestTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestTooLarge:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"detail": "Request body exceeds the configured size limit"}, status_code=413)
        await response(scope, receive, send)


class LocalRequestGuardMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: tuple[str, ...],
        header_name: str = LOCAL_REQUEST_HEADER,
        header_value: str = LOCAL_REQUEST_HEADER_VALUE,
        unsafe_bypass_prefixes: tuple[str, ...] = (COMMUNITY_API_PREFIX,),
    ):
        self.app = app
        self.allowed_origins = frozenset(origin.rstrip("/") for origin in allowed_origins)
        self.header_name = header_name
        self.header_value = header_value
        self.unsafe_bypass_prefixes = unsafe_bypass_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"].upper() not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return

        if any(scope["path"].startswith(prefix) for prefix in self.unsafe_bypass_prefixes):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = (headers.get("origin") or "").rstrip("/")
        if origin not in self.allowed_origins or headers.get(self.header_name) != self.header_value:
            response = JSONResponse({"detail": "Forbidden request"}, status_code=403)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
