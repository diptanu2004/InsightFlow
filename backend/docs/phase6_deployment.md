# Phase 6 deployment guide

Covers what changed operationally in Phase 6 (auth + multi-tenancy): the backend now needs a real
Postgres database and S3-compatible object storage, not just an in-memory process. This doc is
about running/deploying the backend; see `hld.md`/`class_diagram.md` for the architecture itself.

## Local development

```
docker compose up -d postgres minio minio-init
cd backend
cp .env.example .env   # fill in JWT_SECRET and GROQ_API_KEY at minimum
uv run alembic upgrade head
uv run uvicorn insightflow_backend.main:app --reload
```

`docker-compose.yml` (repo root) also has a `backend` service if you'd rather run the whole stack
in containers: `docker compose up --build`.

## Required environment variables

See `backend/.env.example` for the full list with inline explanations. The two that have no safe
default and will stop the app from starting or from working correctly:

- `JWT_SECRET` -- the app raises `RuntimeError` at startup if this is empty (see `main.py`).
  Generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and never reuse
  the local dev value in production.
- `DATABASE_URL` -- defaults to the local docker-compose Postgres; must point at your real
  database in every other environment.

## Database: Neon or Supabase

Either works as a plain Postgres connection string -- this backend doesn't use anything
provider-specific (no Supabase Auth, no Neon branching features). Steps:

1. Create a Postgres database on Neon or Supabase.
2. Copy its connection string into `DATABASE_URL`, using the `postgresql+psycopg://` scheme
   (SQLAlchemy needs the driver name in the URL; most providers give you a plain `postgresql://`
   string -- just swap the scheme prefix).
3. Run migrations against it once, from a machine/CI job with network access to it:
   ```
   DATABASE_URL=<your real url> uv run alembic upgrade head
   ```
4. Run `alembic upgrade head` again after every deploy that adds a new migration (`alembic/versions/`) --
   this is not automatic; nothing runs migrations on app startup, so a schema change ships as two
   steps (migrate, then deploy the new image), not one.

## Object storage: S3 bucket

Provision a real S3 bucket (or S3-compatible service) for production; MinIO is dev/test-only.
Set `S3_ENDPOINT_URL` to AWS's actual endpoint (or omit it / point at your provider), `S3_BUCKET`
to the bucket name, and real `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` credentials scoped to
that bucket only (least privilege -- this backend never needs bucket-level admin, only
Get/Put/Head on objects under its own prefix).

## CORS

`CORS_ALLOWED_ORIGINS` is empty by default (denies every browser origin) since no frontend exists
yet (Phase 8). Once one does, set it to that frontend's real origin(s), comma-separated:
```
CORS_ALLOWED_ORIGINS=https://app.insightflow.example.com
```
Never set this to `*` in production if you also rely on cookies/credentials -- this app uses
Bearer tokens (not cookies) for auth, so a same-site restriction isn't load-bearing for session
security, but a wildcard still needlessly widens what can call the API from a browser.

## Rate limiting

`/auth/login` and `/auth/register` are rate-limited (`AUTH_RATE_LIMIT_MAX_REQUESTS` per
`AUTH_RATE_LIMIT_WINDOW_SECONDS`), and as of Phase 7 M1 the limiter is Redis-backed
(`auth/rate_limit.py`) -- a real global cap shared across every backend instance, not a
per-process approximation. `schema/discover`, `dashboard/generate`, and `chat/ask` are also
rate-limited (`LLM_RATE_LIMIT_MAX_REQUESTS`/`LLM_RATE_LIMIT_WINDOW_SECONDS`), keyed per-project
rather than per-client since LLM spend is a tenant budget concern. See
`backend/docs/phase7_scope.md` for the full M1 design.

Requires `REDIS_URL` pointing at a reachable Redis (`docker compose up -d redis` locally); the app
itself starts fine without one (the client connects lazily), but any request that hits a
rate-limited route will fail once it actually needs Redis.

## TLS / reverse proxy

The backend itself speaks plain HTTP (uvicorn). Terminate TLS in front of it (a managed load
balancer, nginx, Caddy, or your hosting provider's built-in HTTPS) -- don't expose uvicorn
directly to the internet. No app-level change is needed for this; it's purely a deployment-layer
concern.

## Logging

Every request logs one structured JSON line (`logging_config.py`) with `request_id`,
`method`, `path`, `status_code`, `duration_ms`, and `user_id`/`org_id` when authenticated
(`logging_context.py`). The response also carries an `X-Request-ID` header matching the log line,
for correlating a client-reported issue with server-side logs. Point your log aggregator (or just
container stdout capture) at this; there's no file-based logging to configure.

## Audit log

Mutating actions (org/project/dataset creation, project deletion, membership add/role-change/
remove) write a row to the `audit_log` table in the same transaction as the mutation itself
(`audit.py`) -- there's no API route exposing it yet (query it directly in Postgres, or build a
`GET` endpoint when there's an actual consumer for it).

## Docker build

Build context must be the repo root, not `backend/` (path dependencies need the sibling POC
packages present):
```
docker build -f backend/Dockerfile -t insightflow-backend .
```
