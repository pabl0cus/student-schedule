# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules


datas = []
binaries = []
hiddenimports = collect_submodules("keyring.backends")
for package in ("ctranslate2", "faster_whisper"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

project_root = os.path.abspath(os.path.join(SPECPATH, ".."))

analysis = Analysis(
    [os.path.join(SPECPATH, "entrypoint.py")],
    pathex=[os.path.join(project_root, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest"],
    noarchive=False,
)
# The Codex/dev host may add an unrelated Poppler ICU build to PATH. Qt on Windows
# expects the operating-system ICU shim; bundling the foreign `icuuc.dll` causes
# WinError 127 in the frozen app. Neither PySide6-Essentials nor faster-whisper
# ships or requires these host DLLs.
analysis.binaries = [
    entry
    for entry in analysis.binaries
    if os.path.basename(entry[0]).lower() != "icuuc.dll"
    and not os.path.basename(entry[0]).lower().startswith("icudt")
]
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="StudentScheduleWorker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="StudentScheduleWorker",
)
