# backend

FastAPI backend for the AI Work Planner.

## Status

Foundation + auth + task CRUD + Gemini prioritization + Google Calendar (read + incremental-OAuth write + two-way sync) + timezone-aware scheduling proposals + apply-to-calendar. The mobile Flutter app (see `/mobile/README.md`) is the primary client this API serves. Endpoints: `GET /health`; `GET /me`, `GET /tenants/me`, `GET /tenants/{tenant_id}`; `POST/GET /tasks`, `GET /tasks/prioritized`, `GET/PATCH/DELETE /tasks/{id}`, `POST /tasks/{id}/complete`; `POST /tasks/{id}/prioritize`, `GET /tasks/{id}/ai-result`; `GET /calendar/connection`, `GET /calendar/connect` (`?scope=read|write`), `GET /calendar/callback`, `DELETE /calendar/connection`, `GET /calendar/calendars`, `GET /calendar/events`, `GET /calendar/availability`, `GET /calendar/external-events`, `POST /calendar/sync`, `POST /calendar/webhook`; `POST /tasks/schedule` (propose, writes nothing), `POST /tasks/schedule/apply` (revalidates and creates Google Calendar events, idempotent, partial-failure-safe), `GET /tasks/schedule/items` (applied schedule items in a range, joined with task/priority/Calendar status), `GET /tasks/schedule/needs-attention`.

## Prerequisites

- Python 3.11+

## Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate # macOS/Linux
pip install -r requirements-dev.txt
copy .env.example .env      # Windows
# cp .env.example .env      # macOS/Linux, then fill in real values, never commit .env
```

## Run

```bash
uvicorn app.main:app --reload
```

Visit http://localhost:8000/health and http://localhost:8000/docs.

## Test / Lint

```bash
pytest
ruff check .
```

Hermetic (no network): `test_task_schemas.py`, `test_ai_schemas.py`, `test_crypto.py`, `test_oauth_state.py`, `test_calendar_normalization.py`, `test_scheduling.py` (includes timezone/DST cases). Live against a real Supabase project (Google/Gemini calls mocked): `test_tasks.py`, `test_prioritize.py`, `test_calendar.py`, `test_schedule_api.py`, `test_schedule_apply.py`, `test_calendar_sync.py`, `test_prioritized_tasks.py`, `test_schedule_items.py` — skipped as a whole (not failed) unless `TEST_DEMO_USER_A_EMAIL`/`_PASSWORD` and `TEST_DEMO_USER_B_EMAIL`/`_PASSWORD` are set in `.env` for two pre-existing demo users (see `.env.example` and `database/seeds/seed.sql`). Real external API calls, each opt-in and skipped without credentials: `test_prioritize_live_gemini.py` (needs `GEMINI_API_KEY`), `test_calendar_live_google.py` (needs Google OAuth credentials), `test_schedule_apply_live_google.py` (needs `TEST_LIVE_CALENDAR_REFRESH_TOKEN`, a manually-obtained write-scope refresh token — see the test file's docstring; it creates one real Calendar event 300 days out and deletes it afterward). There is no live push-notification test (needs a public HTTPS webhook URL a real Google server can call, e.g. via `ngrok` in dev) — `test_calendar_sync.py` exercises watch channels and the webhook endpoint entirely against a mocked Google API.

## Conventions

- `app/api/` — route handlers, grouped by resource. `app/api/deps.py` holds shared auth/tenant dependencies (`get_current_user`, `require_tenant_membership`).
- `app/core/` — config, shared setup (CORS, settings, JWT verification in `security.py`, the DB pool in `db.py`, token encryption in `crypto.py`, signed OAuth state in `oauth_state.py`).
- `app/models/` — database row/table models
- `app/schemas/` — Pydantic request/response schemas
- `app/services/` — business logic, kept separate from route handlers
- Configuration is read via `app/core/config.py` (pydantic-settings) from environment variables, never hardcoded.

## Auth

Every protected route depends on `get_current_user`, which verifies the `Authorization: Bearer <token>` header against Supabase's JWKS endpoint — a request with no token, an expired token, or a signature that doesn't verify is rejected before any route code runs. Routes scoped to a specific `tenant_id` in the path should depend on `require_tenant_membership`; flat routes like `/tasks` (no tenant in the path) should depend on `get_tenant_context` instead — both re-check membership in Postgres using the verified user id, so a client can send any tenant id and only a real `tenant_members` row makes the request succeed. See `app/api/me.py` / `app/api/tasks.py` for example usage and `/docs/architecture.md` for the full flow.

## Task API

See `/docs/architecture.md` § Task API for the endpoint table and field list. Business logic for tasks lives in `app/services/tasks.py` (a small repository over `public.tasks`, always scoped by a verified `tenant_id`); route handlers in `app/api/tasks.py` stay thin.

## Gemini / AI prioritization

`POST /tasks/{id}/prioritize` is the **only** route that calls Gemini, and only on an explicit request — never from task creation or any read path. `GET /tasks/{id}/ai-result` returns the latest stored result without calling Gemini. See `/docs/architecture.md` § Gemini integration for the model/SDK, structured output schema, scoring/bounds logic, and cost strategy.

Requires `GEMINI_API_KEY` (get one from [Google AI Studio](https://aistudio.google.com/apikey), free tier) in `.env`; `GEMINI_MODEL` defaults to `gemini-3.1-flash-lite` and can be overridden. Without a key configured, `/prioritize` returns a `502` rather than crashing.

## Google Calendar

Starts read-only (`GET /calendar/connect?scope=read`, the default); `GET /calendar/connect?scope=write` requests the additional `calendar.events` scope via incremental authorization, preserving whatever scopes the connection already has — no disconnect/reconnect required. Only `POST /tasks/schedule/apply` ever creates events, and only for the current tenant's own tasks; it never modifies or deletes a user's existing Calendar events. See `/docs/architecture.md` § Google Calendar integration and its "Apply Schedule" section for the full OAuth flow, required Google Cloud Console setup, scopes, and token security approach.

Requires, in `.env`: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI` (must exactly match a redirect URI registered on the Cloud Console OAuth client), `OAUTH_STATE_SECRET` and `TOKEN_ENCRYPTION_KEY` (generation commands in `.env.example`). Apply `database/migrations/0002_calendar_tokens.sql`, `database/migrations/0003_calendar_write_scope.sql`, and `database/migrations/0004_calendar_sync.sql` before using any `/calendar/*` route.

## Scheduling

`POST /tasks/schedule` proposes when tasks should happen, combining Gemini priority (Phase 3) with Google Calendar availability (Phase 4) via a deterministic engine (`app/services/scheduling.py`) — no Gemini or Google calls happen inside the algorithm itself, and it never writes to the database or to Google Calendar. Working hours are interpreted in the connected calendar's own timezone (via `zoneinfo`/`tzdata`), not assumed to be UTC. See `/docs/architecture.md` § Scheduling engine for the algorithm, scoring model, and a verified worked example.

## Apply Schedule

`POST /tasks/schedule/apply` (`app/services/schedule_apply.py`) takes the user-approved proposal (task id + start + end per item, exactly as reviewed) and independently revalidates each item — task still exists and belongs to the tenant, end after start, end at or before the task's due date, no overlap with a freshly-queried Google Calendar `freebusy` window or with another item in the same batch — before creating anything. Each Google Calendar event carries the task title, a generated description (task description + AI category/priority/urgency if available), the correct start/end/timezone, and `extendedProperties.private` metadata (`app`, `task_id`, `tenant_id`, `schedule_item_id`) identifying it as created by this app. A `google_calendar_event_mappings` row is written only after the Google API call succeeds — there's no "failed" placeholder row, so a mapping's mere existence means "already synced," which makes re-submitting the same item idempotent (reported as `already_applied`, no duplicate event) without needing extra state. A batch of items reports per-item outcomes plus `created`/`already_applied`/`failed` counts, so a partial failure is never reported as a full success, and a failed item can be safely retried. See `/docs/architecture.md`'s "Apply Schedule" section and ADR-018 through ADR-021 in `/docs/decisions.md` for the full design.

## Two-way Calendar synchronization

`app/services/calendar_sync.py` keeps the app's database in sync with Google Calendar in the other direction. `POST /calendar/webhook` is Google's push-notification callback (no user JWT — trusted via a channel id/resource id/token match against what was stored when the channel was registered); it triggers a background incremental sync using Google's `syncToken` mechanism (`app/services/google_calendar.list_events_page`), recovering from an invalidated token (`410 Gone`) by clearing it and doing one bounded full resync. An app-created event that gets moved externally updates its `schedule_items` row; one that gets deleted sets `needs_attention`/`attention_reason` instead of being silently recreated (surfaced via `GET /tasks/schedule/needs-attention`). A genuinely external event is cached in `google_calendar_external_events`, never turned into a task. Nothing in this module ever writes back to Google in response to a detected change — see `/docs/architecture.md`'s "Loop prevention" for why that, plus comparing each mapping's stored `google_updated_at`, is what keeps duplicate/replayed notifications idempotent.

`POST /calendar/sync` is both the manual reconciliation fallback (called by both clients on mount, and via a "Sync now" action — never on a timer) and how watch channels get renewed (`ensure_watch_channel()`, opportunistic, checked at the top of every call). Push notifications require `GOOGLE_CALENDAR_WEBHOOK_URL` — a real public HTTPS URL Google can reach; unset in local dev, `/calendar/sync` alone keeps everything working, just not instantly. Every table this module writes to (`schedule_items`, `google_calendar_event_mappings`, `google_calendar_external_events`, `google_calendar_connections`) is in the `supabase_realtime` publication (`database/migrations/0004_calendar_sync.sql`), so both clients get live updates with zero application-level "publish" code. See `/docs/architecture.md`'s "Two-way Calendar synchronization" section and ADR-022 through ADR-025 in `/docs/decisions.md` for the full design.
