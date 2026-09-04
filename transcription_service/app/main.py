from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
import secrets
import time
import unicodedata
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from threading import Lock
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .audio_preparation import (
    PREPARED_CONTENT_TYPE,
    AudioEncoder,
    AudioPreparationWorker,
    FfmpegAudioEncoder,
)
from .auth import ADMIN_SESSION_COOKIE, SESSION_TTL_SECONDS, AdminAuth
from .config import Settings
from .database import (
    RecordingRepository,
    ScheduleSnapshotConflictError,
    ScheduleSnapshotTooLargeError,
    recording_search_tokens,
)
from .middleware import (
    LOCAL_REQUEST_HEADER,
    LocalRequestGuardMiddleware,
    RequestSizeLimitMiddleware,
)
from .schemas import (
    AdminLoginRequest,
    AdminSessionResponse,
    AttachmentSearchResponse,
    AttachmentSummary,
    CommunityJobClaim,
    CommunityJobHeartbeat,
    CommunityJobLease,
    CommunityJobRelease,
    CommunityJobResult,
    CommunityWorkerCreate,
    CommunityWorkerCreated,
    CommunityWorkerPublic,
    HealthResponse,
    RecordingDetail,
    RecordingSearchResponse,
    RecordingSearchScope,
    RecordingSummary,
    RecordingUploadContext,
    ScheduleScopeType,
    ScheduleSnapshot,
    ScheduleSnapshotMetadata,
    ScheduleSnapshotWrite,
    TranscriptionPayload,
)
from .transcriber import FasterWhisperTranscriber, Transcriber
from .worker import QueueWorker
from .worker_auth import CommunityWorkerAuth

ALLOWED_EXTENSIONS = {
    ".aac",
    ".avi",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".wav",
    ".webm",
}
MAX_LESSON_KEY_LENGTH = 4096
MAX_LIST_PAGE_SIZE = 200
MAX_SEARCH_QUERY_LENGTH = 200
MAX_UPLOAD_CONTEXT_LENGTH = 16 * 1024
TEXT_MAX_LENGTHS = {
    "lesson_key": MAX_LESSON_KEY_LENGTH,
    "lesson_title": 300,
    "scope_label": 200,
}
PUBLIC_PROXY_PREFIX = "/recordings-api"
COMMUNITY_API_PREFIX = "/community/v1"
COMMUNITY_LEASE_HEADER = "X-Transcription-Lease"
COMMUNITY_LEASE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")

logger = logging.getLogger(__name__)

DELETING_ARTIFACT_PATTERN = re.compile(
    r"^\.(?P<recording_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\.[0-9a-f]{32}\.deleting$"
)
MIME_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


def _require_community_worker(request: Request) -> dict[str, object]:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    community_auth: CommunityWorkerAuth = request.app.state.community_auth
    worker_record = community_auth.authenticate(token if separator and scheme.lower() == "bearer" else None)
    if worker_record is None:
        raise HTTPException(
            status_code=401,
            detail="Community worker authentication required",
            headers={
                "Cache-Control": "no-store",
                "WWW-Authenticate": "Bearer",
            },
        )
    return worker_record


CommunityWorkerDependency = Annotated[dict[str, object], Depends(_require_community_worker)]


def _clean_text(value: str, field_name: str) -> str:
    cleaned = unicodedata.normalize("NFC", value).strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail=f"{field_name} must not be blank")
    if len(cleaned) > TEXT_MAX_LENGTHS[field_name]:
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be at most {TEXT_MAX_LENGTHS[field_name]} characters",
        )
    if any(unicodedata.category(character) == "Cc" for character in cleaned):
        raise HTTPException(status_code=422, detail=f"{field_name} contains control characters")
    return cleaned


def _filename_leaf(filename: str | None) -> str:
    leaf_name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    leaf_name = unicodedata.normalize("NFC", leaf_name).strip()
    if not leaf_name or leaf_name in (".", "..") or len(leaf_name) > 255:
        raise HTTPException(status_code=422, detail="A filename of at most 255 characters is required")
    if any(unicodedata.category(character) == "Cc" for character in leaf_name):
        raise HTTPException(status_code=422, detail="Filename contains control characters")
    return leaf_name


def _safe_original_filename(filename: str | None) -> tuple[str, str]:
    leaf_name = _filename_leaf(filename)

    extension = Path(leaf_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        supported = ", ".join(sorted(ALLOWED_EXTENSIONS))
        raise HTTPException(status_code=415, detail=f"Unsupported media extension. Allowed: {supported}")
    return leaf_name, extension


def _content_type(upload: UploadFile, original_filename: str) -> str:
    supplied = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if supplied.startswith(("audio/", "video/")) and MIME_TYPE_PATTERN.fullmatch(supplied):
        return supplied[:127]
    guessed, _ = mimetypes.guess_type(original_filename)
    return guessed or "application/octet-stream"


def _attachment_content_type(upload: UploadFile, original_filename: str) -> str:
    supplied = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if len(supplied) <= 127 and MIME_TYPE_PATTERN.fullmatch(supplied):
        return supplied
    guessed, _ = mimetypes.guess_type(original_filename)
    return guessed or "application/octet-stream"


def _recorded_at(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
        raise HTTPException(status_code=422, detail="recorded_at must use YYYY-MM-DD format")
    try:
        return date.fromisoformat(normalized).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="recorded_at must be a valid calendar date") from exc


def _scope_id(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip()
    if not normalized or len(normalized) > 128:
        raise HTTPException(status_code=422, detail="scope_id must contain at most 128 characters")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", normalized):
        raise HTTPException(status_code=422, detail="scope_id contains unsupported characters")
    return normalized


def _upload_context(value: str | None) -> RecordingUploadContext:
    if value is None or not value.strip():
        return RecordingUploadContext()
    if len(value) > MAX_UPLOAD_CONTEXT_LENGTH:
        raise HTTPException(status_code=422, detail="context_json is too large")
    try:
        return RecordingUploadContext.model_validate_json(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="context_json is invalid") from exc


def _search_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if normalized and not recording_search_tokens(normalized):
        raise HTTPException(
            status_code=422,
            detail="Search query must contain a word with at least two characters",
        )
    return normalized


def _calendar_week_start(value: date, field_name: str = "week_start") -> str:
    if value.weekday() != 0:
        raise HTTPException(status_code=422, detail=f"{field_name} must be a Monday")
    return value.isoformat()


def _media_prefix(request: Request) -> str:
    forwarded_prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")
    if forwarded_prefix == PUBLIC_PROXY_PREFIX:
        return forwarded_prefix
    return str(request.scope.get("root_path", "")).rstrip("/")


def _admin_cookie_path(request: Request) -> str:
    prefix = _media_prefix(request)
    return f"{prefix}/admin" if prefix else "/admin"


def _public_recording(recording: dict[str, object], *, media_prefix: str = "") -> dict[str, object]:
    result = dict(recording)
    result.pop("stored_filename", None)
    result["file_name"] = recording["original_filename"]
    result["mime_type"] = recording["content_type"]
    result["language"] = recording["detected_language"]
    result["progress"] = {
        "queued": 0,
        "processing": 50,
        "ready": 100,
        "failed": 100,
    }[str(recording["status"])]
    result["media_url"] = f"{media_prefix}/recordings/{recording['id']}/media"
    return result


def _public_attachment(attachment: dict[str, object], *, media_prefix: str = "") -> dict[str, object]:
    result = dict(attachment)
    result.pop("stored_filename", None)
    result["file_name"] = attachment["original_filename"]
    result["mime_type"] = attachment["content_type"]
    result["content_url"] = f"{media_prefix}/attachments/{attachment['id']}/content"
    return result


def _validated_id(recording_id: str) -> str:
    try:
        return str(UUID(recording_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Recording not found") from exc


def _get_recording_or_404(repository: RecordingRepository, recording_id: str) -> dict[str, object]:
    recording = repository.get(_validated_id(recording_id))
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return recording


def _get_attachment_or_404(repository: RecordingRepository, attachment_id: str) -> dict[str, object]:
    try:
        normalized_id = str(UUID(attachment_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Attachment not found") from exc
    attachment = repository.get_attachment(normalized_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return attachment


def _resolve_media_path(media_dir: Path, stored_filename: str) -> Path:
    root = media_dir.resolve()
    candidate = (root / stored_filename).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=404, detail="Media file not found")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")
    return candidate


def _resolve_media_deletion_path(media_dir: Path, stored_filename: str) -> Path:
    root = media_dir.resolve()
    stored_path = Path(stored_filename)
    if stored_path.name != stored_filename or stored_filename in ("", ".", ".."):
        raise HTTPException(status_code=404, detail="Media file not found")

    candidate = root / stored_filename
    if candidate.parent.resolve() != root or candidate.is_symlink():
        raise HTTPException(status_code=404, detail="Media file not found")
    if candidate.exists() and not candidate.is_file():
        raise HTTPException(status_code=409, detail="Recording media is not a regular file")
    return candidate


def _resolve_prepared_audio_path(
    prepared_audio_dir: Path,
    stored_filename: object,
    recording_id: str,
) -> Path:
    expected_filename = f"{recording_id}.ogg"
    if stored_filename != expected_filename:
        raise HTTPException(status_code=404, detail="Prepared audio file not found")
    root = prepared_audio_dir.resolve()
    candidate = root / expected_filename
    try:
        if candidate.parent.resolve() != root or candidate.is_symlink() or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Prepared audio file not found")
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Prepared audio file not found") from exc
    return candidate


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _prepared_audio_matches_descriptor(path: Path, artifact: dict[str, object]) -> bool:
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and path.stat().st_size == artifact["size_bytes"]
            and artifact["content_type"] == PREPARED_CONTENT_TYPE
            and _file_sha256(path) == artifact["sha256"]
        )
    except OSError:
        return False


def _resolve_attachment_path(attachments_dir: Path, stored_filename: str, attachment_id: str) -> Path:
    try:
        canonical_stored_name = str(UUID(stored_filename))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Attachment file not found") from exc
    if canonical_stored_name != stored_filename or stored_filename != attachment_id:
        raise HTTPException(status_code=404, detail="Attachment file not found")

    root = attachments_dir.resolve()
    candidate = root / stored_filename
    try:
        if candidate.parent.resolve() != root or candidate.is_symlink() or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Attachment file not found")
    except OSError as exc:
        raise HTTPException(status_code=404, detail="Attachment file not found") from exc
    return candidate


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _cleanup_stale_uploads(temp_dir: Path, *, stale_after_seconds: float = 24 * 60 * 60) -> None:
    cutoff = time.time() - stale_after_seconds
    for path in temp_dir.glob(".*.uploading"):
        try:
            UUID(path.name[1 : -len(".uploading")])
            if path.stat().st_mtime > cutoff:
                continue
        except (OSError, ValueError):
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove stale upload artifact %s", path, exc_info=True)


def _recover_staged_deletions(media_dir: Path, repository: RecordingRepository) -> None:
    try:
        candidates = list(media_dir.iterdir())
    except OSError:
        logger.exception("Could not inspect staged recording deletions")
        return

    for staged_path in candidates:
        match = DELETING_ARTIFACT_PATTERN.fullmatch(staged_path.name)
        if match is None or (not staged_path.is_file() and not staged_path.is_symlink()):
            continue

        recording_id = match.group("recording_id")
        try:
            if str(UUID(recording_id)) != recording_id:
                continue
            recording = repository.get(recording_id)
            if recording is None:
                staged_path.unlink(missing_ok=True)
                _sync_directory(media_dir)
                continue
            if staged_path.is_symlink():
                logger.error("Refusing to restore symlinked deletion artifact %s", staged_path)
                continue

            original_path = _resolve_media_deletion_path(media_dir, str(recording["stored_filename"]))
            if original_path.exists():
                logger.error(
                    "Both original and staged media exist for recording %s; preserving both for manual recovery",
                    recording_id,
                )
                continue
            os.replace(staged_path, original_path)
            _sync_directory(media_dir)
            logger.warning("Restored media for interrupted recording deletion %s", recording_id)
        except Exception:
            logger.exception("Could not recover staged deletion artifact %s", staged_path)


def _recover_staged_audio_deletions(
    prepared_audio_dir: Path,
    repository: RecordingRepository,
) -> None:
    try:
        candidates = list(prepared_audio_dir.iterdir())
    except OSError:
        logger.exception("Could not inspect staged prepared-audio deletions")
        return

    for staged_path in candidates:
        match = DELETING_ARTIFACT_PATTERN.fullmatch(staged_path.name)
        if match is None or (not staged_path.is_file() and not staged_path.is_symlink()):
            continue
        recording_id = match.group("recording_id")
        try:
            if str(UUID(recording_id)) != recording_id:
                continue
            recording = repository.get(recording_id)
            artifact = repository.get_audio_artifact(recording_id)
            if recording is None or artifact is None or artifact["state"] != "ready":
                staged_path.unlink(missing_ok=True)
                _sync_directory(prepared_audio_dir)
                continue
            if staged_path.is_symlink():
                logger.error(
                    "Refusing to restore symlinked prepared-audio deletion %s",
                    staged_path,
                )
                continue
            expected_filename = f"{recording_id}.ogg"
            if artifact["stored_filename"] != expected_filename:
                logger.error("Refusing to restore prepared audio with an unexpected filename")
                continue
            original_path = prepared_audio_dir / expected_filename
            if original_path.exists():
                logger.error(
                    "Both original and staged prepared audio exist for recording %s; preserving both",
                    recording_id,
                )
                continue
            if original_path.parent.resolve() != prepared_audio_dir.resolve():
                logger.error("Refusing to restore prepared audio outside its storage directory")
                continue
            os.replace(staged_path, original_path)
            _sync_directory(prepared_audio_dir)
            logger.warning(
                "Restored prepared audio for interrupted recording deletion %s",
                recording_id,
            )
        except Exception:
            logger.exception("Could not recover staged prepared-audio artifact %s", staged_path)


def create_app(
    *,
    settings: Settings | None = None,
    repository: RecordingRepository | None = None,
    transcriber_factory: Callable[[], Transcriber] | None = None,
    audio_encoder_factory: Callable[[], AudioEncoder] | None = None,
    start_worker: bool | None = None,
    start_audio_preparer: bool | None = None,
) -> FastAPI:
    service_settings = settings or Settings.from_env()
    service_settings.media_dir.mkdir(parents=True, exist_ok=True)
    service_settings.attachments_dir.mkdir(parents=True, exist_ok=True)
    service_settings.prepared_audio_dir.mkdir(parents=True, exist_ok=True)
    service_settings.temp_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_stale_uploads(service_settings.temp_dir)
    service_repository = repository or RecordingRepository(service_settings.database_path)
    service_repository.initialize()
    _recover_staged_deletions(service_settings.media_dir, service_repository)
    _recover_staged_audio_deletions(service_settings.prepared_audio_dir, service_repository)
    admin_auth = AdminAuth(
        username=service_settings.admin_username,
        password_hash=service_settings.admin_password_hash,
        session_secret=service_settings.admin_session_secret,
    )
    community_auth = CommunityWorkerAuth(
        repository=service_repository,
        pepper=service_settings.worker_token_pepper,
    )
    local_worker_expected = service_settings.local_worker_enabled if start_worker is None else start_worker
    factory = transcriber_factory or (lambda: FasterWhisperTranscriber(service_settings))
    worker = QueueWorker(
        repository=service_repository,
        media_dir=service_settings.media_dir,
        lock_path=service_settings.worker_lock_path,
        transcriber_factory=factory,
        poll_seconds=service_settings.worker_poll_seconds,
    )
    encoder_factory = audio_encoder_factory or (
        lambda: FfmpegAudioEncoder(
            binary=service_settings.ffmpeg_binary,
            timeout_seconds=service_settings.audio_preparation_timeout_seconds,
            max_output_bytes=service_settings.max_prepared_audio_bytes,
        )
    )
    audio_preparer = AudioPreparationWorker(
        repository=service_repository,
        media_dir=service_settings.media_dir,
        prepared_audio_dir=service_settings.prepared_audio_dir,
        lock_path=service_settings.audio_preparation_lock_path,
        encoder_factory=encoder_factory,
        poll_seconds=service_settings.audio_preparation_poll_seconds,
        max_attempts=service_settings.audio_preparation_max_attempts,
        max_output_bytes=service_settings.max_prepared_audio_bytes,
    )
    audio_preparer_expected = (
        service_settings.audio_preparation_enabled if start_audio_preparer is None else start_audio_preparer
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        service_repository.recover_interrupted()
        try:
            if audio_preparer_expected:
                audio_preparer.start()
            if local_worker_expected:
                worker.start()
            yield
        finally:
            if local_worker_expected:
                worker.stop()
            if audio_preparer_expected:
                audio_preparer.stop()

    application = FastAPI(
        title="Student Schedule Transcription Service",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = service_settings
    application.state.repository = service_repository
    application.state.worker = worker
    application.state.audio_preparer = audio_preparer
    application.state.admin_auth = admin_auth
    application.state.community_auth = community_auth
    deletion_lock = Lock()

    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=list(service_settings.allowed_hosts),
        www_redirect=False,
    )
    application.add_middleware(
        LocalRequestGuardMiddleware,
        allowed_origins=service_settings.allowed_origins,
    )
    application.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=service_settings.max_json_bytes,
        route_max_bytes={
            ("POST", "/recordings"): service_settings.max_request_bytes,
            ("POST", "/attachments"): service_settings.max_attachment_request_bytes,
        },
        prefix_max_bytes={
            (
                "PUT",
                f"{COMMUNITY_API_PREFIX}/jobs/",
            ): service_settings.max_worker_result_bytes,
        },
    )

    if service_settings.allowed_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(service_settings.allowed_origins),
            allow_credentials=True,
            allow_methods=["DELETE", "GET", "POST", "PUT", "OPTIONS"],
            allow_headers=[
                "Content-Type",
                "Range",
                COMMUNITY_LEASE_HEADER,
                LOCAL_REQUEST_HEADER,
            ],
            expose_headers=["Accept-Ranges", "Content-Length", "Content-Range"],
        )

    def require_admin(request: Request) -> str:
        username = admin_auth.session_username(request.cookies.get(ADMIN_SESSION_COOKIE))
        if username is None:
            raise HTTPException(
                status_code=401,
                detail="Admin authentication required",
                headers={"Cache-Control": "no-store"},
            )
        return username

    def remote_lease_identity(
        recording_id: str,
        worker_id: str,
        lease_token: str | None,
    ) -> tuple[str, str, str]:
        normalized_recording_id = _validated_id(recording_id)
        if lease_token is None or COMMUNITY_LEASE_TOKEN_PATTERN.fullmatch(lease_token) is None:
            raise HTTPException(status_code=409, detail="Transcription lease is no longer valid")
        lease = service_repository.get_transcription_lease(normalized_recording_id)
        if lease is None:
            raise HTTPException(status_code=409, detail="Transcription lease is no longer valid")
        return (
            normalized_recording_id,
            str(lease["claim_id"]),
            hashlib.sha256(lease_token.encode("ascii")).hexdigest(),
        )

    @application.get("/health", response_model=HealthResponse)
    def health() -> dict[str, object]:
        service_repository.ping()
        if local_worker_expected and not worker.is_running:
            raise HTTPException(status_code=503, detail="Transcription worker is not running")
        if audio_preparer_expected and not audio_preparer.is_running:
            raise HTTPException(status_code=503, detail="Audio preparation worker is not running")
        return {
            "status": "ok",
            "database": "ok",
            "worker": "running" if worker.is_running else "stopped",
            "queue": service_repository.status_counts(),
        }

    @application.get(
        "/admin/session",
        response_model=AdminSessionResponse,
        response_model_exclude_none=True,
    )
    def get_admin_session(request: Request, response: Response) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        username = admin_auth.session_username(request.cookies.get(ADMIN_SESSION_COOKIE))
        if username is None:
            return {"authenticated": False}
        return {"authenticated": True, "username": username}

    @application.post(
        "/admin/session",
        response_model=AdminSessionResponse,
        response_model_exclude_none=True,
    )
    def create_admin_session(
        request: Request,
        credentials: AdminLoginRequest,
        response: Response,
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        if not admin_auth.is_configured:
            raise HTTPException(
                status_code=503,
                detail="Admin authentication is unavailable",
                headers={"Cache-Control": "no-store"},
            )
        if not admin_auth.authenticate(credentials.username, credentials.password):
            raise HTTPException(
                status_code=401,
                detail="Invalid username or password",
                headers={"Cache-Control": "no-store"},
            )

        response.set_cookie(
            key=ADMIN_SESSION_COOKIE,
            value=admin_auth.issue_session(),
            max_age=SESSION_TTL_SECONDS,
            path=_admin_cookie_path(request),
            secure=service_settings.secure_cookies,
            httponly=True,
            samesite="strict",
        )
        return {"authenticated": True, "username": service_settings.admin_username}

    @application.delete("/admin/session", status_code=status.HTTP_204_NO_CONTENT)
    def delete_admin_session(request: Request, response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.delete_cookie(
            key=ADMIN_SESSION_COOKIE,
            path=_admin_cookie_path(request),
            secure=service_settings.secure_cookies,
            httponly=True,
            samesite="strict",
        )

    @application.get(
        "/admin/recordings",
        response_model=list[RecordingSummary],
        dependencies=[Depends(require_admin)],
    )
    def list_admin_recordings(request: Request, response: Response) -> list[dict[str, object]]:
        response.headers["Cache-Control"] = "no-store"
        media_prefix = _media_prefix(request)
        return [
            _public_recording(recording, media_prefix=media_prefix)
            for recording in service_repository.list_all_recordings()
        ]

    @application.get(
        "/admin/community-workers",
        response_model=list[CommunityWorkerPublic],
        dependencies=[Depends(require_admin)],
    )
    def list_community_workers(response: Response) -> list[dict[str, object]]:
        response.headers["Cache-Control"] = "no-store"
        return [
            {
                "id": worker_record["id"],
                "label": worker_record["label"],
                "created_at": worker_record["created_at"],
                "last_seen_at": worker_record.get("last_seen_at"),
                "revoked_at": worker_record.get("revoked_at"),
            }
            for worker_record in service_repository.list_community_workers()
        ]

    @application.post(
        "/admin/community-workers",
        response_model=CommunityWorkerCreated,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_admin)],
    )
    def create_community_worker(worker: CommunityWorkerCreate, response: Response) -> CommunityWorkerCreated:
        response.headers["Cache-Control"] = "no-store"
        if not community_auth.is_configured:
            raise HTTPException(
                status_code=503,
                detail="Community worker authentication is unavailable",
                headers={"Cache-Control": "no-store"},
            )
        return community_auth.issue(worker.label)

    @application.delete(
        "/admin/community-workers/{worker_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin)],
    )
    def revoke_community_worker(worker_id: str, response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"
        if service_repository.revoke_community_worker(worker_id) is None:
            raise HTTPException(status_code=404, detail="Community worker not found")

    @application.get(f"{COMMUNITY_API_PREFIX}/me", response_model=CommunityWorkerPublic)
    def get_community_worker_profile(
        response: Response,
        worker_record: CommunityWorkerDependency,
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        return {
            "id": worker_record["id"],
            "label": worker_record["label"],
            "created_at": worker_record["created_at"],
            "last_seen_at": worker_record.get("last_seen_at"),
            "revoked_at": worker_record.get("revoked_at"),
        }

    @application.post(
        f"{COMMUNITY_API_PREFIX}/jobs/claim",
        response_model=None,
        responses={
            200: {"model": CommunityJobLease},
            204: {"description": "No transcription work is currently available"},
        },
    )
    def claim_community_job(
        claim: CommunityJobClaim,
        response: Response,
        worker_record: CommunityWorkerDependency,
    ) -> CommunityJobLease | Response:
        response.headers["Cache-Control"] = "no-store"
        claim_id = str(uuid4())
        lease_token = secrets.token_urlsafe(32)
        claimed = service_repository.claim_remote(
            worker_id=str(worker_record["id"]),
            claim_id=claim_id,
            lease_token_hash=hashlib.sha256(lease_token.encode("ascii")).hexdigest(),
            lease_seconds=service_settings.community_lease_seconds,
            max_attempts=service_settings.community_max_attempts,
        )
        if claimed is None:
            return Response(
                status_code=status.HTTP_204_NO_CONTENT,
                headers={"Cache-Control": "no-store", "Retry-After": "30"},
            )

        lease = claimed["lease"]
        audio_artifact = claimed["audio_artifact"]
        initial_prompt = (
            "Українська університетська лекція. "
            f"Дисципліна: {claimed['lesson_title']}. "
            f"Контекст: {claimed['scope_label']}."
        )[:512]
        return CommunityJobLease.model_validate(
            {
                "protocol_version": 1,
                "job_id": claimed["id"],
                "lease_token": lease_token,
                "lease_expires_at": lease["lease_expires_at"],
                "media_path": f"{COMMUNITY_API_PREFIX}/jobs/{claimed['id']}/media",
                "media": {
                    "size_bytes": audio_artifact["size_bytes"],
                    "content_type": audio_artifact["content_type"],
                    "sha256": audio_artifact["sha256"],
                },
                "transcription": {
                    "model": "large-v3",
                    "language": "uk",
                    "beam_size": 5,
                    "vad_filter": True,
                    "word_timestamps": True,
                    "initial_prompt": initial_prompt,
                },
            }
        )

    @application.get(f"{COMMUNITY_API_PREFIX}/jobs/{{recording_id}}/media")
    def download_community_job_media(
        recording_id: str,
        worker_record: CommunityWorkerDependency,
        lease_token: Annotated[str | None, Header(alias=COMMUNITY_LEASE_HEADER)] = None,
    ) -> FileResponse:
        normalized_id, claim_id, lease_token_hash = remote_lease_identity(
            recording_id,
            str(worker_record["id"]),
            lease_token,
        )
        if (
            service_repository.validate_remote_lease(
                recording_id=normalized_id,
                worker_id=str(worker_record["id"]),
                claim_id=claim_id,
                lease_token_hash=lease_token_hash,
            )
            is None
        ):
            raise HTTPException(status_code=409, detail="Transcription lease is no longer valid")

        audio_artifact = service_repository.get_audio_artifact(normalized_id)
        if audio_artifact is None or audio_artifact["state"] != "ready":
            raise HTTPException(status_code=409, detail="Prepared audio is no longer available")
        try:
            media_path = _resolve_prepared_audio_path(
                service_settings.prepared_audio_dir,
                audio_artifact["stored_filename"],
                normalized_id,
            )
            if media_path.stat().st_size != audio_artifact["size_bytes"]:
                raise OSError("Prepared audio size no longer matches its descriptor")
        except (HTTPException, OSError) as exc:
            invalidated = service_repository.release_remote(
                recording_id=normalized_id,
                worker_id=str(worker_record["id"]),
                claim_id=claim_id,
                lease_token_hash=lease_token_hash,
                error="invalid_media: prepared audio is missing or has the wrong size",
                retryable=True,
                max_attempts=service_settings.community_max_attempts,
                count_attempt=False,
                invalidate_audio=True,
            )
            if invalidated is not None:
                audio_preparer.notify()
            raise HTTPException(status_code=409, detail="Prepared audio is no longer available") from exc
        return FileResponse(
            media_path,
            media_type=PREPARED_CONTENT_TYPE,
            filename=f"{normalized_id}.ogg",
            content_disposition_type="attachment",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "ETag": f'"{audio_artifact["sha256"]}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @application.post(f"{COMMUNITY_API_PREFIX}/jobs/{{recording_id}}/heartbeat")
    def heartbeat_community_job(
        recording_id: str,
        heartbeat: CommunityJobHeartbeat,
        response: Response,
        worker_record: CommunityWorkerDependency,
        lease_token: Annotated[str | None, Header(alias=COMMUNITY_LEASE_HEADER)] = None,
    ) -> dict[str, object]:
        del heartbeat
        normalized_id, claim_id, lease_token_hash = remote_lease_identity(
            recording_id,
            str(worker_record["id"]),
            lease_token,
        )
        renewed = service_repository.heartbeat_remote(
            recording_id=normalized_id,
            worker_id=str(worker_record["id"]),
            claim_id=claim_id,
            lease_token_hash=lease_token_hash,
            lease_seconds=service_settings.community_lease_seconds,
        )
        if renewed is None:
            raise HTTPException(status_code=409, detail="Transcription lease is no longer valid")
        response.headers["Cache-Control"] = "no-store"
        return {"lease_expires_at": renewed["lease_expires_at"]}

    @application.put(f"{COMMUNITY_API_PREFIX}/jobs/{{recording_id}}/result")
    def complete_community_job(
        recording_id: str,
        result: CommunityJobResult,
        request: Request,
        response: Response,
        worker_record: CommunityWorkerDependency,
        lease_token: Annotated[str | None, Header(alias=COMMUNITY_LEASE_HEADER)] = None,
    ) -> dict[str, str]:
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            raise HTTPException(
                status_code=415,
                detail="Compressed transcription results are not supported",
            )
        normalized_id, claim_id, lease_token_hash = remote_lease_identity(
            recording_id,
            str(worker_record["id"]),
            lease_token,
        )
        payload = TranscriptionPayload.model_validate(
            {
                "transcript": result.transcript,
                "duration_seconds": result.duration_seconds,
                "detected_language": result.detected_language,
                "language_probability": result.language_probability,
            }
        )
        completion = service_repository.complete_remote(
            recording_id=normalized_id,
            worker_id=str(worker_record["id"]),
            claim_id=claim_id,
            lease_token_hash=lease_token_hash,
            result_id=str(result.submission_id),
            payload=payload,
            engine=result.engine,
            model=result.model,
            engine_version=result.engine_version,
            client_version=result.client_version,
        )
        if completion == "lease_lost":
            raise HTTPException(status_code=409, detail="Transcription lease is no longer valid")
        if completion == "conflict":
            raise HTTPException(status_code=409, detail="A different result was already accepted")
        response.headers["Cache-Control"] = "no-store"
        return {"status": "accepted", "disposition": completion}

    @application.post(
        f"{COMMUNITY_API_PREFIX}/jobs/{{recording_id}}/release",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def release_community_job(
        recording_id: str,
        release: CommunityJobRelease,
        response: Response,
        worker_record: CommunityWorkerDependency,
        lease_token: Annotated[str | None, Header(alias=COMMUNITY_LEASE_HEADER)] = None,
    ) -> None:
        normalized_id, claim_id, lease_token_hash = remote_lease_identity(
            recording_id,
            str(worker_record["id"]),
            lease_token,
        )
        if (
            service_repository.validate_remote_lease(
                recording_id=normalized_id,
                worker_id=str(worker_record["id"]),
                claim_id=claim_id,
                lease_token_hash=lease_token_hash,
            )
            is None
        ):
            raise HTTPException(status_code=409, detail="Transcription lease is no longer valid")
        reason = f"{release.code}: {release.message or 'no details'}"
        benign_release = release.code in {"outside_schedule", "shutdown", "cancelled"}
        invalidate_audio = False
        prepared_path: Path | None = None
        if release.code == "invalid_media":
            artifact = service_repository.get_audio_artifact(normalized_id)
            if artifact is not None and artifact["state"] == "ready":
                try:
                    prepared_path = _resolve_prepared_audio_path(
                        service_settings.prepared_audio_dir,
                        artifact["stored_filename"],
                        normalized_id,
                    )
                    invalidate_audio = not _prepared_audio_matches_descriptor(prepared_path, artifact)
                except HTTPException:
                    invalidate_audio = True
        released = service_repository.release_remote(
            recording_id=normalized_id,
            worker_id=str(worker_record["id"]),
            claim_id=claim_id,
            lease_token_hash=lease_token_hash,
            error=reason,
            retryable=True,
            max_attempts=(2**31 - 1 if benign_release else service_settings.community_max_attempts),
            count_attempt=not benign_release and not invalidate_audio,
            invalidate_audio=invalidate_audio,
        )
        if released is None:
            raise HTTPException(status_code=409, detail="Transcription lease is no longer valid")
        if invalidate_audio:
            # The preparer removes the old canonical file only after it owns the pending DB job.
            # Unlinking here could race with a fast regeneration and delete the new artifact.
            audio_preparer.notify()
        response.headers["Cache-Control"] = "no-store"

    @application.delete(
        "/admin/recordings/{recording_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=[Depends(require_admin)],
    )
    def delete_admin_recording(recording_id: str) -> None:
        with deletion_lock:
            _delete_admin_recording(recording_id)

    def _delete_admin_recording(recording_id: str) -> None:
        recording = _get_recording_or_404(service_repository, recording_id)
        if recording["status"] not in ("ready", "failed"):
            raise HTTPException(
                status_code=409,
                detail="Only completed or failed recordings can be deleted",
            )

        media_path = _resolve_media_deletion_path(service_settings.media_dir, str(recording["stored_filename"]))
        deletion_targets = [(media_path, service_settings.media_dir)]
        prepared_path = _resolve_media_deletion_path(
            service_settings.prepared_audio_dir,
            f"{recording['id']}.ogg",
        )
        deletion_targets.append((prepared_path, service_settings.prepared_audio_dir))

        staged_files: list[tuple[Path, Path, Path]] = []

        def restore_staged_files() -> None:
            for original_path, staged_path, storage_dir in reversed(staged_files):
                try:
                    os.replace(staged_path, original_path)
                    _sync_directory(storage_dir)
                except OSError:
                    logger.critical(
                        "Could not restore recording artifact %s after rejected deletion",
                        staged_path,
                        exc_info=True,
                    )

        for original_path, storage_dir in deletion_targets:
            if not original_path.exists():
                continue
            staged_path = storage_dir / f".{recording['id']}.{uuid4().hex}.deleting"
            try:
                os.replace(original_path, staged_path)
                staged_files.append((original_path, staged_path, storage_dir))
                _sync_directory(storage_dir)
            except FileNotFoundError:
                continue
            except OSError as exc:
                restore_staged_files()
                raise HTTPException(status_code=409, detail="Recording media is currently unavailable") from exc

        try:
            deletion_result = service_repository.delete_finished_recording(str(recording["id"]))
        except Exception:
            restore_staged_files()
            raise

        if deletion_result != "deleted":
            restore_staged_files()
            if deletion_result == "not_found":
                raise HTTPException(status_code=404, detail="Recording not found")
            raise HTTPException(
                status_code=409,
                detail="Only completed or failed recordings can be deleted",
            )

        for _, staged_path, storage_dir in staged_files:
            try:
                staged_path.unlink(missing_ok=True)
                _sync_directory(storage_dir)
            except OSError:
                logger.exception("Could not remove staged recording artifact %s", staged_path)

    @application.get("/recordings", response_model=list[RecordingSummary])
    def list_recordings(
        request: Request,
        lesson_key: Annotated[str | None, Query(min_length=1, max_length=MAX_LESSON_KEY_LENGTH)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIST_PAGE_SIZE)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, object]]:
        normalized_key = _clean_text(lesson_key, "lesson_key") if lesson_key is not None else None
        recordings = service_repository.list(lesson_key=normalized_key, limit=limit, offset=offset)
        media_prefix = _media_prefix(request)
        return [_public_recording(recording, media_prefix=media_prefix) for recording in recordings]

    @application.get("/recordings/search", response_model=RecordingSearchResponse)
    def search_recordings(
        request: Request,
        group_id: Annotated[str, Query(min_length=1, max_length=128)],
        q: Annotated[str, Query(max_length=MAX_SEARCH_QUERY_LENGTH)] = "",
        scope: RecordingSearchScope = "all",
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        result = service_repository.search_recordings(
            group_id=_scope_id(group_id),
            query=_search_query(q),
            scope=scope,
            limit=limit,
            offset=offset,
        )
        media_prefix = _media_prefix(request)
        return {
            **result,
            "items": [_public_recording(recording, media_prefix=media_prefix) for recording in result["items"]],
        }

    @application.get("/attachments", response_model=list[AttachmentSummary])
    def list_attachments(
        request: Request,
        lesson_key: Annotated[str | None, Query(min_length=1, max_length=MAX_LESSON_KEY_LENGTH)] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_LIST_PAGE_SIZE)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[dict[str, object]]:
        normalized_key = _clean_text(lesson_key, "lesson_key") if lesson_key is not None else None
        attachments = service_repository.list_attachments(
            lesson_key=normalized_key,
            limit=limit,
            offset=offset,
        )
        media_prefix = _media_prefix(request)
        return [_public_attachment(attachment, media_prefix=media_prefix) for attachment in attachments]

    @application.get("/attachments/search", response_model=AttachmentSearchResponse)
    def search_attachments(
        request: Request,
        group_id: Annotated[str, Query(min_length=1, max_length=128)],
        q: Annotated[str, Query(max_length=MAX_SEARCH_QUERY_LENGTH)] = "",
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, object]:
        result = service_repository.search_attachments(
            group_id=_scope_id(group_id),
            query=_search_query(q),
            limit=limit,
            offset=offset,
        )
        media_prefix = _media_prefix(request)
        return {
            **result,
            "items": [_public_attachment(attachment, media_prefix=media_prefix) for attachment in result["items"]],
        }

    @application.post(
        "/attachments",
        response_model=AttachmentSummary,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_attachment(
        request: Request,
        file: Annotated[UploadFile, File()],
        lesson_key: Annotated[str, Form(min_length=1, max_length=MAX_LESSON_KEY_LENGTH)],
        lesson_title: Annotated[str, Form()],
        scope_label: Annotated[str, Form()],
        recorded_at: Annotated[str | None, Form()] = None,
        context_json: Annotated[str | None, Form(max_length=MAX_UPLOAD_CONTEXT_LENGTH)] = None,
    ) -> dict[str, object]:
        normalized_key = _clean_text(lesson_key, "lesson_key")
        normalized_title = _clean_text(lesson_title, "lesson_title")
        normalized_scope = _clean_text(scope_label, "scope_label")
        normalized_recorded_at = _recorded_at(recorded_at)
        upload_context = _upload_context(context_json)
        original_filename = _filename_leaf(file.filename)
        attachment_id = str(uuid4())
        stored_filename = attachment_id
        temporary_path = service_settings.temp_dir / f".{attachment_id}.uploading"
        final_path = service_settings.attachments_dir / stored_filename
        size_bytes = 0
        completed = False

        try:
            with temporary_path.open("xb") as output:
                while chunk := await file.read(service_settings.upload_chunk_bytes):
                    size_bytes += len(chunk)
                    if size_bytes > service_settings.max_attachment_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail="Uploaded attachment exceeds the configured size limit",
                        )
                    output.write(chunk)

                if size_bytes == 0:
                    raise HTTPException(status_code=422, detail="Uploaded attachment file is empty")

                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, final_path)
            _sync_directory(service_settings.temp_dir)
            _sync_directory(service_settings.attachments_dir)
            attachment = service_repository.create_attachment(
                attachment_id=attachment_id,
                lesson_key=normalized_key,
                lesson_title=normalized_title,
                scope_label=normalized_scope,
                original_filename=original_filename,
                stored_filename=stored_filename,
                content_type=_attachment_content_type(file, original_filename),
                size_bytes=size_bytes,
                recorded_at=normalized_recorded_at,
                groups=[(group.id, group.label) for group in upload_context.groups],
                lecturer_name=upload_context.lecturer_name,
                lesson_keys=upload_context.lesson_keys,
            )
            completed = True
        finally:
            if not completed:
                temporary_path.unlink(missing_ok=True)
                final_path.unlink(missing_ok=True)
            try:
                await file.close()
            except Exception:
                logger.warning(
                    "Could not close uploaded attachment %s",
                    original_filename,
                    exc_info=True,
                )

        return _public_attachment(attachment, media_prefix=_media_prefix(request))

    @application.get("/attachments/{attachment_id}/content")
    def download_attachment(attachment_id: str) -> FileResponse:
        attachment = _get_attachment_or_404(service_repository, attachment_id)
        content_path = _resolve_attachment_path(
            service_settings.attachments_dir,
            str(attachment["stored_filename"]),
            str(attachment["id"]),
        )
        return FileResponse(
            content_path,
            media_type=str(attachment["content_type"]),
            filename=str(attachment["original_filename"]),
            content_disposition_type="attachment",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cross-Origin-Resource-Policy": "same-origin",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @application.get(
        "/schedule-snapshots/{scope_type}/{scope_id}",
        response_model=list[ScheduleSnapshotMetadata],
    )
    def list_schedule_snapshots(
        scope_type: ScheduleScopeType,
        scope_id: str,
        from_week: Annotated[date | None, Query()] = None,
        to_week: Annotated[date | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=520)] = 260,
    ) -> list[dict[str, object]]:
        normalized_scope_id = _scope_id(scope_id)
        normalized_from = _calendar_week_start(from_week, "from_week") if from_week is not None else None
        normalized_to = _calendar_week_start(to_week, "to_week") if to_week is not None else None
        if normalized_from is not None and normalized_to is not None and normalized_from > normalized_to:
            raise HTTPException(status_code=422, detail="from_week must not be later than to_week")
        return service_repository.list_schedule_snapshots(
            scope_type=scope_type,
            scope_id=normalized_scope_id,
            from_week=normalized_from,
            to_week=normalized_to,
            limit=limit,
        )

    @application.get(
        "/schedule-snapshots/{scope_type}/{scope_id}/{week_start}",
        response_model=ScheduleSnapshot,
    )
    def get_schedule_snapshot(
        scope_type: ScheduleScopeType,
        scope_id: str,
        week_start: date,
    ) -> dict[str, object]:
        snapshot = service_repository.get_schedule_snapshot(
            scope_type=scope_type,
            scope_id=_scope_id(scope_id),
            week_start=_calendar_week_start(week_start),
        )
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Schedule snapshot not found")
        return snapshot

    @application.put(
        "/schedule-snapshots/{scope_type}/{scope_id}/{week_start}",
        response_model=ScheduleSnapshot,
        status_code=status.HTTP_201_CREATED,
    )
    def put_schedule_snapshot(
        scope_type: ScheduleScopeType,
        scope_id: str,
        week_start: date,
        snapshot: ScheduleSnapshotWrite,
        response: Response,
    ) -> dict[str, object]:
        normalized_week_start = _calendar_week_start(week_start)
        normalized_schedule = snapshot.schedule.model_dump(mode="json", by_alias=True)
        try:
            archived, created = service_repository.put_schedule_snapshot(
                scope_type=scope_type,
                scope_id=_scope_id(scope_id),
                scope_label=snapshot.scope_label,
                week_start=normalized_week_start,
                week_end=(week_start + timedelta(days=6)).isoformat(),
                schedule=normalized_schedule,
            )
        except ScheduleSnapshotConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail="This calendar week is already archived with different schedule data",
            ) from exc
        except ScheduleSnapshotTooLargeError as exc:
            raise HTTPException(status_code=413, detail="Schedule snapshot exceeds the 2 MiB limit") from exc

        if not created:
            response.status_code = status.HTTP_200_OK
        return archived

    @application.post(
        "/recordings",
        response_model=RecordingSummary,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_recording(
        request: Request,
        file: Annotated[UploadFile, File()],
        lesson_key: Annotated[str, Form(min_length=1, max_length=MAX_LESSON_KEY_LENGTH)],
        lesson_title: Annotated[str, Form()],
        scope_label: Annotated[str, Form()],
        recorded_at: Annotated[str | None, Form()] = None,
        context_json: Annotated[str | None, Form(max_length=MAX_UPLOAD_CONTEXT_LENGTH)] = None,
    ) -> dict[str, object]:
        normalized_key = _clean_text(lesson_key, "lesson_key")
        normalized_title = _clean_text(lesson_title, "lesson_title")
        normalized_scope = _clean_text(scope_label, "scope_label")
        normalized_recorded_at = _recorded_at(recorded_at)
        upload_context = _upload_context(context_json)
        original_filename, extension = _safe_original_filename(file.filename)
        recording_id = str(uuid4())
        stored_filename = f"{recording_id}{extension}"
        temporary_path = service_settings.temp_dir / f".{recording_id}.uploading"
        final_path = service_settings.media_dir / stored_filename
        size_bytes = 0
        completed = False

        try:
            with temporary_path.open("xb") as output:
                while chunk := await file.read(service_settings.upload_chunk_bytes):
                    size_bytes += len(chunk)
                    if size_bytes > service_settings.max_upload_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail="Uploaded media exceeds the configured size limit",
                        )
                    output.write(chunk)

                if size_bytes == 0:
                    raise HTTPException(status_code=422, detail="Uploaded media file is empty")

                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, final_path)
            _sync_directory(service_settings.temp_dir)
            _sync_directory(service_settings.media_dir)
            recording = service_repository.create(
                recording_id=recording_id,
                lesson_key=normalized_key,
                lesson_title=normalized_title,
                scope_label=normalized_scope,
                original_filename=original_filename,
                stored_filename=stored_filename,
                content_type=_content_type(file, original_filename),
                size_bytes=size_bytes,
                recorded_at=normalized_recorded_at,
                groups=[(group.id, group.label) for group in upload_context.groups],
                lecturer_name=upload_context.lecturer_name,
                lesson_keys=upload_context.lesson_keys,
            )
            completed = True
        finally:
            if not completed:
                temporary_path.unlink(missing_ok=True)
                final_path.unlink(missing_ok=True)
            try:
                await file.close()
            except Exception:
                logger.warning("Could not close uploaded file %s", original_filename, exc_info=True)

        audio_preparer.notify()
        worker.notify()
        return _public_recording(recording, media_prefix=_media_prefix(request))

    @application.get("/recordings/{recording_id}", response_model=RecordingDetail)
    def get_recording(request: Request, recording_id: str) -> dict[str, object]:
        return _public_recording(
            _get_recording_or_404(service_repository, recording_id),
            media_prefix=_media_prefix(request),
        )

    @application.post("/recordings/{recording_id}/retry", response_model=RecordingSummary)
    def retry_recording(request: Request, recording_id: str) -> dict[str, object]:
        recording = _get_recording_or_404(service_repository, recording_id)
        if recording["status"] != "failed":
            raise HTTPException(status_code=409, detail="Only failed recordings can be retried")
        retried = service_repository.retry(str(recording["id"]))
        if retried is None:
            raise HTTPException(status_code=409, detail="Recording status changed; reload and try again")
        audio_preparer.notify()
        worker.notify()
        return _public_recording(retried, media_prefix=_media_prefix(request))

    @application.get("/recordings/{recording_id}/media")
    def stream_media(recording_id: str) -> FileResponse:
        recording = _get_recording_or_404(service_repository, recording_id)
        media_path = _resolve_media_path(service_settings.media_dir, str(recording["stored_filename"]))
        return FileResponse(
            media_path,
            media_type=str(recording["content_type"]),
            filename=str(recording["original_filename"]),
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @application.get("/recordings/{recording_id}/download")
    def download_media(recording_id: str) -> FileResponse:
        recording = _get_recording_or_404(service_repository, recording_id)
        media_path = _resolve_media_path(service_settings.media_dir, str(recording["stored_filename"]))
        return FileResponse(
            media_path,
            media_type=str(recording["content_type"]),
            filename=str(recording["original_filename"]),
            content_disposition_type="attachment",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cross-Origin-Resource-Policy": "same-origin",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return application


app = create_app()
