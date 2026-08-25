# web

Next.js (App Router) + TypeScript dashboard for the AI Work Planner.

## Status

Foundation + auth + task management + AI prioritization + Google Calendar (read + incremental-OAuth write + two-way sync) + timezone-aware scheduling proposals + apply-to-calendar. `src/app/page.tsx` switches between a minimal `AuthForm` and `TaskList` based on Supabase session state: list, create, inline edit, complete, delete, with loading/empty/error states, plus an `AiPrioritizationPanel` ("Prioritize with AI") in the inline edit form, a `CalendarConnectionPanel` (connect/status/disconnect + last-synced time/live-sync status + a "Sync now" action + a 7-day busy-interval preview), and a `SchedulePanel`: "Plan my day" → review the proposed schedule → **Apply to Google Calendar** (prompting a "Connect Calendar permissions" incremental-OAuth step first if needed) → progress → a per-item success/failure summary, plus a "Needs attention" banner for any applied task whose Calendar event was deleted externally. Both panels refresh live via Supabase Realtime whenever the backend's Calendar sync changes something — no polling. Intentionally simple UI (plain HTML form elements, inline styles) — the polished dashboard is a later phase.

## Prerequisites

- Node.js 20.x LTS
- npm 10.x

## Setup

```bash
cd web
npm install
cp .env.example .env.local   # fill in real values, never commit .env.local
```

## Development

```bash
npm run dev
```

## Build

```bash
npm run lint
npm run typecheck
npm run build
```

## Conventions

- `src/app/` — routes (Next.js App Router)
- `src/components/` — shared UI components (`AuthForm.tsx`, `TaskList.tsx`, `AiPrioritizationPanel.tsx`, `CalendarConnectionPanel.tsx`, `SchedulePanel.tsx`)
- `src/lib/supabase/` — Supabase clients (browser + server variants; see below) plus `realtime.ts` (subscribes to Postgres changes on the Calendar-sync tables so `CalendarConnectionPanel`/`SchedulePanel` refetch live instead of polling)
- `src/lib/api/` — typed client for the FastAPI backend: `client.ts` (shared `request()`/`ApiError`), `tasks.ts`, `calendar.ts`, `scheduling.ts`, `types.ts`; follow this shape for future resources
- `src/middleware.ts` — refreshes the Supabase session cookie on every request
- Environment variables are read via `process.env`, prefixed `NEXT_PUBLIC_` only when needed in the browser.
- Only the Supabase anon/publishable key ever goes in `NEXT_PUBLIC_*` — never the service role key. Business-data writes go through the FastAPI backend, not directly from this app.
