from __future__ import annotations

import os
import re
import secrets
import tempfile
from pathlib import Path

SETTING_NAME = "TRANSCRIPTION_WORKER_TOKEN_PEPPER"
SETTING_PATTERN = re.compile(rf"^(?P<prefix>\s*{SETTING_NAME}\s*=)(?P<value>.*?)(?P<newline>\r?\n)?$")
MINIMUM_SECRET_BYTES = 32


def _normalized_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def ensure_worker_token_pepper(env_path: Path) -> bool:
    """Create the deployment-specific worker-token pepper without revealing it."""

    try:
        existing = env_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        existing = ""

    lines = existing.splitlines(keepends=True)
    replacement_index: int | None = None
    replacement_prefix = f"{SETTING_NAME}="
    replacement_newline = "\r\n" if "\r\n" in existing else "\n"
    matches: list[tuple[int, re.Match[str]]] = []

    for index, line in enumerate(lines):
        match = SETTING_PATTERN.fullmatch(line)
        if match is not None:
            matches.append((index, match))

    if len(matches) > 1:
        raise ValueError(f"{SETTING_NAME} is defined more than once")
    if matches:
        replacement_index, match = matches[0]
        current_value = _normalized_value(match.group("value"))
        if current_value:
            if len(current_value.encode("utf-8")) < MINIMUM_SECRET_BYTES:
                raise ValueError(f"{SETTING_NAME} exists but is shorter than {MINIMUM_SECRET_BYTES} bytes")
            return False
        replacement_prefix = match.group("prefix")
        replacement_newline = match.group("newline") or replacement_newline

    secret = secrets.token_urlsafe(48)
    replacement = f"{replacement_prefix}{secret}{replacement_newline}"
    if replacement_index is None:
        if existing and not existing.endswith(("\n", "\r")):
            lines.append(replacement_newline)
        lines.append(replacement)
    else:
        lines[replacement_index] = replacement

    env_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            prefix=f".{env_path.name}.",
            suffix=".tmp",
            dir=env_path.parent,
            delete=False,
        ) as temporary:
            temporary.write("".join(lines))
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, env_path)
        if os.name != "nt":
            env_path.chmod(0o600)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return True


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    changed = ensure_worker_token_pepper(project_root / ".env")
    print("Worker-token pepper configured." if changed else "Worker-token pepper already configured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
