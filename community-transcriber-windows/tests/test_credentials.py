from __future__ import annotations

from student_schedule_worker.auth.credential_store import CredentialStore


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        del self.values[(service_name, username)]


def test_token_round_trip_uses_server_scoped_credential() -> None:
    backend = FakeKeyring()
    store = CredentialStore(backend)
    worker_id = "worker-id"
    store.set("https://schedule.example", worker_id, "secret-token-value")
    assert store.get("https://schedule.example", worker_id) == "secret-token-value"
    assert store.get("https://other.example", worker_id) is None
    assert all("schedule.example" not in service for service, _username in backend.values)
    store.delete("https://schedule.example", worker_id)
    assert not backend.values

