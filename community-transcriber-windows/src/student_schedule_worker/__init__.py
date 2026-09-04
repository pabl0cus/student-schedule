"""Community transcription worker for Student Schedule."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("student-schedule-worker")
except PackageNotFoundError:  # pragma: no cover - source checkout without installation
    __version__ = "0.1.0"


__all__ = ["__version__"]

