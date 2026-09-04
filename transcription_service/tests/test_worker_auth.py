from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any

import pytest
from app.worker_auth import COMMUNITY_WORKER_TOKEN_PATTERN, CommunityWorkerAuth


class FakeCommunityWorkerRepository:
    def __init__(self) -> None:
        self.workers: dict[str, dict[str, Any]] = {}
        self.touched: list[str] = []
        self.allow_touch = True

    def create_community_worker(
        self,
        *,
        worker_id: str,
        label: str,
        token_hash: str,
    ) -> dict[str, Any]:
        worker = {
            "id": worker_id,
            "label": label,
            "token_hash": token_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_seen_at": None,
            "revoked_at": None,
        }
        self.workers[worker_id] = worker
        return dict(worker)

    def get_community_worker(self, worker_id: str) -> dict[str, Any] | None:
        worker = self.workers.get(worker_id)
        return dict(worker) if worker is not None else None

    def touch_community_worker(self, worker_id: str) -> bool:
        self.touched.append(worker_id)
        return self.allow_touch and self.workers.get(worker_id, {}).get("revoked_at") is None


def test_issue_returns_secret_once_and_persists_only_hmac() -> None:
    repository = FakeCommunityWorkerRepository()
    pepper = "p" * 32
    auth = CommunityWorkerAuth(repository=repository, pepper=pepper)

    created = auth.issue("  Volunteer GPU  ")

    assert created.label == "Volunteer GPU"
    assert COMMUNITY_WORKER_TOKEN_PATTERN.fullmatch(created.token)
    stored = repository.workers[created.id]
    assert created.token not in stored.values()
    assert stored["token_hash"] == hmac.new(
        pepper.encode(),
        created.token.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def test_authenticate_accepts_only_matching_active_token_and_redacts_hash() -> None:
    repository = FakeCommunityWorkerRepository()
    auth = CommunityWorkerAuth(repository=repository, pepper="p" * 32)
    created = auth.issue("GPU")

    authenticated = auth.authenticate(created.token)

    assert authenticated is not None
    assert authenticated["id"] == created.id
    assert "token_hash" not in authenticated
    assert repository.touched == [created.id]

    prefix, secret = created.token.rsplit(".", 1)
    replacement = "A" if secret[-1] != "A" else "B"
    assert auth.authenticate(f"{prefix}.{secret[:-1]}{replacement}") is None
    assert repository.touched == [created.id]


def test_revoked_or_racing_revocation_is_rejected() -> None:
    repository = FakeCommunityWorkerRepository()
    auth = CommunityWorkerAuth(repository=repository, pepper="p" * 32)
    created = auth.issue("GPU")
    repository.workers[created.id]["revoked_at"] = datetime.now(timezone.utc).isoformat()

    assert auth.authenticate(created.token) is None
    assert repository.touched == []

    repository.workers[created.id]["revoked_at"] = None
    repository.allow_touch = False
    assert auth.authenticate(created.token) is None
    assert repository.touched == [created.id]


@pytest.mark.parametrize("pepper", (None, "", "too-short"))
def test_missing_or_short_pepper_disables_worker_authentication(pepper: str | None) -> None:
    repository = FakeCommunityWorkerRepository()
    auth = CommunityWorkerAuth(repository=repository, pepper=pepper)

    assert auth.is_configured is False
    assert auth.authenticate("anything") is None
    with pytest.raises(RuntimeError, match="not configured"):
        auth.issue("GPU")


@pytest.mark.parametrize(
    "token",
    (
        None,
        "",
        "not-a-token",
        "sst_wk_v1_../../etc/passwd.secret",
        "x" * 257,
        "sst_wk_v1_абвгдеєжзиіїклмн.секрет",
    ),
)
def test_malformed_tokens_are_rejected(token: str | None) -> None:
    repository = FakeCommunityWorkerRepository()
    auth = CommunityWorkerAuth(repository=repository, pepper="p" * 32)

    assert auth.authenticate(token) is None
    assert repository.touched == []
