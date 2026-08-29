# AI Work Planner

**AI-assisted task prioritization and scheduling.** Enter a task in plain English, Gemini reads it and assigns a priority, category, confidence score, and time estimate, and a deterministic scheduling engine builds a plan around your deadlines and real Google Calendar availability — synced back to Calendar (and to every signed-in device) automatically.

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue.svg)](LICENSE.md)
[![Backend CI](https://github.com/athulnairrr/AI-Task-Prioritization-App/actions/workflows/backend-ci.yml/badge.svg)](../../actions/workflows/backend-ci.yml)
[![Mobile CI](https://github.com/athulnairrr/AI-Task-Prioritization-App/actions/workflows/mobile-ci.yml/badge.svg)](../../actions/workflows/mobile-ci.yml)
[![Web CI](https://github.com/athulnairrr/AI-Task-Prioritization-App/actions/workflows/web-ci.yml/badge.svg)](../../actions/workflows/web-ci.yml)

> **License note:** this repository is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE.md) — free to use, modify, and share for noncommercial purposes; commercial use requires a separate license from the copyright holder.

---

## Screenshots

All screenshots below are captured live from the Flutter app running on a physical Android device (Pixel 10), signed in against the real backend and a real Gemini/Google Calendar connection — not mocked or staged.

| | |
|---|---|
| ![Onboarding](docs/screenshots/01_onboarding.png) **Onboarding** | ![Sign in](docs/screenshots/02_sign_in.png) **Sign in** |
| ![Today](docs/screenshots/03_today.png) **Today** — focus time, high-priority tasks, merged agenda | ![Tasks](docs/screenshots/04_tasks_categories.png) **Tasks** — category filters, AI priority/confidence |
| ![Add work](docs/screenshots/05_add_work.png) **Add Work** — natural-language task capture | ![Task detail](docs/screenshots/06_task_detail_ai_calendar.png) **Task detail** — AI prioritization + live Calendar sync status |
| ![Plan](docs/screenshots/08_plan_proposal.png) **Plan** — AI-proposed schedule from real Calendar availability | ![Calendar](docs/screenshots/07_calendar_synced.png) **Calendar** — connected, merged agenda, AI-scheduled block |

## What it does

1. **Capture** — type a task in plain English (e.g. *"Prepare the quarterly tax filing by next Friday"*); no separate fields required.
2. **Prioritize** — tap "Prioritize with AI" and Gemini returns a priority score, confidence, category, effort estimate, and a one-line reasoning — an explicit, user-triggered call, never automatic.
3. **Plan** — "Plan my day/week" combines every prioritized task with real Google Calendar free/busy data through a deterministic (non-AI) scheduling engine, and returns a reviewable proposal.
4. **Apply** — approving the proposal creates real Google Calendar events, records the resulting event IDs, and is safe to re-run (idempotent, partial-failure-safe).
5. **Sync** — changes made directly in Google Calendar (moved/deleted events) flow back into the app via push notifications + incremental sync, and every change propagates to all signed-in devices live through Supabase Realtime — no manual refresh, no polling.

## Status

Backend, mobile, and web are implemented end to end: auth, task management, Gemini-powered prioritization, Google Calendar OAuth (read + incremental write scope), a timezone-aware scheduling engine, an idempotent "Apply Schedule" flow, and two-way Calendar synchronization with live cross-client propagation. The **Flutter mobile app** is the primary deliverable and has a complete, polished MVP flow (onboarding → sign-in → Today → Add Work → Prioritize → Plan → Apply → Calendar → Settings). The web client is a functional but intentionally minimal dashboard. Team calendars, subscription billing/usage metering, and a production deployment target are not yet built.

See [`/docs/progress.md`](docs/progress.md) for the full phase-by-phase history and what remains, and [`/docs/decisions.md`](docs/decisions.md) for the architecture decision log (28 ADRs).

## Architecture overview

Two thin clients (Flutter mobile, Next.js web) talk to a single FastAPI backend, which is the only component that calls Gemini, Google Calendar, and enforces business rules. Data lives in Supabase-hosted PostgreSQL; Supabase also provides Auth and Realtime sync. No microservices, queues, or Kubernetes in the MVP.

Full details, data flow, and technology rationale: [`/docs/architecture.md`](docs/architecture.md).

## Repository structure

```text
/mobile     Flutter app (iOS/Android)
/web        Next.js + TypeScript web dashboard
/backend    FastAPI backend
/database   SQL migrations + seed data (Supabase Postgres)
/docs       Architecture, ADRs, progress log, screenshots
/infra      Local Docker development environment
/.github    CI workflows
```

Each app (`mobile`, `web`, `backend`) has its own `README.md` and `.env.example`.

## Local prerequisites

- [Flutter SDK](https://docs.flutter.dev/get-started/install) (stable channel)
- [Node.js](https://nodejs.org/) 20.x LTS + npm
- [Python](https://www.python.org/) 3.11+
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (optional, for the local Compose stack)
- A [Supabase](https://supabase.com/) project (hosted Postgres/Auth/Realtime)
- Xcode (iOS builds, macOS only) / Android Studio (Android builds), as needed

## Setup (≈15–20 minutes)

1. Create a [Supabase](https://supabase.com/) project. From Project Settings, collect:
   - Project URL
   - Anon/publishable key (safe for clients)
   - Service role/secret key (**backend only**, never share with mobile/web)
   - JWKS URL: `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`
   - A database connection string — prefer the **connection pooler** (`aws-0-<region>.pooler.supabase.com:6543`, username `postgres.<project-ref>`) over the direct `db.<ref>.supabase.co:5432` host, which is IPv6-only on some networks. URL-encode special characters in the password (`@` → `%40`).

2. Apply the database migrations (see [`/database/README.md`](database/README.md)):

   ```bash
   cd database
   supabase link --project-ref your-project-ref
   supabase db push
   ```

3. Install and configure each app:

   ```bash
   git clone https://github.com/athulnairrr/AI-Task-Prioritization-App.git
   cd AI-Task-Prioritization-App

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

6. For Google Calendar, create a Google Cloud OAuth client (Web application type, Calendar API enabled, no billing required) and set `GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`/`GOOGLE_OAUTH_REDIRECT_URI` plus a generated `OAUTH_STATE_SECRET` and `TOKEN_ENCRYPTION_KEY` in `backend/.env`. Full step-by-step Cloud Console setup: [`/docs/architecture.md`](docs/architecture.md#google-calendar-integration). Apply `database/migrations/0002_calendar_tokens.sql`, `0003_calendar_write_scope.sql`, and `0004_calendar_sync.sql` first.

7. (Optional) For live Google Calendar push notifications, set `GOOGLE_CALENDAR_WEBHOOK_URL` in `backend/.env` to a real public HTTPS URL Google can reach (e.g. a deployed backend, or an `ngrok`-style tunnel in dev) pointing at `/calendar/webhook`. Leave it unset to skip push notifications — the app falls back to explicit `POST /calendar/sync` calls (on calendar-panel mount + a manual "Sync now" button), with no functional loss beyond not being instant.

**Never commit real secrets** — only `.env.example` files are tracked in git; every app's `.gitignore` excludes `.env`/`.env.local`.

### Auth model at a glance

Sign-up/sign-in/logout/password-reset are handled by the Supabase Auth SDK directly from `mobile`/`web` — the backend is not in that path. A database trigger auto-creates a personal tenant (workspace) and owner membership for every new user. The FastAPI backend verifies the caller's Supabase JWT (via JWKS) on every request and independently re-checks tenant membership in Postgres before trusting any tenant id a client sends. See [`/docs/architecture.md`](docs/architecture.md#auth-architecture) and ADR-006–ADR-008 in [`/docs/decisions.md`](docs/decisions.md).

### Task management

Both clients sign the user in via Supabase, then call the FastAPI task API (`/tasks`) with the resulting access token — create, list (with status filter), view, edit, complete, and delete. See [`/docs/architecture.md`](docs/architecture.md#task-api).

### AI prioritization

An explicit "Prioritize with AI" action on a task calls `POST /tasks/{id}/prioritize`, which sends the task's title/details to Gemini (`gemini-3.1-flash-lite`, free tier, structured JSON output only) and stores category/urgency/importance/priority/confidence/duration/reasoning. It's never called automatically. See [`/docs/architecture.md`](docs/architecture.md#gemini-integration).

### Google Calendar

A "Connect Google Calendar" action starts a standard OAuth authorization-code flow handled entirely by the backend — neither client ever sees a Google client secret, refresh token, or access token. Calendar access starts read-only; applying a schedule triggers an incremental OAuth upgrade (same connection, no disconnect/reconnect) that adds the `calendar.events` write scope. See [`/docs/architecture.md`](docs/architecture.md#google-calendar-integration).

### Scheduling

"Plan my day" calls `POST /tasks/schedule`, combining each task's Gemini priority/duration with real Calendar availability (in the connected calendar's own timezone) through a deterministic, dependency-free engine — Gemini never picks a timestamp, and nothing is written until the proposal is approved. "Apply to Google Calendar" (`POST /tasks/schedule/apply`) independently revalidates every item before creating each event, records the event mapping, and is safe to retry. See [`/docs/architecture.md`](docs/architecture.md#scheduling-engine).

### Two-way Calendar synchronization

Changes made directly in Google Calendar flow back into the app via push notifications (or an explicit `POST /calendar/sync` fallback, no polling). A moved app-created event updates its task's schedule; a deleted one is flagged, never silently recreated. Genuinely external events are cached as busy blocks, never turned into tasks. Every resulting change reaches all signed-in clients live via Supabase Realtime. See [`/docs/architecture.md`](docs/architecture.md#two-way-calendar-synchronization).

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

## License

Source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE.md). You may use, modify, and distribute this code freely for any noncommercial purpose (personal, educational, research, evaluation). **Commercial use is not permitted** without a separate license from the copyright holder — see [`LICENSE.md`](LICENSE.md) for the full terms and contact details.
