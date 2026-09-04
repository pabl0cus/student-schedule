from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any, Protocol

from .schemas import CommunityWorkerCreate, CommunityWorkerCreated

COMMUNITY_WORKER_TOKEN_PREFIX = "sst_wk_v1_"
COMMUNITY_WORKER_PUBLIC_ID_BYTES = 12
COMMUNITY_WORKER_SECRET_BYTES = 32
COMMUNITY_WORKER_PUBLIC_ID_LENGTH = 16
COMMUNITY_WORKER_SECRET_LENGTH = 43
MINIMUM_PEPPER_BYTES = 32
MAXIMUM_TOKEN_LENGTH = 256

COMMUNITY_WORKER_TOKEN_PATTERN = re.compile(
    rf"^{COMMUNITY_WORKER_TOKEN_PREFIX}"
    rf"(?P<worker_id>[A-Za-z0-9_-]{{{COMMUNITY_WORKER_PUBLIC_ID_LENGTH}}})\."
    rf"(?P<secret>[A-Za-z0-9_-]{{{COMMUNITY_WORKER_SECRET_LENGTH}}})$"
)
DUMMY_TOKEN_HASH = "0" * (hashlib.sha256().digest_size * 2)


class CommunityWorkerRepository(Protocol):
    def create_community_worker(
        self,
        *,
        worker_id: str,
        label: str,
        token_hash: str,
    ) -> dict[str, Any]: ...

    def get_community_worker(self, worker_id: str) -> dict[str, Any] | None: ...

    def touch_community_worker(self, worker_id: str) -> bool: ...


class CommunityWorkerAuth:
    def __init__(self, *, repository: CommunityWorkerRepository, pepper: str | None):
        self.repository = repository
        self.pepper = pepper

    @property
    def is_configured(self) -> bool:
        if not self.pepper:
            return False
        try:
            return len(self.pepper.encode("utf-8")) >= MINIMUM_PEPPER_BYTES
        except UnicodeError:
            return False

    def _token_hash(self, token: str) -> str:
        if not self.is_configured or self.pepper is None:
            raise RuntimeError("Community worker authentication is not configured")
        return hmac.new(
            self.pepper.encode("utf-8"),
            token.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _public_worker(record: dict[str, Any], *, token: str) -> CommunityWorkerCreated:
        return CommunityWorkerCreated.model_validate(
            {
                "id": record["id"],
                "label": record["label"],
                "created_at": record["created_at"],
                "last_seen_at": record.get("last_seen_at"),
                "revoked_at": record.get("revoked_at"),
                "token": token,
            }
        )

    def issue(self, label: str) -> CommunityWorkerCreated:
        if not self.is_configured:
            raise RuntimeError("Community worker authentication is not configured")

        normalized_label = CommunityWorkerCreate(label=label).label
        worker_id = secrets.token_urlsafe(COMMUNITY_WORKER_PUBLIC_ID_BYTES)
        secret = secrets.token_urlsafe(COMMUNITY_WORKER_SECRET_BYTES)
        token = f"{COMMUNITY_WORKER_TOKEN_PREFIX}{worker_id}.{secret}"
        token_hash = self._token_hash(token)
        record = self.repository.create_community_worker(
            worker_id=worker_id,
            label=normalized_label,
            token_hash=token_hash,
        )
        return self._public_worker(record, token=token)

    def authenticate(self, token: str | None) -> dict[str, Any] | None:
        if not self.is_configured:
            return None

        candidate = token if token is not None and len(token) <= MAXIMUM_TOKEN_LENGTH else ""
        match = COMMUNITY_WORKER_TOKEN_PATTERN.fullmatch(candidate)
        worker_id = match.group("worker_id") if match is not None else None
        record = self.repository.get_community_worker(worker_id) if worker_id is not None else None

        try:
            candidate_hash = self._token_hash(candidate)
        except UnicodeEncodeError:
            candidate_hash = DUMMY_TOKEN_HASH
        stored_hash = record.get("token_hash") if record is not None else None
        expected_hash = stored_hash if isinstance(stored_hash, str) and len(stored_hash) == 64 else DUMMY_TOKEN_HASH
        hash_matches = hmac.compare_digest(candidate_hash, expected_hash)

        if not hash_matches or record is None or record.get("revoked_at") is not None:
            return None
        if not self.repository.touch_community_worker(worker_id):
            return None

        authenticated = dict(record)
        authenticated.pop("token_hash", None)
        return authenticated
