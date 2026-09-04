from __future__ import annotations

import sys


def _keyring_is_usable(backend: object) -> bool:
    try:
        return float(backend.priority) > 0 and backend.__class__.__module__ != "keyring.backends.fail"  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return False


def _self_test() -> int:
    import ctranslate2  # noqa: F401
    import httpx  # noqa: F401
    import keyring
    import platformdirs  # noqa: F401
    from faster_whisper import WhisperModel  # noqa: F401
    from PySide6.QtWidgets import QApplication

    from .config.models import ScheduleConfig, ScheduleMode
    from .schedule.evaluator import is_allowed

    app = QApplication.instance() or QApplication([])
    backend = keyring.get_keyring()
    if app is None or not _keyring_is_usable(backend):
        return 1
    return 0 if is_allowed(ScheduleConfig(mode=ScheduleMode.ALWAYS)) else 1


def main() -> None:
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    from .app import run

    raise SystemExit(run())


if __name__ == "__main__":
    main()
