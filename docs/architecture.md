# Architecture

## System overview

The AI Work Planner is a single product surfaced through two clients (mobile, web) backed by one API and one database. There is no microservice split in the MVP — one FastAPI service owns all business logic.

```text
┌─────────────┐    ┌─────────────┐
│  Flutter    │    │  Next.js    │
│  mobile app │    │  web app    │
└──────┬──────┘    └──────┬──────┘
       │ HTTPS (REST)     │ HTTPS (REST)
       └────────┬─────────┘
                 ▼
         ┌───────────────┐
         │  FastAPI       │
         │  backend       │
         └───────┬────────┘
                 │
     ┌───────────┼─────────────────┐
     ▼           ▼                 ▼
┌─────────┐ ┌───────────┐   ┌──────────────┐
│ Supabase│ │ Gemini API │   │ Google       │
│ Postgres│ │ (AI)       │   │ Calendar API │
│ + Auth  │ │            │   │              │
└─────────┘ └───────────┘   └──────────────┘
```

Both clients also talk to Supabase directly for auth (sign-in/session) and can use Supabase's realtime channel to receive database change notifications, in addition to calling the FastAPI backend for everything else. This keeps auth and realtime sync cheap (no code we have to maintain) while all task/scheduling/AI logic lives in one backend we control.

## Component responsibilities

### mobile (Flutter)

- iOS/Android client for end users.
- Renders tasks, plans, and calendar state; captures new tasks.
- Authenticates via Supabase Auth SDK.
- Calls the FastAPI backend for anything involving business logic (task prioritization, scheduling, calendar sync triggers).
- Subscribes to Supabase realtime for live updates when the backend changes data.

### web (Next.js + TypeScript)

- Browser dashboard, same responsibilities as mobile, aimed at users who prefer a desktop experience.
- Server-rendered where useful (Next.js App Router) but is still a thin client over the same FastAPI backend — no business logic duplicated between web and mobile.

### backend (FastAPI)

- Single source of truth for business logic: task intake, calling Gemini to interpret/prioritize tasks, running the scheduling engine, and orchestrating Google Calendar writes.
- Owns all writes that require business rules; clients don't write directly to tables that require validation or AI processing.
- Talks to Postgres (via Supabase), Gemini API, and Google Calendar API.

### database (PostgreSQL via Supabase)

- Single relational store for users, tasks, plans, schedule items, and calendar sync state.
- Supabase also provides: Auth (issues JWTs the backend verifies), Realtime (change streams clients subscribe to), and Row Level Security (defense in depth alongside backend authorization).
- Schema changes are plain, ordered SQL migrations under `/database/migrations`.

### infra

- `docker-compose.yml` for local development only: spins up backend, web, and a throwaway local Postgres so the stack can run without hitting hosted Supabase during dev.
- Staging/production do not run the local Postgres container — they point at hosted Supabase.

### .github (CI/CD)

- Per-app GitHub Actions workflows (lint/build/test), scoped by path so unrelated apps don't rebuild on every change.
- Deployment automation is deferred until a hosting target is chosen (see `/docs/decisions.md`).

## Data flow (target end state)

1. User enters a task (mobile or web) → backend receives it.
2. Backend calls Gemini to interpret/prioritize the task (urgency, effort, category, dependencies).
3. Backend reads the user's deadlines and Google Calendar availability.
4. Scheduling engine (backend) produces an optimized plan (which tasks go where in the calendar).
5. Backend writes the plan to Postgres and pushes the corresponding events to Google Calendar.
6. Postgres change → Supabase Realtime → both clients update live; Google Calendar is now the source of truth for calendar apps outside this product.

None of steps 2–6 are implemented yet — this repository currently only contains the foundation each step will be built on top of.

## Tenant model

Every piece of business data (tasks, plans, schedule items, calendar connections, usage) belongs to a **tenant** (a workspace), not directly to a user. A user reaches a tenant through **tenant membership**:

```text
auth.users (Supabase Auth)
      │  1:1
      ▼
public.profiles
      │
      │  new user trigger creates:
      ▼
public.tenants  ◄──────────────  public.tenant_members  ──────────────►  public.profiles
(the workspace)      1:N                (join table,                N:1      (a member)
                                     carries `role`)
```

- On signup, a Postgres trigger on `auth.users` (`handle_new_user`, see `0001_init.sql`) automatically creates the user's `profiles` row, a personal `tenants` row (`is_personal = true`), and a `tenant_members` row with `role = 'owner'`. This happens server-side in the database itself — no client or backend code has to remember to do it, and it can't be skipped by calling the API in the wrong order.
- Every tenant-owned table carries a `tenant_id` foreign key. There is no implicit "current tenant" — every query is scoped by an explicit `tenant_id` that the backend has independently verified the caller belongs to (see Auth architecture below).
- This shape is intentionally identical for personal and team use: adding teammates to a tenant later is just inserting more `tenant_members` rows (`role = 'admin' | 'member'`) — no schema migration required. `tenants.is_personal` and the `owner`/`admin`/`member` roles exist now specifically so that future "invite a teammate" / "manager view" features are additive, not a schema rewrite.

## Auth architecture

Authentication is Supabase Auth end to end; the backend never re-implements login, and it never trusts an identity the client merely *asserts*.

```text
Client (mobile/web)                FastAPI backend                 Supabase
  │                                      │                              │
  │ 1. signUp / signInWithPassword ─────────────────────────────────────►
  │ ◄──────────────────────────── access token (JWT) + refresh token ───┤
  │                                      │                              │
  │ 2. Authorization: Bearer <JWT> ─────►│                              │
  │                                      │ 3. verify signature via      │
  │                                      │    JWKS (ES256), check       │
  │                                      │    exp/aud ──────────────────►
  │                                      │ ◄── signing key (cached) ────┤
  │                                      │ 4. trust claims.sub as       │
  │                                      │    the user id               │
  │                                      │ 5. re-check tenant_members   │
  │                                      │    in Postgres before using  │
  │                                      │    any client-supplied       │
  │                                      │    tenant_id                 │
  │ ◄──── response ──────────────────────┤                              │
```

- **Signup / login / logout / password reset** are handled by the Supabase Auth API directly from the client SDKs (`supabase_flutter` on mobile, `@supabase/ssr` + `@supabase/supabase-js` on web) — the backend is not in this path at all. This is standard practice: Supabase Auth already does secure password hashing, email verification, token issuance/refresh, and rate limiting, and re-implementing it in FastAPI would add risk without adding value.
- **JWT verification in FastAPI** (`app/core/security.py`, `app/api/deps.py`): this project's Supabase keys use the newer **asymmetric signing key** scheme (`sb_publishable_...` / `sb_secret_...`, ES256 tokens), so the backend verifies tokens against the project's JWKS endpoint (`SUPABASE_JWKS_URL`) rather than a shared HS256 secret — this also means the backend never needs to hold a symmetric signing secret at all, only the public JWKS. `get_current_user` is a FastAPI dependency that decodes and verifies the bearer token and returns `AuthenticatedUser(id, email)`; every other dependency and route builds on this rather than reading a user id from the request itself.
- **Tenant authorization**: `require_tenant_membership` (a dependency) takes a `tenant_id` from the URL, but never trusts it — it queries `tenant_members` in Postgres for `(tenant_id, verified_user_id)` and 403s if no row exists. A client can put any `tenant_id` it wants in a URL; only a real membership row makes the request succeed.
- **RLS as defense in depth**: Postgres Row Level Security is enabled on every tenant-owned table so that even a direct Supabase client query (bypassing the backend entirely, e.g. for realtime subscriptions) is scoped to tenants the caller actually belongs to. See "RLS approach" below for the exact policy shape.

## RLS approach

- `auth.uid()` (provided by Supabase) is the only source of "who is asking" inside a policy — it reads the verified `sub` claim of the caller's JWT, the same claim the backend trusts.
- A `SECURITY DEFINER` helper function, `public.is_tenant_member(tenant_id)`, centralizes the membership check and avoids RLS recursion (a policy on `tenant_members` that queried `tenant_members` under RLS would recurse into itself). `public.tenant_role_for(tenant_id)` does the same for role-gated policies (e.g. only an owner can delete a tenant).
- **Policy shape, by table group:**
  - `profiles`: a user can read their own profile and profiles of people who share a tenant with them (for future "see your teammates" UI); they can only update their own row.
  - `tenants` / `tenant_members`: members can read; tenant creation/role changes/removal are gated by role (`owner`/`admin`), and a member may always remove themselves (leave a tenant).
  - `tasks`, `task_ai_results`, `plans`, `schedule_items`, `google_calendar_connections`, `google_calendar_event_mappings`, `usage_records`: tenant members can `SELECT` (so clients can read their own data directly, e.g. for realtime), but there is **no** `INSERT`/`UPDATE`/`DELETE` policy for the `authenticated` role on these tables. All writes to business data go through the FastAPI backend using the Supabase **service role** key, which bypasses RLS by platform default — this matches the architectural rule that the backend, not the client, enforces business rules (AI calls, validation, calendar sync) before data is written.
  - The `anon` role is explicitly revoked from all of the above — an unauthenticated request gets a permission-denied error, not an empty result set.
- This was verified live against a real Supabase project during this phase (not just written and assumed correct) — see `/docs/progress.md` for what was tested.

## Environment variables

See each app's `.env.example` for the authoritative, up-to-date list. Summary:

| App | Key variables |
|---|---|
| `backend` | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (server-side only, never shipped to a client), `SUPABASE_JWKS_URL`, `DATABASE_URL` (Supabase connection pooler) |
| `web` | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (anon/publishable key — safe for the browser), `NEXT_PUBLIC_API_BASE_URL` |
| `mobile` | `SUPABASE_URL`, `SUPABASE_ANON_KEY` (anon/publishable key — safe to ship in the app), `API_BASE_URL` |

The service role key is a backend-only secret: it bypasses RLS entirely, so it must never reach `web` or `mobile` code, bundles, or version control.

## Why each technology

- **Flutter**: one codebase for iOS + Android, avoids maintaining two native apps for an MVP team of this size.
- **Next.js + TypeScript**: mainstream, well-supported React framework; TypeScript catches integration bugs against the backend's API contracts early.
- **FastAPI**: async-first Python framework with automatic OpenAPI docs, good fit for an AI-heavy backend (easy to call Gemini, easy to reason about I/O-bound calendar/API calls) and fast to iterate in.
- **PostgreSQL via Supabase**: relational data model fits tasks/schedules/relationships well; Supabase bundles managed Postgres + Auth + Realtime, which removes three separate pieces of infrastructure we'd otherwise have to run ourselves for an MVP.
- **Gemini API**: chosen AI provider for task understanding/prioritization per product requirements.
- **Google Calendar API**: the calendar product users already live in; syncing there (rather than building a calendar UI) is the fastest path to real usage.
- **Docker**: consistent local dev environment and a portable deployment unit for the backend/web, without committing to a specific cloud platform yet.

## MVP vs. future production architecture

The MVP is intentionally a single backend service, hosted Postgres, and two thin clients — no microservices, no message queue, no cache layer, no Kubernetes. These are deferred, not rejected:

| Concern | MVP approach | Reconsider when |
|---|---|---|
| Compute | Single FastAPI container | Backend has distinct scaling needs per workload (e.g. AI calls vs. CRUD) that a single service can't meet |
| Async/background work | Synchronous request/response | Scheduling or Gemini calls become slow enough to need background jobs (would introduce a queue then, not before) |
| Caching | None | Read load or external API rate limits (Gemini, Calendar) prove it's needed |
| Orchestration | Docker Compose (dev), single container deploy (prod) | Traffic/operational complexity outgrows a single service — Kubernetes only if that's actually true |
| Realtime | Supabase Realtime | Requirements exceed what Supabase's realtime can provide |

This table itself is a decision and should be revisited in `/docs/decisions.md` as the product grows.

## Task API

The first vertical slice: authenticated task CRUD, callable identically from mobile and web.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/tasks` | Create a task in the caller's tenant |
| `GET` | `/tasks` | List the tenant's tasks (`?status=`, `?limit=`, `?offset=`), ordered by `due_at` (nulls last) then `created_at` |
| `GET` | `/tasks/{task_id}` | Fetch one task |
| `PATCH` | `/tasks/{task_id}` | Partially update a task (only fields sent are changed; an explicit `null` clears a nullable field, an omitted field is left alone) |
| `DELETE` | `/tasks/{task_id}` | Delete a task |
| `POST` | `/tasks/{task_id}/complete` | Convenience shortcut for `PATCH {"status": "done"}` |
| `GET` | `/tasks/prioritized` | Tasks left-joined with their latest AI result (`?status=`, `?limit=`, `?offset=`), ordered by `priority_score` desc (nulls last) — see "Mobile MVP" below |

Every route depends on `get_tenant_context` (`app/api/deps.py`): it resolves which tenant the request runs against, verifying membership in Postgres regardless of whether a `tenant_id` was supplied — an MVP account with exactly one (personal) tenant doesn't need to pass one at all; an account in more than one tenant must. `created_by` on create is always the verified caller's id from the JWT, never a client-supplied value. A task belonging to another tenant returns `404`, not `403`, from every route (`GET`/`PATCH`/`DELETE`/`complete`) — this avoids confirming a given id exists in someone else's tenant.

Fields are limited to what `tasks` actually has (see ADR-009 for why `category`/`priority` aren't here yet): `title`, `description`, `raw_input`, `status`, `due_at`, `estimated_minutes`. Both mobile and web call this same API with the caller's Supabase access token as `Authorization: Bearer` — neither client writes to Postgres directly.

## Gemini integration

Turns a task's title/description/raw text into a structured assessment, stored in `task_ai_results` (the table Phase 1 reserved for exactly this).

### Model and SDK

- **Model**: `gemini-3.1-flash-lite` — small, fast, free-tier-eligible. The name lives in config (`Settings.gemini_model`, env `GEMINI_MODEL`), never hardcoded in route/service code, so it can be swapped without a code change if Google renames or deprecates it.
- **SDK**: the official `google-genai` Python package (`from google import genai`), async client (`client.aio.models.generate_content`).
- **Structured output, not free-form text**: the request passes `response_mime_type="application/json"` and `response_schema=GeminiTaskAnalysis` (a Pydantic model) directly to Gemini; the SDK returns `response.parsed` already instantiated as that model (with a manual `model_validate_json(response.text)` fallback). Gemini's prose is never regex'd or hand-parsed.

### Structured output schema

`GeminiTaskAnalysis` (`app/schemas/ai.py`) is what's requested from Gemini and what its response is parsed into:

| Field | Type | Notes |
|---|---|---|
| `category` | enum | work / personal / health / finance / learning / household / other |
| `urgency` | enum | low / medium / high |
| `importance` | enum | low / medium / high |
| `priority_score` | number | intended 0–100; see Scoring below |
| `confidence_score` | number | intended 0.0–1.0; see Scoring below |
| `estimated_minutes` | integer | inferred from task details if no duration is stated |
| `reasoning` | string | one concise sentence |

### Scoring logic (deterministic, not left to Gemini)

Two independent layers, deliberately not merged (see ADR-011 for the full rationale):

1. **Structural validation (Pydantic, at parse time).** `category`/`urgency`/`importance` must be one of the declared enum values; all fields must be present and correctly typed. A violation here means Gemini didn't follow the requested shape at all — treated as a hard failure (`AiPrioritizationError`), and **nothing is written to the database**.
2. **Deterministic bounds (`GeminiTaskAnalysis.clamp()`, plain Python).** `priority_score` → clamped to [0, 100]; `confidence_score` → clamped to [0.0, 1.0]; `estimated_minutes` → clamped to [5, 480] minutes; `reasoning` → truncated to 500 characters. This runs on every successful response, always, before storage — Gemini proposes the numbers, the backend guarantees they're sane regardless of what Gemini actually returned.

There is no additional weighted-scoring formula in this phase — `priority_score` is Gemini's own qualitative-plus-quantitative judgment, bounds-enforced. A separate deterministic scoring model (e.g. combining urgency/importance/deadline proximity into a formula that overrides or blends with Gemini's score) is a reasonable future enhancement, not implemented here.

### Failure handling

`AiPrioritizationService.analyze()` (`app/services/ai.py`) wraps every failure mode — network error, API error, missing API key, unusable/malformed structured output — in a single `AiPrioritizationError`. The route (`POST /tasks/{id}/prioritize`) catches it and returns `502`, writing nothing to `task_ai_results`. A task's own row is never touched by a failed or malformed AI call.

### API

| Method | Path | Calls Gemini? |
|---|---|---|
| `POST` | `/tasks/{task_id}/prioritize` | **Yes** — the only route that does |
| `GET` | `/tasks/{task_id}/ai-result` | No — returns the latest stored result, or 404 |

Re-running `POST .../prioritize` inserts a new `task_ai_results` row (re-prioritization), it does not overwrite the previous one — the "latest" one is just the most recent by `created_at`.

### Cost / free-tier strategy

- Gemini is called from **exactly one route**, triggered only by an explicit user action ("Prioritize with AI" in both clients). It is never called from task creation, task listing/reading, realtime events, or on any kind of automatic retry/refresh.
- `GET .../ai-result` exists specifically so clients can display an already-computed result (e.g. re-opening a task) without spending another Gemini call.
- Prompts are small and fixed-shape (title + optional details, ~50–150 tokens), `max_output_tokens=400`, `temperature=0.2` — cheap and consistent, well within Gemini's free tier.
- No billing is enabled; only the free tier is used. No retries-on-failure loop exists — a failed call surfaces as an error to the user, who can explicitly retry (not the system automatically).

### Testing strategy

- `tests/test_ai_schemas.py` — 12 hermetic tests (no network) covering both validation layers: structural rejection and `clamp()` bounds enforcement.
- `tests/test_prioritize.py` — integration tests against the real Supabase project (real tenant/task rows, real auth) with the Gemini call itself replaced via `app.dependency_overrides[get_ai_service]` — a fake service returns canned success/failure results, so these are deterministic and free, covering: success, clamping, malformed output, API failure (no DB write in either failure case), unauthenticated, task-not-found, cross-tenant (404, and confirms Gemini is never even called), and re-prioritization.
- `tests/test_prioritize_live_gemini.py` — one optional, real end-to-end test that actually calls the Gemini API. Skipped unless `GEMINI_API_KEY` (and live Supabase config) is set; run manually / when a key is available, not part of routine hermetic CI.

## Google Calendar integration

Lets a user connect their Google Calendar, see connection status, read events/free-busy for a date range, and disconnect. **Read-only** — this phase creates, modifies, and syncs nothing on the user's actual calendar. This is the foundation the scheduling engine (a later phase) will read from.

### OAuth flow

```text
Client (web/mobile)          FastAPI backend                    Google
  │                                │                                │
  │ 1. GET /calendar/connect ─────►│ (authenticated fetch)           │
  │                                │ mints signed `state`            │
  │                                │ (tenant_id + user_id, 10 min)   │
  │ ◄──── {authorization_url} ─────┤                                 │
  │ 2. top-level redirect / external browser launch ─────────────────►
  │                                │                     user consents (or denies)
  │                                │ ◄── redirect: /calendar/callback?code=&state= ─┤
  │                                │ 3. verify `state` signature+expiry             │
  │                                │ 4. exchange `code` for tokens ─────────────────►
  │                                │ ◄── access_token, refresh_token, expires_in ───┤
  │                                │ 5. fetch connected account email ──────────────►
  │                                │ 6. encrypt + upsert google_calendar_connections │
  │ ◄── plain HTML confirmation page (no Authorization header available here) ──────┤
```

- **Step 1** (`GET /calendar/connect`) is a normal authenticated route — the client calls it with the usual `Authorization: Bearer <supabase-jwt>` header and gets back `{"authorization_url": "..."}`. The client then performs the actual redirect itself (`window.location = authorization_url` on web; an external browser launch via `url_launcher` on mobile).
- **Step 2's callback** (`GET /calendar/callback`) is hit directly by Google's browser redirect — there is no Authorization header on this request at all. This is why `state` exists: it's the only thing carrying "which tenant/user started this" across the trip, and it's cryptographically signed (see ADR-013) so it can be trusted the same way a JWT is trusted elsewhere in this backend — never as a bare claim.
- **CSRF protection**: `state` is verified (signature + 10-minute expiry) before anything else happens in the callback. An invalid, tampered, or expired `state` fails closed with a friendly HTML error page and no database write.
- **Denied authorization**: Google redirects back with `?error=access_denied` (or similar) instead of `?code=...` — handled explicitly, shows a friendly "cancelled" page, no error, no partial state written.
- **Missing refresh_token**: Google only issues a `refresh_token` on a user's *first* consent for a given app+scopes, unless `prompt=consent` is forced (which this backend always sends specifically to guarantee one is issued even on reconnect after a revoke). If Google still doesn't return one, the callback shows a clear error rather than silently storing an unusable connection.

### Required Google Cloud configuration

1. Create a project in the [Google Cloud Console](https://console.cloud.google.com/) (or use an existing one).
2. Enable the **Google Calendar API** for that project (APIs & Services → Library). This does **not** require billing to be enabled — the Calendar API has a free quota usable on a standard (non-billing) Cloud project.
3. Configure the **OAuth consent screen** (External or Internal, Testing mode is fine for development).
4. Create an **OAuth 2.0 Client ID** (APIs & Services → Credentials), type **Web application**.
5. Add an **Authorized redirect URI** that exactly matches `GOOGLE_OAUTH_REDIRECT_URI` (default `http://localhost:8000/calendar/callback` for local dev) — Google rejects any mismatch, including a trailing slash difference.
6. Copy the **Client ID** and **Client secret** into `backend/.env` as `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET`. **Never commit these** — if you download the JSON credentials file from the Cloud Console, copy its values into `.env` and delete the JSON file; it's covered by `.gitignore` (`client_secret*.json`) as a backstop, but don't rely on that.

No billing was enabled for this project, and none is required for the functionality built in this phase (OAuth + Calendar API read calls, within free quota). If a future phase's Google API usage ever requires billing, that should stop and be documented explicitly rather than enabled silently — this phase confirms it wasn't needed.

### Required scopes

Minimum necessary for this phase, requested in `app/services/google_calendar.py`:

| Scope | Why |
|---|---|
| `openid` | Standard OIDC scope, paired with the next one |
| `https://www.googleapis.com/auth/userinfo.email` | Identify which Google account was connected (shown to the user as "Connected as ...") |
| `https://www.googleapis.com/auth/calendar.readonly` | Read events and free/busy — covers everything this phase does |

No write scope (`https://www.googleapis.com/auth/calendar`) is requested — this phase never creates, modifies, or deletes a Google Calendar event.

### Token security

- **Encryption**: `refresh_token` (and `access_token`, when cached) are encrypted with Fernet (AES-128-CBC + HMAC-SHA256, from the `cryptography` package) before every write, via `app/core/crypto.py`. Nowhere else in the codebase touches those columns directly.
- **Key**: `TOKEN_ENCRYPTION_KEY`, a Fernet key from environment config. Generate with:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
  Never committed, logged, or sent to a client — server-side config only.
- **Rotation**: Fernet has no built-in multi-key rotation. At this MVP's scale, the accepted approach is: rotate the key, and treat every existing connection as needing reconnection (a decrypt failure surfaces as `reauth_required`, not a crash). A KMS-backed multi-key rotation scheme is a reasonable upgrade once there are enough real connections for that tradeoff to matter — not built here.
- **Access tokens are a cache, not a persisted secret**: `get_valid_access_token()` (`app/services/calendar_connections.py`) only calls Google to refresh when the cached token is missing or within 60 seconds of `token_expires_at`; most requests are served from cache. `access_token` is nullable specifically so it's never required to persist one.
- **Never logged**: no code path in `app/services/google_calendar.py`, `calendar_connections.py`, or `app/api/calendar.py` logs a token value (errors log Google's *error* response, not request tokens).
- **Never returned via any API**: `CalendarConnectionOut` (the only shape `/calendar/connection` and the callback page expose) has no token field, full stop — not even in an admin/debug sense.
- **Never sent to clients**: enforced twice — application code never puts a token in a response model, and the database itself revokes direct-client `SELECT` on the `access_token`/`refresh_token` columns (`0002_calendar_tokens.sql`), so even a bug that bypassed the backend and queried Supabase directly as the signed-in user couldn't read them.
- **Do not store access tokens unnecessarily**: see "cache, not persisted secret" above — this is the literal mechanism, not just a stated goal.

See ADR-014 for the full schema-change rationale.

### API

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/calendar/connection` | Required | Current status: `not_connected` / `connected` / `reauth_required` / `error`. Never includes a token. |
| `GET` | `/calendar/connect` | Required | Returns `{"authorization_url": "..."}` for the client to redirect to. |
| `GET` | `/calendar/callback` | None (Google's redirect; trusts signed `state`) | Exchanges the code, stores the (encrypted) connection, shows a plain HTML confirmation/error page. |
| `DELETE` | `/calendar/connection` | Required | Best-effort revoke at Google, then always deletes the local row. |
| `GET` | `/calendar/calendars` | Required | The connected account's calendar list (id/summary/primary). |
| `GET` | `/calendar/events?start=&end=` | Required | Normalized events in range (max 90 days). |
| `GET` | `/calendar/availability?start=&end=` | Required | Normalized busy intervals in range (max 90 days). |

Every authenticated route here uses the same `get_tenant_context` dependency as the task API — a connection is scoped to `(tenant_id, user_id, calendar_id)`, and cross-tenant access is impossible by construction (verified by test). `/calendar/events`, `/calendar/availability`, and `/calendar/calendars` all 404 if there's no connection, and 409 with `{"detail": {"code": "REAUTH_REQUIRED", ...}}` if the stored refresh token is rejected by Google — a client can distinguish "not connected yet" from "was connected, needs reconnecting" without guessing.

### Calendar data model

Google's raw event payload carries far more than a scheduling engine needs (attendees, conference data, description, location, extended properties, ...). `CalendarEventOut` (`app/schemas/calendar.py`, normalized by `app/services/calendar_events.py`) keeps only:

| Field | Notes |
|---|---|
| `event_id` | Google's event id |
| `title` | Falls back to `"(No title)"` if Google's `summary` is absent |
| `start` / `end` | Timed events use `dateTime`; all-day events use `date`, normalized to UTC midnight |
| `all_day` | `true` if the event has no time component |
| `status` | `confirmed` / `tentative` / `cancelled` |
| `is_recurring` | `true` if the event is an instance of a recurring series (`recurringEventId` present) |

A bare cancelled-instance stub (`{"id": ..., "status": "cancelled"}` with no `start`/`end`, which Google's API can return for a cancelled occurrence of a recurring event) is dropped during normalization rather than crashing — it can't be placed on a timeline.

`/calendar/availability` normalizes Google's `freeBusy.query` response into `{"busy": [{"start", "end"}, ...]}` for the requested range — "free" is implicitly everything else in that range; computing actual free *slots* (gaps between busy intervals, filtered by working hours, etc.) is scheduling-engine work, deliberately not built here.

### Cost / free-tier strategy

- Google Calendar API's free quota (1,000,000 queries/day at the time of writing) is far beyond what an MVP's read-only, on-demand usage will approach — no rate-limiting logic was needed beyond passing through Google's own 429 responses as 429s to the client (see Error handling).
- No Google Cloud billing account was enabled or required — the Calendar API and OAuth work fully on a non-billing Cloud project.
- Access tokens are cached and only refreshed when actually needed (not on every request) — see Token security above.
- No polling, no webhooks/watch channels (explicitly out of scope for this phase — see /docs/progress.md), no background jobs hitting Google on a schedule. Every Google API call in this phase is the direct result of an explicit user action (connect, view availability, disconnect) or a request the client made.

### Error handling

| Situation | Behavior |
|---|---|
| OAuth denied by user | Friendly HTML page, no DB write |
| Invalid/expired/tampered `state` | Friendly HTML page, no DB write |
| Google rejects the refresh token (`invalid_grant`) | Connection marked `reauth_required` in the database; subsequent calendar reads return `409 REAUTH_REQUIRED` instead of a generic error |
| Google returns 401/403 on a Calendar API call | Mapped to `409 REAUTH_REQUIRED` (treated the same as a rejected refresh — access was denied) |
| Google returns 429 | Passed through as `429` to the client; no automatic retry |
| Any other Google 4xx/5xx | `502 Bad Gateway`, "Google Calendar is temporarily unavailable" |
| Disconnected account (no connection row) | `404` from every calendar-data route |

### Testing strategy

- `tests/test_crypto.py` — 5 hermetic tests: encrypt/decrypt round trip, wrong key, corrupted ciphertext, missing key.
- `tests/test_oauth_state.py` — 6 hermetic tests: valid round trip, tampered, expired, garbage, wrong signing secret, missing secret.
- `tests/test_calendar_normalization.py` — 8 hermetic tests: timed/all-day/recurring/cancelled events, missing title, bare cancelled stubs, free/busy normalization.
- `tests/test_calendar.py` — 27 integration tests against the real Supabase project with every Google call replaced via monkeypatching `app.services.google_calendar` functions (deterministic, free): unauthenticated access, not-connected 404s, the full connect→callback→connected flow, reconnecting (upsert not duplicate), cross-tenant isolation, disconnect, revoked-refresh-token → `REAUTH_REQUIRED` (with the DB status actually updated, not just the one response), normalized events/availability/calendars, and Google 429/5xx → 429/502 mapping.
- `tests/test_calendar_live_google.py` — 4 tests against the **real** Google API (skipped unless Google OAuth credentials are configured): confirms the registered OAuth client + redirect URI are actually accepted by Google (not just internally self-consistent), and pins the real error shapes (`invalid_grant`, `401`) this backend's error handling assumes. A full human-interactive consent flow isn't scriptable and isn't attempted; these are the strongest checks possible without one.

## Scheduling engine

Combines a task's Gemini-derived priority/duration (Phase 3), its deadline (`tasks.due_at`), and Google Calendar availability (Phase 4) to propose *when* a task should happen. This phase only **proposes** — nothing is written to `schedule_items` or to Google Calendar; that's deliberately deferred to a later "apply" phase.

### Where the decision is made

```text
Gemini (Phase 3)                Scheduling engine (this phase)
  │                                        │
  │ priority_score, urgency,               │
  │ estimated_minutes ─────────────────────►
  │ (task intelligence)                    │ decides: which day, which
  │                                        │ interval, what start/end time
  │                                        │ (deterministic arithmetic --
  │                                        │  no model call happens here)
```

Gemini is never consulted again once a task has a priority/duration — the scheduling engine (`app/services/scheduling.py`) is pure Python: no database, no Gemini, no Google API calls inside it. That's what makes it fully unit-testable with fixed fixtures and what makes "why did task X land at 2pm on Tuesday" always answerable by reading the code, not by re-running a model. See ADR-016.

### Algorithm (deterministic greedy / ranked-slot)

1. **Compute free intervals.** For each calendar day in `[horizon_start, horizon_end]`, intersect the configured working-hours window (`09:00–18:00` UTC by default) with that day, then subtract every busy interval from `/calendar/availability`. Sub-intervals shorter than `min_block_minutes` (default 30) are discarded.
2. **Sort tasks**: priority `DESC`, then deadline `ASC` (a task with no deadline sorts after one with a deadline, at equal priority), then task id as a final deterministic tiebreak. This directly implements requirements 1 and 2 ("higher priority first," "earlier deadlines first") and, because tasks are placed in this order, requirement 7 ("prefer earlier completion for high-priority/urgent tasks") falls out naturally — a high-priority task is placed before a lower-priority one even gets to look at the calendar.
3. **Walk the sorted tasks, one at a time (greedy).** For each task:
   - Find every remaining free interval that can fit the task's full duration and ends before its deadline (if any) — a deadline falling *inside* a free interval truncates the usable portion of that interval rather than disqualifying it outright.
   - No candidate → the task is reported unscheduled with a specific reason (see Failure cases), never silently dropped.
   - One or more candidates → score each (see Scoring below) and take the highest; ties break on earliest start.
   - Place the task at the **start** of the chosen interval (front-loaded — earliest possible, again favoring urgent/high-priority work).
   - **Consume** that slice of time from the free-interval pool before moving to the next task, so two tasks in the same proposal can never overlap (requirement 3/5).

### Scoring model

```text
score = min(100, priority_score + bonus)

bonus = 0
bonus += earliest_slot_bonus  (default +3)  if this candidate starts at the earliest available time among this task's candidates
bonus += snug_fit_bonus       (default +2)  if duration / candidate_interval_length >= snug_fit_threshold (default 0.6)
```

`priority_score` (0–100, from Gemini) dominates the score by design — the bonuses are small and additive, reflecting *slot quality* on top of the AI's own priority judgment, not overriding it. "Snug fit" rewards choosing a tightly-fitting interval over fragmenting a much larger one when both would work equally well timing-wise (preserves bigger blocks for later tasks). Every scheduled item also gets a plain-English `reason` string built from the same inputs (see Example below) — never templated boilerplate disconnected from the actual score.

### Constraints (configurable, not hardcoded)

`SchedulingConstraints` (`app/services/scheduling.py`) is a single dataclass threaded through every function in the engine — nothing reads a magic number from elsewhere:

| Field | Default | Meaning |
|---|---|---|
| `working_hours_start_hour` / `working_hours_end_hour` | 9 / 18 | Daily working window (UTC — see note below) |
| `min_block_minutes` | 30 | Free intervals shorter than this are discarded as unusable |
| `default_priority_score` | 50.0 | Priority assumed for a task explicitly requested by id but with no AI result yet |
| `earliest_slot_bonus` | 3.0 | Scoring bonus for the earliest-starting candidate |
| `snug_fit_bonus` | 2.0 | Scoring bonus for a tightly-fitting candidate |
| `snug_fit_threshold` | 0.6 | Fraction of interval length a task must use to count as "snug" |

**Known MVP simplification**: working hours are applied in UTC across every day in the horizon, including weekends — there is no per-user timezone or working-day-of-week configuration anywhere in the schema yet (Phase 4's calendar data is UTC throughout too). This is a documented default, not an oversight; adding per-user timezone/working-days is a small, additive change to `SchedulingConstraints` and how it's constructed, not a rewrite of the algorithm.

### Failure cases

| Situation | Result |
|---|---|
| No free interval big enough anywhere in the horizon | Unscheduled: "No free `N`-minute interval in the requested window was found." |
| A free interval exists but not before the deadline | Unscheduled: "No free `N`-minute interval before the deadline (`...`) was found." |
| Deadline falls inside an interval, truncating it below the needed duration | Same as above — the truncated usable portion is what's checked |
| Task has no AI result and no `estimated_minutes` | Unscheduled: "No duration estimate available — run AI prioritization or set an estimated duration." |
| Task id doesn't exist / belongs to another tenant | Unscheduled: "Task not found." (no distinction shown to the caller — same as other routes' cross-tenant handling) |
| No calendar connection | `404` for the whole request — there's no availability to schedule against |
| Refresh token rejected by Google | `409 {"code": "REAUTH_REQUIRED"}` for the whole request |
| Google Calendar rate-limited / down | `429` / `502` for the whole request, no partial/silent result |

A task that can't be scheduled never blocks the rest of the batch — `unscheduled` and `scheduled` are independent lists in the same response.

### API

`POST /tasks/schedule` — authenticated, tenant-scoped (same `get_tenant_context` dependency as the task API).

Request:

```json
{
  "task_ids": ["<uuid>", "..."],   // omit to consider every unscheduled, prioritized task in the tenant
  "horizon_start": "2026-09-01T00:00:00Z",  // omit to default to now
  "horizon_end": "2026-09-15T00:00:00Z"     // omit to default to horizon_start + 14 days; max 60-day span
}
```

"Every unscheduled, prioritized task" (when `task_ids` is omitted) means: `status = 'pending'` **and** at least one row in `task_ai_results` exists for it. A task requested explicitly by id that has no AI result yet still gets a default priority (`default_priority_score`) rather than being rejected outright, provided it has an `estimated_minutes` to schedule against.

Response (`ScheduleProposal`):

```json
{
  "horizon_start": "2026-09-01T00:00:00Z",
  "horizon_end": "2026-09-15T00:00:00Z",
  "scheduled": [
    {
      "task_id": "...",
      "title": "Finish client proposal",
      "start": "2026-09-01T09:00:00Z",
      "end": "2026-09-01T11:00:00Z",
      "priority_score": 94,
      "score": 96,
      "reason": "High-priority task (score 94) with a deadline of 2026-09-04T18:00:00Z; scheduled into a 120-minute window (earliest available slot; tightly fits the window, preserving other free time)."
    }
  ],
  "unscheduled": []
}
```

### Example (from the product brief — verified against the real engine, not hand-calculated)

```text
Task: Finish client proposal
Priority: 94   Duration: 120 minutes   Deadline: Friday

Calendar: Monday fully booked, Tuesday 10-12 available, Wednesday 14-17
available, Thursday heavily booked
```

Running this exact scenario through `build_schedule()`:

```text
start: 2026-08-25 10:00:00+00:00   end: 2026-08-25 12:00:00+00:00   score: 99.0
reason: High-priority task (score 94) with a deadline of 2026-08-28T18:00:00+00:00;
        scheduled into a 120-minute window (earliest available slot;
        tightly fits the window, preserving other free time).
```

Tuesday's 10:00–12:00 block is both the earliest valid candidate (Monday is fully booked) and an exact 120-minute fit for the 120-minute task (`120/120 = 1.0 ≥ 0.6` snug threshold) — where Wednesday's 3-hour block would leave an hour unused. Both bonuses apply: `94 + 3 (earliest) + 2 (snug) = 99`. This is "the best valid slot," not simply the first free one Google happened to return — Wednesday's block is free too, but Tuesday wins on both being earlier and fitting tighter.

### Incremental OAuth for the future write scope

This phase never creates a Google Calendar event — but a later "Apply Schedule" phase will, and needs `https://www.googleapis.com/auth/calendar.events` to do it. `app/services/google_calendar.py` is already shaped for that: `READ_ONLY_SCOPES` (unchanged, still all any route in this phase requests) and `WRITE_SCOPES` (read scopes + `calendar.events`) are both defined, and `build_authorization_url()` accepts an optional `scopes` parameter plus always sends `include_granted_scopes=true`. A future phase requesting `WRITE_SCOPES` performs Google's documented *incremental authorization*: the existing connection is upgraded in place (the user is asked to additionally grant write access, not to re-consent from scratch), no disconnect/reconnect required, and this phase's read-only connect flow is completely unaffected. See ADR-017.

### Cost

No new external calls beyond what Phase 3 (Gemini, already-stored results) and Phase 4 (`/calendar/availability`, already implemented) provide — `POST /tasks/schedule` makes exactly one Google API call (`freeBusy.query`, already used by `/calendar/availability`) regardless of how many tasks are being scheduled. No optimization service, no queue, no Redis — the whole engine runs in-process, synchronously, within the request.

### Testing strategy

- `tests/test_scheduling.py` — 25 hermetic tests (no network, no database) directly exercising `app/services/scheduling.py` with fixed fixtures: working-hour boundaries, busy-interval subtraction (including overlapping busy intervals and sub-minimum slivers), one task/one slot, multiple tasks, priority ordering, deadline ordering (including the no-deadline-sorts-last case and a deadline truncating a free interval), exact-fit slots, duration-longer-than-any-slot, completely insufficient availability, never-overlapping placement (both against existing busy intervals and between two newly-scheduled tasks), preferring a snug fit over fragmenting a larger block, determinism across repeated runs, and configurable constraints actually changing behavior.
- `tests/test_schedule_api.py` — 9 integration tests against the real Supabase project with Google calls mocked (deterministic, free): unauthenticated, horizon validation, calendar-not-connected 404, a full proposal end to end, no-free-slot handling, missing-duration handling, unknown-task-id handling, cross-tenant isolation (Bob requesting Alice's task id gets "Task not found," nothing about it is exposed), and auto-select mode only considering the caller's own tenant.

## Apply Schedule

Completes the flow: task → AI priority → scheduling proposal → **user approval** → Google Calendar events → stored event mappings. Nothing here runs automatically — every event this phase creates is the direct result of a user tapping "Apply to Google Calendar" on a proposal they reviewed first.

### Timezone strategy

Phase 5 shipped a UTC-only scheduling engine as a documented MVP simplification. This phase replaces it:

- **Source of truth**: the connected Google Calendar's own `timeZone` (from `calendars.get`), fetched once at connect time and cached on `google_calendar_connections.calendar_timezone`. Not a user preference field, not inferred from IP/locale/browser — the calendar the events are actually going into is authoritative.
- **Internally**: `SchedulingConstraints.working_hours_timezone` (an IANA name) is threaded into `compute_free_intervals()`, which resolves each day's 09:00/18:00 boundary via direct `datetime(year, month, day, hour, tzinfo=ZoneInfo(tz))` construction — deliberately not "midnight + a fixed `timedelta`", which does not re-resolve the UTC offset and would silently misplace working hours by an hour on a DST-transition day. Every datetime everywhere in this codebase remains timezone-aware; nothing is ever compared or stored as naive.
- **Writing events**: `create_event()` localizes the event's start/end into the target timezone (`.astimezone(ZoneInfo(tz))`) before serializing, and sends both the ISO datetime *and* an explicit `timeZone` field to Google — so the event Google actually stores has the correct wall-clock time in the calendar's own zone, not merely the correct instant with a same-instant-different-label mismatch.
- **A connection with no cached timezone yet** (e.g. the `calendars.get` call failed non-fatally during connect) falls back to UTC rather than crashing — `ConnectionRecord.timezone_or_utc`.
- **Platform note**: neither Windows (this dev machine) nor `python:3.11-slim` (the Docker base image) ship a system IANA timezone database, so stdlib `zoneinfo` raises `ZoneInfoNotFoundError` even for `"UTC"` without one. `tzdata` (Python's pip-installable copy of the IANA database) is now a normal dependency — see ADR-018.

### OAuth write-scope flow (incremental authorization)

```text
User taps "Apply to Google Calendar"
        │
        ▼
Does the connection already have calendar.events? ── yes ──► apply directly
        │ no
        ▼
Client shows "Connect Calendar permissions"
        │
        ▼
GET /calendar/connect?scope=write ──► authorization_url (WRITE_SCOPES,
        │                              include_granted_scopes=true)
        ▼
User redirected to Google, grants the additional scope
        │
        ▼
GET /calendar/callback ──► same connection row upgraded in place
        │                  (granted_scopes now includes calendar.events;
        │                   refresh_token replaced with one covering both
        │                   the original read scopes AND the new write one)
        ▼
User returns to the app, taps "Apply to Google Calendar" again ──► now succeeds
```

- `GET /calendar/connect?scope=write` is the *only* thing that ever requests `WRITE_SCOPES`; the default (`?scope=read` or omitted) is unchanged from Phase 4 and still all any other route uses.
- **The existing read-only connection is never disconnected or replaced** — the callback's `upsert_connection()` call is the same `ON CONFLICT (tenant_id, user_id, calendar_id) DO UPDATE` used since Phase 4; an incremental-auth callback just updates `granted_scopes`/tokens on the same row.
- **Denied authorization**: handled by the exact same `/calendar/callback?error=...` path Phase 4 already built — a friendly page, no state change, the connection keeps whatever scope it already had.
- See ADR-019 for how "does this connection have write access" is tracked (`granted_scopes`, surfaced as `CalendarConnectionOut.has_write_access`).

### Apply endpoint

`POST /tasks/schedule/apply` — authenticated, tenant-scoped.

```json
{
  "items": [
    {"task_id": "<uuid>", "start": "2026-09-01T13:00:00Z", "end": "2026-09-01T14:00:00Z"}
  ]
}
```

1. `404` if no calendar connection exists.
2. `403 {"code": "CALENDAR_WRITE_SCOPE_REQUIRED"}` if the connection lacks write access — checked once, up front, before touching anything.
3. `409 {"code": "REAUTH_REQUIRED"}` if the refresh token is rejected — same as every other calendar route.
4. One `freeBusy.query` call covering the full span of the request (not one per item — see Cost below).
5. Each item is independently revalidated and applied (see "Apply endpoint revalidation" below) — one item's failure never aborts the batch.

**Apply endpoint revalidation** ("never trust client-supplied timestamps blindly", per the brief): for every item, before anything is written, `apply_schedule_item()` (`app/services/schedule_apply.py`) checks, against fresh data — not what the client asserted:

- The task actually exists and belongs to the caller's tenant (looked up fresh; a stale/forged task id from another tenant is reported "Task not found", same as every other cross-tenant-safe route in this app).
- `end > start`.
- `end` is before the task's real `due_at`, if it has one.
- The slot doesn't overlap the *freshly re-queried* free/busy snapshot (not the one the original proposal was computed from — time may have passed, or another app/device may have booked something since).
- The slot doesn't overlap another item in the same apply request (a freshly-created event from an earlier item in this same batch wouldn't appear in that one stale freebusy snapshot, so this check exists specifically to catch that case).

A slot that fails any of these is reported failed with a specific reason; the client's proposed time is never written as-is just because the client sent it.

### Google event creation

For each approved, revalidated item, `google_calendar.create_event()` creates exactly one new event:

| Field | Source |
|---|---|
| `summary` | The task's title |
| `description` | Task description (if any) + category/priority/urgency from the latest AI result + "Scheduled by AI Work Planner." |
| `start` / `end` | The revalidated (not client-trusted) start/end, localized into the calendar's timezone |
| `timeZone` | The connection's `calendar_timezone` |
| `extendedProperties.private` | `{"app": "ai-work-planner", "task_id": "...", "tenant_id": "...", "schedule_item_id": "..."}` -- see below |

**Google event metadata**: `extendedProperties.private` is Google's documented mechanism for attaching application-specific data to an event that's readable only by the app that wrote it via the API (not shown in Google Calendar's UI, not visible to other apps) — used here to tag every event this app creates with the task/tenant/schedule-item identifiers that map it back to this database, without polluting the event's visible description with machine-readable IDs.

**This never touches any other event.** `create_event()` is purely an insert against the events collection; nothing in this phase reads, modifies, or deletes an event it didn't itself just create (except the one test-only `delete_event()` helper used solely to clean up after the live smoke test — see Testing strategy).

### Event mapping / persistence

Uses the existing Phase 1 schema unchanged (no new tables):

```text
tasks ──1:N── schedule_items ──1:1── google_calendar_event_mappings
                   │
                   └── plan_id → plans (auto-created "My Plan" per tenant,
                                          see ADR-021 -- no plan-management
                                          feature was built, just enough to
                                          satisfy the NOT NULL plan_id)
```

`schedule_items` (`tenant_id`, `plan_id`, `task_id`, `starts_at`, `ends_at`, `status`) records what was scheduled; `google_calendar_event_mappings` (`schedule_item_id` UNIQUE, `connection_id`, `google_event_id`, `sync_status`, `last_synced_at`) records the resulting Google event, only ever inserted **after** `create_event()` succeeds.

### Idempotency

At most one active `schedule_items` row per `(tenant_id, task_id)` in this MVP. Applying the same task twice:

1. **Already synced** (a `google_calendar_event_mappings` row with `sync_status = 'synced'` exists) → `already_applied`, the stored `google_event_id`/time is returned, **no Google call is made**.
2. **Never successfully synced** (no `schedule_items` row yet, or one exists with no mapping — a previous attempt failed) → treated as a genuine attempt: the `schedule_items` row is created or updated in place, `create_event()` is called, and on success a mapping is inserted for the first time.

See ADR-020 for why "no mapping row" (rather than a failed-state sentinel value) is what represents "attempted but not yet synced" — `google_event_id` is `NOT NULL` + unique per connection, so there's no safe sentinel for "failed, no real id yet."

### Partial failure behavior

Every item in an apply request is processed independently; one failing never stops or rolls back the others.

```text
Created: 2
Failed: 1
```

`ScheduleApplyResult` reports `created` / `already_applied` / `failed` counts plus a per-item `results` list (`status`, `google_event_id` if created, `reason` if failed). Handled failure causes:

| Cause | Behavior |
|---|---|
| Task not found / cross-tenant | That item fails with "Task not found." |
| Slot no longer free (revalidation) | That item fails with "That time is no longer available on your calendar." |
| Slot after deadline (revalidation) | That item fails, reason cites the deadline |
| Overlaps another item in the same batch | That item fails |
| Google rate limit (429) on one event | That item fails, "try again shortly" — no whole-batch abort |
| Google server error on one event | That item fails with the underlying error, others still attempted |
| Refresh token revoked / missing write scope | Whole request fails up front (`409`/`403`) before any event is attempted — these are connection-level, not per-item, so failing fast avoids `N` doomed API calls |

Failed items are always safely retryable in a later request without duplicating anything already created — see Idempotency above.

### Cost / free-tier strategy

No new external services. `POST /tasks/schedule/apply` makes exactly one `freeBusy.query` call (covering the whole batch) plus one `events.insert` call per item actually needing creation (already-applied items make zero calls) — all within Google Calendar API's free quota, same as every other phase. No billing enabled, no paid tier anywhere in this stack.

### Testing strategy

- Timezone correctness added to `tests/test_scheduling.py` (hermetic): working hours in a named non-UTC zone, and a dedicated DST-spring-forward-boundary test confirming the UTC offset actually changes across the transition (not just the local wall-clock hour, which stays "9" either side and would pass even if the code were wrong).
- `tests/test_schedule_apply.py` — 18 integration tests against the real Supabase project, every Google call mocked: unauthenticated, empty-items, not-connected, incremental-auth scope requests (read vs. write URLs), missing-write-scope 403, connection status reporting `has_write_access`/`calendar_timezone`, upgrading read→write on the same connection, successful creation with correct metadata/timezone asserted from the actual call arguments, duplicate-apply idempotency (asserts Google is called exactly once across two identical requests), retrying a previously-failed item (succeeds without duplicating), partial failure (1 of 3 fails, exact per-item status order asserted), revalidation rejecting a slot that's no longer free / past deadline / for an unknown task, revoked-token 409, and cross-tenant protection.
- `tests/test_schedule_apply_live_google.py` — one optional, real end-to-end test: creates one real Calendar event via the actual Google API and deletes it immediately after (cleanup runs even if an assertion fails). Requires a real refresh token with `calendar.events` already granted, which (like Phase 4's live OAuth test) can't be obtained without a human completing Google's consent screen once — skipped, not failed, unless `TEST_LIVE_CALENDAR_REFRESH_TOKEN` is set; the test file's docstring documents exactly how to obtain one.

## Two-way Calendar synchronization

Phase 7 closes the loop opened by Phase 6: changes made directly in Google Calendar (an event moved, deleted, or created outside the app) now reach this app's database, and — because the earlier phases already made every app-created event traceable back to a task — the reverse direction (task → schedule item → Google event) stays intact throughout. Explicitly **not** built this phase: automatic rescheduling in response to an external change, recurring-event intelligence, team calendars, or writing anything back to Google in response to a detected external change (see "Loop prevention" below for why that last one is a design choice, not a gap).

```text
Google Calendar
      |
      v
  Webhook (push notification -- headers only, never the event itself)
      |
      v
  Incremental sync (Google's syncToken mechanism)
      |
      v
  PostgreSQL (schedule_items, google_calendar_event_mappings,
              google_calendar_external_events)
      |
      v
  Supabase Realtime (logical replication -- no app code "publishes")
      |
      v
  Flutter + Web (subscribed, refetch on change)
```

### Watch channels

`app/services/calendar_sync.py`'s `ensure_watch_channel()` registers a Google Calendar `events.watch` push-notification channel for a connection, opportunistically — called after every successful `/calendar/callback` and at the top of every `POST /calendar/sync` call, and a no-op in both cases unless the current channel is missing or within `calendar_watch_renew_within_hours` (default 24h) of `watch_expires_at`. Each channel gets:

- a random channel id (`watch_channel_id`) and a random per-channel secret (`watch_token`), both generated by this app — Google just echoes them back;
- Google's `resourceId` for that channel (`watch_resource_id`);
- an expiration `calendar_watch_ttl_days` (default 7) out, well inside Google's own maximum for this resource.

Google channels cannot be renewed in place — a new channel is registered and the old one stopped (`channels.stop`, best-effort) afterward. Registration requires `GOOGLE_CALENDAR_WEBHOOK_URL` to be a real, public, HTTPS endpoint (Google refuses `http://` and unreachable/localhost addresses outright); left unset, `ensure_watch_channel()` does nothing and the connection relies entirely on the reconciliation fallback below. This is the expected state in local development, where no public HTTPS URL exists — see "Free-tier / local-dev considerations".

### The webhook endpoint

`POST /calendar/webhook` is the only route in this app with no user JWT — it's called directly by Google's servers. Google's push notifications never carry the changed event itself, only headers: `X-Goog-Channel-Id`, `X-Goog-Resource-Id`, `X-Goog-Resource-State` (`sync` on the initial handshake right after registration, otherwise a real notification), and `X-Goog-Channel-Token`. The handler:

1. Looks up the connection by `X-Goog-Channel-Id`. Unknown/stale channel id → `200 OK`, nothing else happens (avoids both leaking which channel ids are valid and inviting a Google retry storm over something that will never succeed).
2. Rejects the call (still `200 OK`, same reasoning) unless `X-Goog-Channel-Token` matches the stored `watch_token` for that connection **and** `X-Goog-Resource-Id` matches the stored `watch_resource_id` — a webhook call is never trusted on channel id alone.
3. `resource_state == "sync"` (the registration handshake) → acknowledge, no sync triggered.
4. Otherwise, schedules an incremental sync as a FastAPI `BackgroundTask` and returns immediately — Google expects a fast response, and the sync itself never needs to block it.

### Incremental sync (`syncToken`)

`app/services/calendar_sync.sync_connection()` is the single entry point for both webhook-triggered and manually-triggered syncs. Each connection stores its own `sync_token` (Google's opaque incremental-sync cursor):

- **No token yet** (new connection, or one just cleared by a 410 — see below): a *full* sync, bounded to `[now - calendar_sync_window_days_past, now + calendar_sync_window_days_future]` (defaults: 7 days back, 90 days forward) rather than a user's entire calendar history — deliberately narrow to stay well inside the free quota.
- **Has a token**: an incremental `events.list?syncToken=...` call, returning only what changed since the last sync, scoped to whatever window established that token. `singleEvents=true` and `showDeleted=true` are always set (tombstones are how deletions surface); `orderBy` is never set, since Google's API rejects it whenever `syncToken` is in play.
- **`410 Gone`**: Google's documented signal that the token is no longer valid (e.g. too much time has passed). Recovery: clear `sync_token`, redo the sync as a full (bounded-window) sync, store whatever `nextSyncToken` that full sync ends with. Retried at most once per call.
- On success, the page's `nextSyncToken` replaces the stored `sync_token` and `last_synced_at` advances to now — whether or not anything actually changed.

A Postgres session-level advisory lock (`pg_try_advisory_lock(hashtext('calendar_sync'), hashtext(connection_id))`, held on one dedicated pooled connection for the duration) serializes concurrent syncs of the *same* connection; a second caller that loses the race returns immediately (`synced: false, reason: "... already in progress"`) rather than racing the same `syncToken`, which Google's API is not safe to use concurrently from two callers.

### Event mapping

Every event from a sync page is looked up by `(connection_id, google_event_id)` against `google_calendar_event_mappings` first — that table, not the event's `extendedProperties`, is the authoritative answer to "is this ours":

- **Mapping found, event cancelled** → an app-created event was deleted externally. The mapping's `sync_status` becomes `'deleted'` and `schedule_items.needs_attention`/`attention_reason` are set. The app never silently recreates it — `GET /tasks/schedule/needs-attention` surfaces it, and the explicit fix is the user re-running `POST /tasks/schedule/apply` for that task, which (per Phase 6) creates a fresh event since the existing mapping is no longer `'synced'`.
- **Mapping found, event present** → an app-created event was moved (or otherwise edited). If the event's Google `updated` timestamp is newer than what's recorded on the mapping, `schedule_items.starts_at`/`ends_at` are updated to match and the mapping's `google_updated_at` advances. If it's *not* newer, this is a no-op — see "Loop prevention".
- **No mapping, cancelled** → an external event (never ours) was deleted; its row in the local cache is removed if present.
- **No mapping, present, and tagged `extendedProperties.private.app = "ai-work-planner"` with a `schedule_item_id` this tenant actually owns** → a self-heal path: the event really is ours, but the mapping insert never happened (e.g. Google's `events.insert` succeeded and the app crashed before recording it in Phase 6's `_mark_synced`). A mapping row is adopted/inserted now rather than the event being miscategorized as external.
- **No mapping, present, not ours** → a genuine external event. Normalized (title, start/end, all-day flag, status) and upserted into `google_calendar_external_events`, keyed by `(connection_id, google_event_id)` — this is the local cache of "other things on your calendar", shown to the user as busy blocks, and never turned into a task.

```text
Task
 |
 v
Schedule Item  <---- moved/deleted here on an app-created external change
 |
 v
Google Event   (google_calendar_event_mappings: schedule_item_id <-> google_event_id)
```

### Loop prevention

The scenario the brief calls out — "our app changes event → webhook → backend changes event → webhook → ..." — requires a backend write-back step to exist at all. It doesn't: nothing in `calendar_sync.py` ever calls Google's API to create, update, or delete an event in response to a detected external change; every branch above only writes to this app's own database. That asymmetry (Google → app is live-synced; app → Google only ever happens through the explicit, user-initiated `POST /tasks/schedule/apply`) is most of "loop prevention" by construction.

What's left is making sure a **duplicate or replayed** notification, or two overlapping sync passes, is still handled idempotently:

- Every `google_calendar_event_mappings` row carries `google_updated_at` — Google's own `updated` timestamp for the event, recorded both right after this app creates it (from the `events.insert` response, in `schedule_apply.py`) and after every sync that touches it. An incoming sync entry whose `updated` is not strictly newer than the stored value is recognized as either an echo of the app's own last write or a replayed notification, and is skipped (`app_noop`) rather than reapplied.
- A cancellation for a mapping whose `sync_status` is already `'deleted'` is a no-op (`app_delete_noop`) — replaying the same deletion notification twice never re-flags anything or errors.
- External-event upserts and deletes are naturally idempotent (`ON CONFLICT ... DO UPDATE` / a plain `DELETE` that no-ops if the row is already gone).
- The advisory lock (above) prevents two concurrent syncs of the same connection from racing the same `syncToken`.

### Realtime propagation

`schedule_items`, `google_calendar_event_mappings`, `google_calendar_external_events`, and `google_calendar_connections` are all in the `supabase_realtime` publication (`database/migrations/0004_calendar_sync.sql`) and already have an RLS `SELECT` policy scoping rows to tenant members (from `0001_init.sql`). That combination means Supabase's realtime engine — which reads the same Postgres logical-replication stream the tables' row-level changes already produce — pushes a change to a subscribed client automatically, filtered by what that client's RLS policy would let them see, **with no application-level "publish" call**: the backend just writes to these tables the same way it always has (via its own service-role Postgres connection, same as every other write in this app), and Supabase's platform does the rest.

Both clients subscribe to `postgres_changes` (`event: "*"`) on these four tables (`web/src/lib/supabase/realtime.ts`, `mobile/lib/src/core/calendar_realtime.dart`) and, on any change, simply refetch what they're already displaying (connection status, the needs-attention list) — deliberately coarse rather than diffing payloads client-side, which is unnecessary for these small per-tenant datasets.

### Reconciliation fallback (no polling)

Push notifications require a public HTTPS webhook URL this local-dev environment doesn't have, and even a correctly configured one can legitimately miss a notification (Google does not guarantee delivery). Rather than poll on a timer, `POST /calendar/sync` is the explicit, cheap fallback: it runs `ensure_watch_channel()` (renewing if needed) and then exactly one `sync_connection()` pass, inline. Both clients call it once when the calendar panel mounts and expose a manual "Sync now" action — never on an interval. This is also exactly what "a missed webhook notification" recovers via: nothing distinguishes a reconciliation call from a notification-triggered sync at the code level, they're the same function.

### Connection lifecycle

- **Revoked/expired refresh token**: `get_valid_access_token` already marks the connection `reauth_required` (Phase 4/6 behavior, unchanged). `sync_connection()` catches `ReauthRequiredError` and returns `synced: false, reason: "Reauthorization required."` rather than raising — a sync failure here is informational, not a hard error the caller needs to handle specially.
- **Disconnect**: `DELETE /calendar/connection` now also best-effort stops the active watch channel (`channels.stop`) before deleting the row, same best-effort/never-block-the-user pattern as the existing refresh-token revoke call right below it.
- **Invalid sync token**: handled inline as part of every sync call — see "Incremental sync" above.
- **Webhook retries/duplicates**: handled by the loop-prevention idempotency rules above; Google's own delivery guarantees are explicitly "at least once, not exactly once", so this is not an edge case to special-case, it's the normal operating assumption for every branch in `_apply_event_change`.

### Free-tier / local-dev considerations

No new paid services: watch channels, `events.list` with `syncToken`, and `channels.stop` are all part of the same free Google Calendar API quota already used by every prior phase. Realtime is a built-in feature of the Supabase project already in use, not an add-on. The reconciliation fallback deliberately avoids interval polling — it only runs when a client actually opens/foregrounds the calendar panel or the user explicitly asks. This local development environment has no public HTTPS URL, so `GOOGLE_CALENDAR_WEBHOOK_URL` is unset here and every watch-channel code path is exercised only against a mocked Google API in tests — see "Known issues" in `/docs/progress.md`.

### Testing strategy

`tests/test_calendar_sync.py` — 24 integration tests against the real Supabase project, every Google call mocked: new/updated/deleted external events, app-created-event moved/deleted externally (including the `needs-attention` surface and confirming a delete is never silently recreated), loop prevention (an echoed `updated` timestamp is a no-op), duplicate webhook notifications, missed-webhook-then-reconciliation, invalid `syncToken` → full-resync recovery, watch-channel registration/renewal (including the old channel being stopped), sync skipping gracefully on revoked authorization, cross-tenant isolation of both the external-event cache and the needs-attention list, and the webhook endpoint's own security checks (unknown channel, wrong token, the `sync`-state handshake, and a valid notification actually triggering a background sync). A full human-interactive push-notification round trip (a real Google server calling this app's webhook) was not exercised live in this environment — same category of limitation as every prior phase's live-OAuth-consent step, and for the same reason: no public HTTPS endpoint / real browser available here.

## Mobile MVP

Phase 8 is a client-side rebuild of the Flutter app around a real product flow (Onboarding → Sign in → Today → Add work → AI prioritization → Plan my day/week → Review → Apply to Google Calendar → live state) rather than a screen per backend resource. Two small, narrowly-scoped backend additions support it; everything else reuses the existing API surface unchanged.

### New endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/tasks/prioritized` | Tasks left-joined with their latest AI result (`?status=`, `?limit=`, `?offset=`), ordered by `priority_score` desc, nulls last. Avoids an N+1 fetch (one `/tasks` call plus one `/ai-result` call per task) just to render a priority-sorted list. Never calls Gemini. |
| `GET` | `/tasks/schedule/items?start=&end=&task_id=` | Applied schedule items overlapping a date range, joined with the task's title and latest priority score and the Calendar mapping's `google_event_id`/`sync_status`. The single source of truth the Today, Calendar, and task-detail screens all read "what's actually on my plan" from. `task_id` narrows to one task's item (used by the task detail screen with a wide ±365-day window instead of requiring the client to guess a narrow one). |

Both are read-only, tenant-scoped via the same `get_tenant_context` dependency as every other route, and registered ahead of `/tasks/{task_id}` / after the schedule router's existing static paths so the literal segments (`prioritized`, `items`) are matched before the `{task_id}` pattern could swallow them — the same ordering rule already established for `/tasks/schedule` in `app/main.py`.

### Screen structure and navigation

Five tabs behind a `NavigationBar` (`lib/src/navigation/root_shell.dart`), each kept alive in an `IndexedStack` so switching tabs doesn't force a reload: **Today · Tasks · Plan · Calendar · Settings**. Feature folders keep the established `data/` (models + API client) / `presentation/` (screens/widgets) shape:

- `lib/src/onboarding/` — a one-time, three-page carousel gating first launch (flag persisted via `shared_preferences`, checked by `AuthGate` before the auth/session logic runs at all).
- `lib/src/auth/` — sign in/up/forgot-password (existing Supabase Auth SDK usage, restyled) plus a new `ResetPasswordScreen`, wired to `AuthChangeEvent.passwordRecovery`.
- `lib/src/today/` — the home screen: greeting, focus-time-available card, high-priority tasks, and a merged chronological agenda of applied schedule items + external Calendar events for the day.
- `lib/src/tasks/` — `AddWorkScreen` (natural-language primary input), `PrioritizedTasksScreen` (the Tasks tab), `TaskDetailScreen` (full detail + Edit/Complete/Reschedule/Delete), and the existing `AiPrioritizationSection`/`TaskFormScreen`.
- `lib/src/scheduling/` — `PlanScreen`: Plan my day/week → review (grouped by day for the week view) → Apply to Google Calendar → progress → success summary, replacing the old embedded `SchedulePanel`.
- `lib/src/calendar/` — `CalendarScreen`: a single day's agenda (Google events + AI-created work blocks + external busy periods, merged) plus connection/sync status and a manual "Sync now" — deliberately not a month/week grid.
- `lib/src/settings/` — account, Google Calendar connection + sync status, and read-only working-hours/timezone display (see "What's read-only" below).

### Design system

`lib/src/design/` is the one place layout/color/type decisions live, so screens don't each invent their own spacing or palette: `tokens.dart` (a fixed spacing/radius scale), `app_theme.dart` (a single seed color, a tuned `TextTheme`, and consistent component shapes — no per-screen `ThemeData` overrides), `format.dart` (shared greeting/date/time/duration formatting), and `widgets/` (`EmptyState`, `ErrorState`/`InlineErrorBanner`, `SkeletonBox`/`SkeletonList` for loading states, `PriorityBadge`, `SectionLabel`). No new package was added for any of this — the pulsing skeleton loader is a plain `AnimationController`, not a shimmer library, and typography is tuned system fonts, not a bundled/network font.

### What's read-only (and why)

Settings shows working hours and timezone for transparency, but neither is editable from this phase: working hours are still `SchedulingConstraints`' fixed server-side default (per-user configuration was explicitly out of scope through Phase 7 and remains so — see `/docs/progress.md`), and the timezone shown is the connected Google Calendar's own `calendar_timezone` (the same value the scheduling engine already uses), not a separate user preference. Building a real settings-write flow for either would be a backend feature, not a mobile-UI one, and wasn't required to support this phase's UX.

### Testability: injectable HTTP clients

`TaskApiClient`, `ScheduleApiClient`, and `CalendarApiClient` each take an optional `http.Client` (defaulting to a real one) alongside the existing optional `AuthRepository`. This is what lets widget tests swap in `package:http/testing.dart`'s `MockClient` and assert on real request/response handling — including error states — without any network. Screens that use these clients (`AddWorkScreen`, `TaskDetailScreen`, `AiPrioritizationSection`, `PlanScreen`, `CalendarScreen`, `TodayScreen`, `PrioritizedTasksScreen`) accept the same clients as optional constructor parameters for the same reason. Screens that also subscribe to Supabase Realtime additionally take an `enableRealtime` flag (default `true`), since a real `CalendarRealtimeSubscription` needs an initialized Supabase client and opens a real websocket — neither of which a hermetic widget test should depend on.

### Demo data

`database/seeds/seed.sql` includes a small, realistic task set (a client proposal, a presentation, competitor research, documentation, a team-meeting prep) with real-looking natural-language titles, varied deadlines, and varied estimated durations — deliberately not placeholder/lorem-ipsum text, so the Today/Tasks/Plan screens have something worth demoing immediately after prioritizing and planning against them.

### Testing strategy

47 Flutter tests across `mobile/test/`: pure-logic unit tests for `Format` and `priorityTierFor`; widget tests for onboarding (page advance/skip), sign-in (validation, sign-in/sign-up success and failure, forgot-password), task creation (`AddWorkScreen`: empty-input validation, natural-language title capture, example-chip fill, API-error display), task detail (loaded/error states, Complete, scheduled-time/Calendar-status display), planning (proposal display with priority/reason, missing-write-scope prompt, apply-success summary, empty-proposal and error states), and Calendar (not-connected/connected states, sync-now, needs-attention banner, error state) — all against a `MockClient`, never a real network. See `/mobile/README.md` for how to run them and the exact list.

