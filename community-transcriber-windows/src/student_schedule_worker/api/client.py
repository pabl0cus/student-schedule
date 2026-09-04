from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .. import __version__
from ..constants import DOWNLOAD_CHUNK_BYTES, MAX_MEDIA_BYTES, PROTOCOL_PREFIX, PROTOCOL_VERSION
from .dto import JobLease
from .errors import (
    ApiError,
    LeaseLostError,
    MediaIntegrityError,
    OperationCancelled,
    ProtocolError,
    UnauthorizedError,
)
from .security import normalized_server_origin, protocol_url

CONTENT_RANGE_PATTERN = re.compile(r"^bytes (?P<start>\d+)-(?P<end>\d+)/(?P<total>\d+)$")


class CommunityApiClient:
    def __init__(
        self,
        server_url: str,
        token: str,
        worker_id: str,
        *,
        client: httpx.Client | None = None,
    ):
        self.server_url = normalized_server_origin(server_url)
        self.worker_id = worker_id
        normalized_token = token.strip()
        if len(normalized_token) < 16 or len(normalized_token) > 4096:
            raise ValueError("worker token has an invalid length")
        self._headers = {
            "Authorization": f"Bearer {normalized_token}",
            "User-Agent": f"student-schedule-worker/{__version__}",
        }
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=False)
        self.claim_retry_after_seconds = 15.0

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CommunityApiClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _url(self, path: str) -> str:
        return protocol_url(self.server_url, path)

    def _lease_headers(self, lease: JobLease) -> dict[str, str]:
        return {**self._headers, "X-Transcription-Lease": lease.lease_token}

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise UnauthorizedError("worker token was rejected", status_code=response.status_code)
        if response.status_code == 409:
            raise LeaseLostError("transcription lease is no longer active", status_code=409)
        if 300 <= response.status_code < 400:
            raise ProtocolError("redirects are not allowed", status_code=response.status_code)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ApiError(f"server returned HTTP {response.status_code}", status_code=response.status_code) from exc

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ProtocolError("server returned invalid JSON") from exc

    def get_me(self) -> dict[str, Any]:
        response = self._client.get(self._url(f"{PROTOCOL_PREFIX}/me"), headers=self._headers)
        self._raise(response)
        body = self._json(response)
        if not isinstance(body, dict):
            raise ProtocolError("/me response must be an object")
        return body

    def claim(self) -> JobLease | None:
        response = self._client.post(
            self._url(f"{PROTOCOL_PREFIX}/jobs/claim"),
            headers=self._headers,
            json={
                "protocol_version": PROTOCOL_VERSION,
                "client_id": self.worker_id,
                "client_version": __version__,
            },
        )
        if response.status_code == 204:
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                self.claim_retry_after_seconds = min(max(float(retry_after), 1.0), 300.0)
            return None
        self._raise(response)
        lease = JobLease.from_dict(self._json(response))
        expected_path = f"{PROTOCOL_PREFIX}/jobs/{lease.job_id}/media"
        if lease.media_path != expected_path:
            raise ProtocolError("server returned an unexpected media path")
        if lease.media.size_bytes > MAX_MEDIA_BYTES:
            raise ProtocolError("job media exceeds the client safety limit")
        return lease

    def download_media(
        self,
        lease: JobLease,
        destination: Path,
        *,
        should_continue: Callable[[], bool] = lambda: True,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(f"{destination.suffix}.part")
        existing = partial.stat().st_size if partial.exists() else 0
        if existing > lease.media.size_bytes:
            partial.unlink(missing_ok=True)
            existing = 0

        digest = hashlib.sha256()
        if existing:
            with partial.open("rb") as downloaded:
                while chunk := downloaded.read(DOWNLOAD_CHUNK_BYTES):
                    if not should_continue():
                        raise OperationCancelled("media download was cancelled")
                    digest.update(chunk)
        if existing == lease.media.size_bytes:
            if digest.hexdigest() != lease.media.sha256:
                partial.unlink(missing_ok=True)
                raise MediaIntegrityError("downloaded media hash does not match the job descriptor")
            if not should_continue():
                raise OperationCancelled("media download was cancelled")
            partial.replace(destination)
            return

        headers = self._lease_headers(lease)
        headers["Accept-Encoding"] = "identity"
        if existing:
            headers["Range"] = f"bytes={existing}-"

        with self._client.stream("GET", self._url(lease.media_path), headers=headers) as response:
            self._raise(response)
            if existing and response.status_code == 200:
                existing = 0
                digest = hashlib.sha256()
            elif existing and response.status_code != 206:
                raise ProtocolError("server did not honor the media range request")
            elif not existing and response.status_code != 200:
                raise ProtocolError("unexpected media response status")

            content_encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
            if content_encoding not in ("", "identity"):
                raise ProtocolError("downloaded media must not use content encoding")
            if response.status_code == 206:
                match = CONTENT_RANGE_PATTERN.fullmatch(response.headers.get("Content-Range", ""))
                if (
                    match is None
                    or int(match.group("start")) != existing
                    or int(match.group("end")) != lease.media.size_bytes - 1
                    or int(match.group("total")) != lease.media.size_bytes
                ):
                    raise ProtocolError("server returned an invalid media content range")
            expected_response_bytes = lease.media.size_bytes - existing
            content_length = response.headers.get("Content-Length", "")
            if not content_length.isdigit() or int(content_length) != expected_response_bytes:
                raise ProtocolError("server returned an invalid media content length")

            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            expected_type = lease.media.content_type.split(";", 1)[0].strip().lower()
            if content_type != expected_type:
                raise ProtocolError("downloaded media has an unexpected content type")

            mode = "ab" if existing else "wb"
            written = existing
            with partial.open(mode) as output:
                for chunk in response.iter_bytes(DOWNLOAD_CHUNK_BYTES):
                    if not should_continue():
                        raise OperationCancelled("media download was cancelled")
                    written += len(chunk)
                    if written > lease.media.size_bytes:
                        raise ProtocolError("server sent more media than declared")
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
            if written != lease.media.size_bytes:
                raise ProtocolError("downloaded media size does not match the job")
        if digest.hexdigest() != lease.media.sha256:
            partial.unlink(missing_ok=True)
            raise MediaIntegrityError("downloaded media hash does not match the job descriptor")
        if not should_continue():
            raise OperationCancelled("media download was cancelled")
        partial.replace(destination)

    def heartbeat(self, lease: JobLease, *, progress: int | None = None) -> datetime:
        payload: dict[str, int] = {"protocol_version": PROTOCOL_VERSION}
        if progress is not None:
            if not 0 <= progress <= 99:
                raise ValueError("progress must be between 0 and 99")
            payload["progress"] = progress
        response = self._client.post(
            self._url(f"{PROTOCOL_PREFIX}/jobs/{lease.job_id}/heartbeat"),
            headers=self._lease_headers(lease),
            json=payload,
        )
        self._raise(response)
        body = self._json(response)
        if not isinstance(body, dict) or not isinstance(body.get("lease_expires_at"), str):
            raise ProtocolError("heartbeat response must contain lease_expires_at")
        try:
            expires_at = datetime.fromisoformat(body["lease_expires_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProtocolError("heartbeat lease_expires_at is invalid") from exc
        if expires_at.tzinfo is None:
            raise ProtocolError("heartbeat lease_expires_at must contain a timezone")
        return expires_at

    def submit_result(self, lease: JobLease, payload: Mapping[str, Any]) -> str:
        response = self._client.put(
            self._url(f"{PROTOCOL_PREFIX}/jobs/{lease.job_id}/result"),
            headers=self._lease_headers(lease),
            json=dict(payload),
        )
        self._raise(response)
        body = self._json(response)
        if not isinstance(body, dict) or body.get("status") != "accepted":
            raise ProtocolError("result response was not an acceptance")
        disposition = body.get("disposition")
        if disposition not in {"completed", "duplicate"}:
            raise ProtocolError("result response contains an invalid disposition")
        return disposition

    def release(self, lease: JobLease, *, code: str, message: str | None = None) -> None:
        allowed_codes = {
            "outside_schedule",
            "shutdown",
            "cancelled",
            "download_failed",
            "invalid_media",
            "transcription_failed",
            "out_of_memory",
            "internal_error",
        }
        if code not in allowed_codes:
            raise ValueError("unknown release code")
        payload: dict[str, int | str] = {"protocol_version": PROTOCOL_VERSION, "code": code}
        if message:
            payload["message"] = message[:500]
        response = self._client.post(
            self._url(f"{PROTOCOL_PREFIX}/jobs/{lease.job_id}/release"),
            headers=self._lease_headers(lease),
            json=payload,
        )
        if response.status_code == 409:
            return
        self._raise(response)
