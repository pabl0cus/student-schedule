from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
PASSWORD_DIGEST_BYTES = 32
PASSWORD_SALT_BYTES = 16
SESSION_TTL_SECONDS = 8 * 60 * 60
ADMIN_SESSION_COOKIE = "kpi_schedule_admin_session"

_SESSION_SECRET_PATTERN = re.compile(r"[A-Za-z0-9_-]{43,}")


def _encode_base64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid URL-safe base64 value")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def hash_password(
    password: str,
    *,
    salt: bytes | None = None,
    iterations: int = PASSWORD_ITERATIONS,
) -> str:
    if iterations != PASSWORD_ITERATIONS:
        raise ValueError(f"iterations must be {PASSWORD_ITERATIONS}")
    effective_salt = salt if salt is not None else secrets.token_bytes(PASSWORD_SALT_BYTES)
    if len(effective_salt) < PASSWORD_SALT_BYTES or len(effective_salt) > 64:
        raise ValueError(f"salt must contain between {PASSWORD_SALT_BYTES} and 64 bytes")

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        effective_salt,
        iterations,
        dklen=PASSWORD_DIGEST_BYTES,
    )
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(iterations),
            _encode_base64(effective_salt),
            _encode_base64(digest),
        )
    )


def _parse_password_hash(record: str | None) -> tuple[int, bytes, bytes] | None:
    if not record or len(record) > 1024:
        return None
    try:
        scheme, raw_iterations, raw_salt, raw_digest = record.split("$")
        iterations = int(raw_iterations)
        salt = _decode_base64(raw_salt)
        digest = _decode_base64(raw_digest)
    except (TypeError, ValueError):
        return None

    if (
        scheme != PASSWORD_SCHEME
        or iterations != PASSWORD_ITERATIONS
        or len(salt) < PASSWORD_SALT_BYTES
        or len(salt) > 64
        or len(digest) != PASSWORD_DIGEST_BYTES
    ):
        return None
    return iterations, salt, digest


def is_valid_password_hash(record: str | None) -> bool:
    return _parse_password_hash(record) is not None


def verify_password(password: str, record: str | None) -> bool:
    parsed = _parse_password_hash(record)
    if parsed is None:
        return False

    iterations, salt, expected_digest = parsed
    try:
        password_bytes = password.encode("utf-8")
    except UnicodeError:
        return False
    candidate_digest = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations, dklen=PASSWORD_DIGEST_BYTES)
    return hmac.compare_digest(candidate_digest, expected_digest)


@dataclass(frozen=True, slots=True)
class AdminAuth:
    username: str | None
    password_hash: str | None
    session_secret: str | None

    @property
    def is_configured(self) -> bool:
        try:
            valid_username = bool(self.username and self.username.encode("utf-8"))
        except UnicodeError:
            valid_username = False
        return bool(
            valid_username
            and len(self.username) <= 128
            and is_valid_password_hash(self.password_hash)
            and self.session_secret
            and _SESSION_SECRET_PATTERN.fullmatch(self.session_secret)
        )

    def authenticate(self, username: str, password: str) -> bool:
        if not self.is_configured or self.username is None:
            return False

        # Always execute the expensive password check, including for an unknown username.
        password_matches = verify_password(password, self.password_hash)
        try:
            username_matches = hmac.compare_digest(username.encode("utf-8"), self.username.encode("utf-8"))
        except UnicodeError:
            username_matches = False
        return username_matches and password_matches

    def issue_session(self, *, now: int | None = None) -> str:
        if not self.is_configured or self.username is None or self.session_secret is None:
            raise RuntimeError("Admin authentication is not configured")

        issued_at = int(time.time()) if now is None else now
        payload = {
            "exp": issued_at + SESSION_TTL_SECONDS,
            "iat": issued_at,
            "sub": self.username,
            "v": 1,
        }
        encoded_payload = _encode_base64(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = hmac.new(
            self.session_secret.encode("ascii"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{encoded_payload}.{_encode_base64(signature)}"

    def session_username(self, token: str | None, *, now: int | None = None) -> str | None:
        if (
            not self.is_configured
            or self.username is None
            or self.session_secret is None
            or not token
            or len(token) > 4096
        ):
            return None

        try:
            encoded_payload, encoded_signature = token.split(".")
            supplied_signature = _decode_base64(encoded_signature)
            expected_signature = hmac.new(
                self.session_secret.encode("ascii"),
                encoded_payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                return None

            payload = json.loads(_decode_base64(encoded_payload))
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return None

        if not isinstance(payload, dict):
            return None
        issued_at = payload.get("iat")
        expires_at = payload.get("exp")
        subject = payload.get("sub")
        version = payload.get("v")
        if (
            type(version) is not int
            or version != 1
            or type(issued_at) is not int
            or type(expires_at) is not int
            or not isinstance(subject, str)
            or expires_at - issued_at != SESSION_TTL_SECONDS
        ):
            return None

        current_time = int(time.time()) if now is None else now
        if issued_at > current_time + 60 or expires_at <= current_time:
            return None
        try:
            subject_matches = hmac.compare_digest(subject.encode("utf-8"), self.username.encode("utf-8"))
        except UnicodeError:
            return None
        if not subject_matches:
            return None
        return self.username
