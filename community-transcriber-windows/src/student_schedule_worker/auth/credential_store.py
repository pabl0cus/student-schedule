from __future__ import annotations

import hashlib
from typing import Protocol

from ..api.security import normalized_server_origin
from ..constants import APP_SLUG


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class CredentialStore:
    """Stores the administrator-issued worker token outside config.json."""

    def __init__(self, backend: KeyringBackend | None = None):
        if backend is None:
            import keyring

            backend = keyring
        self._backend = backend

    @staticmethod
    def _service_name(server_url: str) -> str:
        origin = normalized_server_origin(server_url)
        digest = hashlib.sha256(origin.encode("utf-8")).hexdigest()[:16]
        return f"{APP_SLUG}:{digest}"

    def get(self, server_url: str, worker_id: str) -> str | None:
        return self._backend.get_password(self._service_name(server_url), worker_id)

    def set(self, server_url: str, worker_id: str, token: str) -> None:
        normalized = token.strip()
        if len(normalized) < 16 or len(normalized) > 4096:
            raise ValueError("worker token has an invalid length")
        self._backend.set_password(self._service_name(server_url), worker_id, normalized)

    def delete(self, server_url: str, worker_id: str) -> None:
        try:
            self._backend.delete_password(self._service_name(server_url), worker_id)
        except Exception as exc:
            # keyring raises a backend-specific error when the entry does not exist.
            if exc.__class__.__name__ != "PasswordDeleteError":
                raise
