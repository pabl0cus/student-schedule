# Recording, archive and community transcription server

FastAPI service for lecture recordings, generic attachments, immutable schedule snapshots and the durable SQLite
transcription queue. The default server is CPU-only: a single background `ffmpeg` preparer strips video,
metadata and chapters into mono 16 kHz Ogg/Opus, then invited Windows clients run `faster-whisper` large-v3 and
return bounded JSON with Ukrainian timestamps. The original upload remains separate for playback and download.

## Run directly

Python 3.10+ plus `ffmpeg`/`ffprobe` on `PATH` are sufficient for the community server:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8012
```

Use the platform-specific virtual-environment paths on Windows. Run commands from `transcription_service/` and
configure the service with the variables documented in `.env.example`.

The Docker image is intentionally based on the regular Python runtime and does not require CUDA:

```bash
docker build -t schedule-transcription-server .
docker run --rm -p 127.0.0.1:8011:8011 -v schedule-transcriptions:/data schedule-transcription-server
```

## Administrator and worker tokens

Administrator access has no built-in credentials. Set all three values or the admin API remains locked:

```dotenv
TRANSCRIPTION_ADMIN_USERNAME=your-admin-username
TRANSCRIPTION_ADMIN_PASSWORD_HASH='pbkdf2_sha256$600000$BASE64URL_SALT$BASE64URL_DIGEST'
TRANSCRIPTION_ADMIN_SESSION_SECRET=your-long-random-urlsafe-token
```

Generate a password record and an independent session secret inside the deployment environment:

```bash
python -c "from getpass import getpass; from app.auth import hash_password; print(hash_password(getpass('Admin password: ')))"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Sessions are HMAC-SHA256 signed, expire after eight hours, and use an HttpOnly, SameSite=Strict cookie limited to
`/admin` or `/recordings-api/admin` behind the application proxy. Set `TRANSCRIPTION_SECURE_COOKIES=true` behind
HTTPS.

Community-worker authentication uses another independent 32+ byte secret:

```dotenv
TRANSCRIPTION_WORKER_TOKEN_PEPPER=generate-an-independent-random-value
```

For a local checkout, `python scripts/ensure_worker_token_pepper.py` creates this value atomically in the ignored
root `.env` without printing the secret. Existing valid values are left unchanged.

An authenticated administrator creates one token per trusted Windows computer through
`POST /admin/community-workers`. The plaintext token is returned once. SQLite stores only its HMAC-SHA256 digest;
listing workers never returns the secret, and revocation immediately invalidates the token and requeues its active
job. There are no default tokens.

## Community job lifecycle

The native client uses outbound HTTPS only; the server never connects to a volunteer PC:

1. The background preparer durably creates mono Ogg/Opus (about 32 kbit/s) using an atomic temporary-file rename.
2. `POST /community/v1/jobs/claim` leases the oldest queued recording whose derived audio is ready.
3. `GET /community/v1/jobs/{id}/media` streams only that leased audio and requires both bearer and lease tokens.
   Its descriptor contains exact size, `audio/ogg` and SHA-256; the client verifies resumed downloads before use.
4. `POST /community/v1/jobs/{id}/heartbeat` extends the lease using server time.
5. `PUT /community/v1/jobs/{id}/result` validates and atomically stores the timestamped transcript.
6. `POST /community/v1/jobs/{id}/release` returns interrupted work to the queue or fails it after the configured
   attempt limit.

Only one active lease is allowed per worker token. Expired leases and leases belonging to revoked workers are
recovered without stealing valid work after a server restart. Result submission is idempotent: repeating the same
submission ID and normalized payload succeeds, while a changed or stale result returns `409`.

Preparation is asynchronous, so upload requests never wait for transcoding. Crashed preparation claims return to
their own queue; completed files are fsynced and atomically published. Failed preparation can be retried through
the existing recording retry route. `ffmpeg` is invoked as a fixed argv without a shell, network/playlist formats
are excluded, and the Docker image runs it as the existing unprivileged service user.

The result route has a separate 8 MiB default body limit (`TRANSCRIPTION_MAX_WORKER_RESULT_BYTES`). Schemas also
bound segment and word counts, text lengths, timestamps and probabilities. Compressed result bodies are rejected.

## Optional legacy local Whisper worker

The former local worker and `FasterWhisperTranscriber` remain available for private installations, but are disabled
by default and omitted from the CPU Docker image. Install the optional dependency set and opt in explicitly:

```bash
pip install -r requirements-local-worker.txt
```

```dotenv
TRANSCRIPTION_LOCAL_WORKER_ENABLED=true
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
```

Do not run the local mode unless its process has access to the configured GPU runtime. Local and remote claims use
the same atomic queue, and local recovery does not invalidate a live remote lease.

## Other API routes

- `POST /recordings` — multipart recording upload with lesson metadata and optional `recorded_at`.
- `GET /recordings?lesson_key=...` — recording summaries for a schedule occurrence.
- `GET /recordings/{id}` — recording state and transcript.
- `GET /recordings/{id}/media` — inline media with byte-range support.
- `GET /recordings/{id}/download` — forced original-media download.
- `POST /recordings/{id}/retry` — return a failed recording to the queue.
- `POST /attachments` and `GET /attachments` — generic lecture attachments.
- `GET /attachments/{id}/content` — safe attachment download.
- `PUT /schedule-snapshots/{scope_type}/{scope_id}/{week_start}` — insert an immutable calendar week.
- `GET /schedule-snapshots/...` — read archived weeks and metadata.
- `GET`, `POST`, `DELETE /admin/session` — administrator session lifecycle.
- `GET /admin/recordings`, `DELETE /admin/recordings/{id}` — administrator recording management.
- `GET`, `POST /admin/community-workers`, `DELETE /admin/community-workers/{id}` — worker-token management.
- `GET /health` — database, optional local worker and queue state.

Recording uploads are limited to 500 MiB and attachments to 100 MiB by default. Limits are enforced while
streaming even when `Content-Length` is absent or false. Browser mutations require the configured origin plus
`X-KPI-Local-Request: 1`. Only `/community/v1/` bypasses that browser guard, and every route in that prefix is
protected by a worker bearer token; job-specific routes additionally require `X-Transcription-Lease`.

## Deployment boundary

The provided Compose file binds nginx to localhost. A public installation still needs a trusted HTTPS reverse
proxy, exact allowed hosts/origins, secure cookies, upload authentication or quotas, per-token/IP rate limits, disk
monitoring, backups and a retention policy. Do not log `Authorization`, lease headers, result bodies or media.

A worker token proves which invited device submitted a result; it cannot prove that the operator actually ran
large-v3 or did not retain the derived audio. Community processing therefore requires explicit disclosure and trusted
participants. A fully open network would additionally need reputation, audit samples or redundant transcription.

## Tests

Tests inject fake transcribers and do not download Whisper or require a GPU:

```bash
pip install -r requirements-dev.txt
pytest
```
