from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit

from ..constants import PROTOCOL_PREFIX
from .errors import ProtocolError


def _is_loopback(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def normalized_server_origin(server_url: str) -> str:
    candidate = server_url.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("server URL must use HTTPS")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("server URL must contain a valid host and no credentials")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("server URL must be an origin without path, query, or fragment")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise ValueError("HTTP is allowed only for an explicit loopback server")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("server URL has an invalid port") from exc
    default_port = 443 if parsed.scheme == "https" else 80
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port in {None, default_port} else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def protocol_url(server_url: str, path: str) -> str:
    origin = normalized_server_origin(server_url)
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ProtocolError("server returned a cross-origin or decorated path")
    if not parsed.path.startswith(f"{PROTOCOL_PREFIX}/"):
        raise ProtocolError("server returned a path outside the community API")
    if "\\" in parsed.path or any(part in {".", ".."} for part in parsed.path.split("/")):
        raise ProtocolError("server returned an unsafe media path")
    return f"{origin}{parsed.path}"

