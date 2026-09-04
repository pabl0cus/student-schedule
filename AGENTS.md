# Student Schedule — Agent Guide

This repository contains an unofficial student schedule with lecture recordings, attachments, archived calendar
weeks, and Ukrainian transcription. It is derived from `kpi-ua/schedule.kpi.ua` but is maintained independently.

## Stack

- Frontend: TypeScript, React 18, Vite 6, Tailwind CSS 4.
- State and data: Zustand, React Context, React Query v3.
- Backend: Python 3.10+, FastAPI, SQLite, bearer tokens and expiring transcription leases.
- Community client: separate Windows tray application, faster-whisper with Whisper large-v3 and NVIDIA GPU.
- Runtime: CPU-only Docker Compose server with ffmpeg/nginx; GPUs belong to invited clients.

## Layout

```text
src/
├── api/            # HTTP clients and wire-format normalization
├── queries/        # React Query hooks
├── store/          # Zustand stores
├── common/         # constants, contexts, hooks and pure utilities
├── components/     # reusable UI and recording/material features
├── containers/     # page and feature composition
├── layouts/        # route layouts
├── models/         # domain entities
└── types/          # shared structural types

transcription_service/
├── app/            # FastAPI application, persistence and worker
├── tests/          # backend test suite
└── scripts/        # optional local helpers
```

The Windows client is maintained separately at
`https://github.com/pabl0cus/student-schedule-worker-windows`.

## Commands

```bash
npm ci
npm run dev
npm run prettier
npm run lint
npm run build

cd transcription_service
pip install -r requirements-dev.txt
pytest
```

The server stack starts with `docker compose up --build`. Compose binds to localhost and does not require a GPU.
Whisper runs in the separately packaged Windows client. Run its tests and packaging workflow in the client
repository.

## Conventions

- Match the existing arrow-component style and use relative imports.
- Keep raw HTTP and snake_case-to-camelCase conversion in `src/api/`; expose it through hooks in `src/queries/`.
- Put shared transient state in Zustand and subtree-specific state in React Context.
- Compose conditional Tailwind classes with `cn()` from `src/common/utils/cn.ts`.
- Reuse constants instead of duplicating endpoint roots, upload limits, or request headers.
- Prefer small, focused changes and preserve unrelated work in a dirty worktree.
- Add or update backend tests for API, persistence, authentication, queue, or validation changes.
- Run formatting, lint, production build, backend tests, and `docker compose config --quiet` before release.

## Runtime data and secrets

Never commit `.env`, recordings, attachments, SQLite databases, model caches, virtual environments, build output,
or test caches. These are excluded by `.gitignore` and `.dockerignore`; keep those rules intact.

Administrator access must remain disabled when its username, password hash, or session secret is missing. Do not
add default credentials. The hidden frontend path is not an authorization mechanism; deletion must stay protected
by the backend session check.

The request-origin marker prevents unwanted cross-origin browser mutations but does not authenticate uploaders.
Do not describe the default Compose setup as internet-production-ready. Public deployment additionally requires
HTTPS, secure cookies, exact allowed hosts/origins, upload authorization or quotas, rate limits, disk monitoring,
backups, and a retention policy.

## Compatibility

- `VITE_ADMIN_PATH` is the preferred administrator route variable.
- `VITE_LOCAL_ADMIN_PATH` remains a compatibility fallback for existing local installations.
- Archived schedule snapshots are immutable by design; never rewrite existing weeks during synchronization.
- Recording and attachment limits must be enforced both in the browser and while streaming on the backend.
- Media responses must retain byte-range support; downloads and attachments must keep safe content-disposition and
  response headers.
- Community routes require bearer authentication; job media, heartbeat, result and release also require the current
  lease token. Never reset or overwrite a valid remote lease during startup recovery.
- Community jobs receive only the atomically prepared mono Ogg/Opus artifact with an exact size and SHA-256;
  original uploads remain separate for playback/download. Remote claims must wait for artifact state `ready`.
- The legacy local Whisper worker remains opt-in through `TRANSCRIPTION_LOCAL_WORKER_ENABLED`; do not add its large
  runtime dependencies back to the default CPU server image.

## Publishing

Do not deploy, push, create a GitHub release, or publish packages unless the user explicitly asks for that external
action. Preparing files and validating a release does not authorize publication.
