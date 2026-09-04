from __future__ import annotations

from student_schedule_worker.__main__ import _keyring_is_usable


def test_self_test_rejects_missing_and_zero_priority_keyrings() -> None:
    class NoPriority:
        pass

    class ZeroPriority:
        priority = 0

    class Usable:
        priority = 5

    assert not _keyring_is_usable(NoPriority())
    assert not _keyring_is_usable(ZeroPriority())
    assert _keyring_is_usable(Usable())


def test_self_test_rejects_keyring_fail_backend() -> None:
    FailBackend = type("Keyring", (), {"priority": 1})
    FailBackend.__module__ = "keyring.backends.fail"
    assert not _keyring_is_usable(FailBackend())
