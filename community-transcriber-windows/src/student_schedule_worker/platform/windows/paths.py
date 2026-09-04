from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from platformdirs import user_cache_path, user_log_path

from ...constants import APP_SLUG

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AppPaths:
    cache: Path
    models: Path
    jobs: Path
    logs: Path

    @classmethod
    def discover(cls) -> AppPaths:
        cache = user_cache_path(APP_SLUG, appauthor=False)
        return cls(
            cache=cache,
            models=cache / "models",
            jobs=cache / "jobs",
            logs=user_log_path(APP_SLUG, appauthor=False),
        )

    def create(self) -> None:
        for path in (self.cache, self.models, self.jobs, self.logs):
            path.mkdir(parents=True, exist_ok=True)

    def cleanup_orphaned_jobs(self) -> None:
        """Best-effort cleanup limited to UUID directories owned by this application."""

        jobs_root = self.jobs.resolve()
        if jobs_root.parent != self.cache.resolve():
            raise ValueError("jobs directory must stay directly inside the worker cache")
        if not jobs_root.exists():
            return
        for candidate in jobs_root.iterdir():
            if not candidate.is_dir():
                continue
            try:
                UUID(candidate.name)
            except ValueError:
                continue
            try:
                shutil.rmtree(candidate)
            except OSError:
                logger.warning("Could not remove an orphaned job directory: %s", candidate.name, exc_info=True)
