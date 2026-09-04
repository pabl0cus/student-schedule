from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ensure_worker_token_pepper import SETTING_NAME, ensure_worker_token_pepper


def _configured_value(env_path: Path) -> str:
    line = next(line for line in env_path.read_text(encoding="utf-8").splitlines() if line.startswith(SETTING_NAME))
    return line.split("=", 1)[1]


def test_worker_token_pepper_is_generated_once_without_changing_other_settings(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("APP_PORT=3002\nTRANSCRIPTION_WORKER_TOKEN_PEPPER=\n", encoding="utf-8")

    assert ensure_worker_token_pepper(env_path) is True
    generated = _configured_value(env_path)
    assert len(generated.encode("utf-8")) >= 32
    assert "APP_PORT=3002" in env_path.read_text(encoding="utf-8")

    assert ensure_worker_token_pepper(env_path) is False
    assert _configured_value(env_path) == generated


def test_short_existing_worker_token_pepper_is_rejected(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(f"{SETTING_NAME}=too-short\n", encoding="utf-8")

    with pytest.raises(ValueError, match="shorter than 32 bytes"):
        ensure_worker_token_pepper(env_path)


def test_duplicate_worker_token_pepper_is_rejected(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(f"{SETTING_NAME}=\n{SETTING_NAME}=\n", encoding="utf-8")

    with pytest.raises(ValueError, match="defined more than once"):
        ensure_worker_token_pepper(env_path)
