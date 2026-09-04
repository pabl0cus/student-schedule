from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from platformdirs import user_config_path

from ..constants import APP_SLUG
from .models import AppConfig


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (user_config_path(APP_SLUG, appauthor=False) / "config.json")

    def load(self) -> AppConfig | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read config: {exc}") from exc
        return AppConfig.from_dict(raw)

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n"
        temporary_name: str | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            os.replace(temporary_name, self.path)
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

    def delete(self) -> None:
        """Remove only this application's config file."""

        self.path.unlink(missing_ok=True)
