# AI Work Planner

AI-assisted task prioritization and scheduling SaaS. Users enter tasks, AI (Gemini) understands and prioritizes them, the scheduling engine builds an optimized plan around deadlines and calendar availability, and the plan is synced to Google Calendar and back to mobile/web in real time.

**Status:** foundation, auth, task management (create/view/edit/complete/delete), Gemini-powered AI prioritization, Google Calendar integration (read-only browsing plus write-scope event creation), a deterministic timezone-aware scheduling engine (`POST /tasks/schedule`, proposal-only), an "Apply Schedule" flow that writes the approved proposal to Google Calendar (`POST /tasks/schedule/apply`, idempotent, partial-failure-safe), and two-way Calendar synchronization (Google push notifications + incremental sync feed changes back into the app, propagated to both clients live via Supabase Realtime, with a manual-sync fallback and no polling) are implemented end to end across backend, web, and mobile. On top of that, the **mobile app** — the primary product deliverable — has a complete, polished MVP flow: onboarding, sign in, a Today home screen, natural-language task capture, AI prioritization, Plan my day/week, review, apply to Google Calendar, and live task/calendar state, all backed by the same API. Team calendars, automatic rescheduling, recurring-event intelligence, and a polished web dashboard are not yet built — see [`/docs/progress.md`](docs/progress.md) for what's done vs. planned.

## Architecture overview

Two thin clients (Flutter mobile, Next.js web) talk to a single FastAPI backend, which is the only component that calls Gemini, Google Calendar, and enforces business rules. Data lives in Supabase-hosted PostgreSQL; Supabase also provides Auth and Realtime sync. No microservices, queues, or Kubernetes in the MVP.

Full details, data flow, and technology rationale: [`/docs/architecture.md`](docs/architecture.md).

## Repository structure

```text
/mobile     Flutter app (iOS/Android)
/web        Next.js + TypeScript web dashboard
/backend    FastAPI backend
/database   SQL migrations + seed data (Supabase Postgres)
/docs       Architecture, ADRs, progress log
/infra      Local Docker development environment
/.github    CI workflows
```

Each app (`mobile`, `web`, `backend`) has its own `README.md` and `.env.example`.

## Local prerequisites

- [Flutter SDK](https://docs.flutter.dev/get-started/install) (stable channel)
- [Node.js](https://nodejs.org/) 20.x LTS + npm
- [Python](https://www.python.org/) 3.11+
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (optional, for the local Compose stack)
- A [Supabase](https://supabase.com/) project (for hosted Postgres/Auth once those phases start)
- Xcode (iOS builds) / Android Studio (Android builds), as needed

> Note: Docker was not available in this project's dev environment, so the `infra/docker-compose.yml` stack is written to standard conventions but not run-verified here — see [`/docs/progress.md`](docs/progress.md#known-issues). Everything else (`flutter analyze`/`test`, `npm run lint`/`typecheck`/`build`, `pytest`/`ruff`, and the task API) has been verified live against a real Supabase project.

## Initial setup

1. Create a [Supabase](https://supabase.com/) project. From Project Settings, collect:
   - Project URL
   - Anon/publishable key (safe for clients)
   - Service role/secret key (**backend only**, never share with mobile/web)
   - JWKS URL: `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`
   - A database connection string — prefer the **connection pooler** (`aws-0-<region>.pooler.supabase.com:6543`, username `postgres.<project-ref>`) over the direct `db.<ref>.supabase.co:5432` host, which is IPv6-only on some networks. URL-encode special characters in the password (`@` → `%40`).

2. Apply the database migration (see [`/database/README.md`](database/README.md)):

   ```bash
   cd database
   supabase link --project-ref your-project-ref
   supabase db push
   ```

3. Install and configure each app:

   ```bash
   git clone <this-repo>
   cd "AI Task Prioritization App"

   # Mobile
   cd mobile && flutter pub get && cp .env.example .env && cd ..

   # Web
   cd web && npm install && cp .env.example .env.local && cd ..

   # Backend
   cd backend
   python -m venv .venv
   .venv\Scripts\activate      # Windows; use `source .venv/bin/activate` on macOS/Linux
   pip install -r requirements-dev.txt
   cp .env.example .env
   cd ..
   ```

4. Fill in real Supabase values in each `.env`/`.env.local` file (URL + anon key for `mobile`/`web`; URL + service role key + JWKS URL + `DATABASE_URL` for `backend`).

5. For AI prioritization, get a free-tier [Gemini API key](https://aistudio.google.com/apikey) and set `GEMINI_API_KEY` in `backend/.env` (`GEMINI_MODEL` defaults to `gemini-3.1-flash-lite`). Without it, `/tasks/{id}/prioritize` returns a clean `502` rather than crashing — everything else works fine.

6. For Google Calendar, create a Google Cloud OAuth client (Web application type, Calendar API enabled, no billing required) and set `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`/`GOOGLE_OAUTH_REDIRECT_URI` plus a generated `OAUTH_STATE_SECRET` and `TOKEN_ENCRYPTION_KEY` in `backend/.env`. Full step-by-step Cloud Console setup: [`/docs/architecture.md`](docs/architecture.md#google-calendar-integration). Apply `database/migrations/0002_calendar_tokens.sql`, `database/migrations/0003_calendar_write_scope.sql`, and `database/migrations/0004_calendar_sync.sql` first.

7. (Optional) For live Google Calendar push notifications, set `GOOGLE_CALENDAR_WEBHOOK_URL` in `backend/.env` to a real public HTTPS URL Google can reach (e.g. via a deployed backend, or an `ngrok`/similar tunnel in dev) pointing at `/calendar/webhook`. Leave it unset to skip push notifications entirely — the app falls back to explicit `POST /calendar/sync` calls (on calendar-panel mount + a manual "Sync now" button), with no functional loss beyond not being instant. See [`/docs/architecture.md`](docs/architecture.md#two-way-calendar-synchronization).

**Never commit real secrets** — only `.env.example` files are tracked in git; every app's `.gitignore` excludes `.env`/`.env.local`.

### Auth model at a glance

Signup/login/logout/password-reset are handled by the Supabase Auth SDK directly from `mobile`/`web` — the backend is not in that path. A database trigger auto-creates a personal tenant (workspace) and owner membership for every new user. The FastAPI backend verifies the caller's Supabase JWT (via JWKS) on every request and independently re-checks tenant membership in Postgres before trusting any tenant id a client sends — see [`/docs/architecture.md`](docs/architecture.md#auth-architecture) for the full flow and [`/docs/decisions.md`](docs/decisions.md) (ADR-006 through ADR-008) for why.

### Task management

Both clients sign the user in via Supabase, then call the FastAPI task API (`/tasks`) with the resulting access token — create, list (with status filter), view, edit, complete, and delete. See [`/docs/architecture.md`](docs/architecture.md#task-api) for the endpoint table.

### AI prioritization

An explicit "Prioritize with AI" action on a task calls `POST /tasks/{id}/prioritize`, which sends the task's title/details to Gemini (`gemini-3.1-flash-lite`, free tier, structured JSON output only) and stores category/urgency/importance/priority/confidence/duration/reasoning in `task_ai_results`. It's never called automatically — not on task creation, not on list/refresh, not on a schedule. See [`/docs/architecture.md`](docs/architecture.md#gemini-integration) for the model, structured output schema, scoring logic, and cost strategy.

### Google Calendar

A "Connect Google Calendar" action starts a standard OAuth authorization-code flow handled entirely by the backend — neither client ever sees a Google client secret, refresh token, or access token. Once connected, both clients can view connection status, a busy-interval preview (`GET /calendar/availability`), and disconnect. Calendar access starts read-only; tapping "Apply to Google Calendar" triggers an incremental OAuth upgrade (same connection, no disconnect/reconnect) that adds the `calendar.events` write scope. See [`/docs/architecture.md`](docs/architecture.md#google-calendar-integration) for the OAuth flow, required Google Cloud setup, scopes, and token security approach.

### Scheduling

A "Plan my day" action calls `POST /tasks/schedule`, which combines each task's Gemini priority/duration with Google Calendar availability — using the connected calendar's own timezone, not UTC — through a deterministic, dependency-free engine (`app/services/scheduling.py`); Gemini never picks a timestamp, and nothing is written to Google Calendar or the database, the response is a proposal for the user to review. Tapping **Apply to Google Calendar** calls `POST /tasks/schedule/apply`, which independently revalidates every item (still free, still before the task's deadline, no overlap) before creating each event, persists the resulting event mapping, and is safe to retry — re-submitting an already-applied item is reported as `already_applied` rather than creating a duplicate. A batch reports per-item results (`Created: 2 / Failed: 1` style) so a partial failure is never presented as a full success. See [`/docs/architecture.md`](docs/architecture.md#scheduling-engine) and its "Apply Schedule" section for the algorithm, OAuth upgrade flow, event mapping, and idempotency design.

### Two-way Calendar synchronization

Changes made directly in Google Calendar now flow back into the app: a Google push notification (or, as a $0-cost fallback with no polling, an explicit `POST /calendar/sync` call when a client opens the calendar panel or the user taps "Sync now") triggers an incremental sync using Google's `syncToken` mechanism. An app-created event that gets moved externally updates the corresponding task's schedule; one that gets deleted is never silently recreated — it's flagged (`GET /tasks/schedule/needs-attention`) so the user can re-apply it. A genuinely external event (never created by this app) is cached as a normalized busy block, never turned into a task. Every resulting database change reaches both clients live via Supabase Realtime (no manual refresh, no application-level "publish" step — the tables involved are simply in the `supabase_realtime` publication with the same RLS policies that already scope them to tenant members). See [`/docs/architecture.md`](docs/architecture.md#two-way-calendar-synchronization) for the watch-channel lifecycle, `syncToken`/410-recovery strategy, event-mapping rules, and how sync loops are prevented by construction.

## Development commands

| App | Run | Test | Build |
|---|---|---|---|
| mobile | `flutter run` (in `/mobile`) | `flutter test` | `flutter build apk` / `flutter build ios` |
| web | `npm run dev` (in `/web`) | `npm run lint && npm run typecheck` | `npm run build` |
| backend | `uvicorn app.main:app --reload` (in `/backend`) | `pytest` | `docker build .` |

Or run backend + web + a local Postgres together:

```bash
cd infra
docker compose up --build
```

## Database migrations

```bash
cd database
supabase link --project-ref your-project-ref
supabase db push
```

See [`/database/README.md`](database/README.md) for conventions.

## Documentation

- [`/docs/architecture.md`](docs/architecture.md) — system architecture, component responsibilities, data flow, technology rationale, MVP vs. future
- [`/docs/decisions.md`](docs/decisions.md) — architecture decision record (ADR) log
- [`/docs/progress.md`](docs/progress.md) — completed work, current phase, remaining phases, known issues
