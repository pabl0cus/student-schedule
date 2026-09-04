from __future__ import annotations

import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

from ...constants import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable)
        return subprocess.list2cmdline([str(executable)])

    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    selected = pythonw if pythonw.exists() else executable
    return subprocess.list2cmdline([str(selected), "-m", "student_schedule_worker"])


def set_autostart(enabled: bool) -> None:
    if os.name != "nt":
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            with suppress(FileNotFoundError):
                winreg.DeleteValue(key, APP_NAME)
