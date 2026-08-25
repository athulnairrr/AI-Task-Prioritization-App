# mobile

Flutter app (iOS/Android) for the AI Work Planner — the primary product deliverable (the client requires simultaneous iOS + Android builds). Created via `flutter create` (org `com.aiplanner`).

## Status

A complete, polished MVP flow: **Onboarding → Sign in → Today → Add work → AI prioritization → Plan my day/week → Review → Apply to Google Calendar → live task/calendar state.** Five tabs (Today · Tasks · Plan · Calendar · Settings) behind a bottom `NavigationBar`. Auth (sign in/up/forgot password/reset), tasks (natural-language add, prioritize with AI, complete, edit, delete, reschedule), scheduling (plan my day/week, review, apply to Google Calendar with incremental OAuth), and Google Calendar (connect, live status, manual sync, external events, needs-attention) are all implemented and wired to the FastAPI backend — the mobile app never talks to Gemini, Google, or Postgres directly, and never duplicates business logic (revalidation, scheduling, sync) that already lives server-side. See `/docs/architecture.md` § "Mobile MVP" for the full design writeup and `/docs/progress.md` for phase-by-phase history.

## Prerequisites

- Flutter SDK (stable channel), currently developed against 3.44.x
- Xcode (for iOS builds) / Android Studio + SDK (for Android builds)

## Setup

```bash
cd mobile
flutter pub get
cp .env.example .env   # fill in real values, never commit .env
```

## Run

```bash
flutter run
```

## Build

```bash
flutter analyze
flutter test
flutter build apk        # Android
flutter build ios        # iOS (macOS + Xcode required)
```

## Screen structure and navigation

`lib/src/navigation/root_shell.dart` is the signed-in app's root: a `NavigationBar` with five destinations, each screen kept alive in an `IndexedStack` (switching tabs doesn't reload the screen; each screen still refreshes itself independently on realtime/lifecycle events).

| Tab | Screen | What it does |
|---|---|---|
| Today | `lib/src/today/presentation/today_screen.dart` | Home screen: greeting, focus-time-available, high-priority tasks, a merged agenda of AI-scheduled blocks + Calendar events, needs-attention banner. |
| Tasks | `lib/src/tasks/presentation/prioritized_tasks_screen.dart` | Every open task, sorted by AI priority, with priority/confidence/deadline/duration and a one-line AI explanation. FAB opens Add Work. |
| Plan | `lib/src/scheduling/presentation/plan_screen.dart` | "Plan my day" / "Plan my week" → review the proposal → Apply to Google Calendar → progress → success summary. |
| Calendar | `lib/src/calendar/presentation/calendar_screen.dart` | One day's agenda (Google events + AI-created blocks + external busy periods, merged), connection/sync status, manual "Sync now". |
| Settings | `lib/src/settings/presentation/settings_screen.dart` | Account, Google Calendar connection/sync status, read-only working-hours/timezone, sign out. |

Outside the tabs: `lib/src/onboarding/` (first-launch carousel, gates everything else via a `shared_preferences` flag), `lib/src/auth/presentation/` (`SignInScreen`, `ResetPasswordScreen`), `lib/src/tasks/presentation/add_work_screen.dart` (natural-language task creation) and `task_detail_screen.dart` (full detail + Edit/Complete/Reschedule/Delete), reached by pushing a route from Today/Tasks/Plan rather than being tabs themselves.

`AuthGate` (`lib/src/auth/presentation/auth_gate.dart`) is the very first router: onboarding-not-seen → `OnboardingScreen`; else, watches Supabase auth state — `AuthChangeEvent.passwordRecovery` → `ResetPasswordScreen`; signed in → `RootShell`; signed out → `SignInScreen`.

## Design conventions

`lib/src/design/` is the single place layout/color/type decisions live — screens read from here rather than hardcoding their own:

- `tokens.dart` — a fixed `Spacing`/`Corners` scale.
- `app_theme.dart` — one seed color (`AppTheme.light`), a tuned `TextTheme`, consistent shapes for cards/buttons/inputs/chips/nav bar. `SemanticColors` holds the priority-tier and attention/success colors used outside Material's default error/success mapping.
- `format.dart` — shared greeting/date/time/duration formatting (`Format.greeting`, `Format.dayHeading`, `Format.time`, `Format.timeRange`, `Format.duration`).
- `widgets/` — `EmptyState`, `ErrorState`/`InlineErrorBanner`, `SkeletonBox`/`SkeletonListTile`/`SkeletonList` (loading placeholders — a plain `AnimationController` pulse, not a shimmer package), `PriorityBadge` (+ `priorityTierFor`), `SectionLabel`.

No new UI/animation packages were added for any of this. General conventions, unchanged from earlier phases:

- `lib/src/core/` holds cross-cutting setup: `env.dart` (`.env` loading), `supabase_client.dart` (the Supabase SDK instance), `calendar_realtime.dart` (the Postgres-changes subscription helper used by Today/Tasks/Calendar).
- Feature folders follow `data/` (model + API client) / `presentation/` (screens/widgets) — same shape for `auth`, `tasks`, `calendar`, `scheduling`, `settings`, `today`, `onboarding`.
- Environment/config values are read from `.env` via `flutter_dotenv` (never hardcoded, never committed). `.env` must exist (copied from `.env.example`) before `flutter run`/`flutter build` — it's declared as a Flutter asset.
- Only the Supabase anon/publishable key ever goes in this app's `.env` — never the service role key, never a Google OAuth client secret, never a Google token (the backend never returns one).

## Backend integration

Every read/write of tasks, AI results, scheduling, and Calendar data goes through the FastAPI backend with the caller's Supabase access token — the mobile app never calls Gemini, Google, or Postgres directly, and never re-implements backend logic (task prioritization, schedule revalidation, Calendar sync) client-side. Supabase Auth is used only for authentication/session management, exactly as in earlier phases. `TaskApiClient`, `ScheduleApiClient`, `CalendarApiClient` (`lib/src/*/data/*_api_client.dart`) are the only things that call the backend.

## Realtime

`lib/src/core/calendar_realtime.dart` wraps `supabase_flutter`'s existing `RealtimeChannel` API (already a dependency for auth — no new package needed) to subscribe to Postgres changes on the tables the backend's Calendar sync writes to (`schedule_items`, `google_calendar_event_mappings`, `google_calendar_external_events`, `google_calendar_connections`). Today, Tasks, and Calendar all use it to refetch automatically when something changes on the backend — no polling, and no full-app rebuild (each screen just reloads its own data).

## Google Calendar connect flow (mobile)

Tapping "Connect Google Calendar" (Settings, or Calendar) fetches an authorization URL from the backend and opens it in an **external browser** (`url_launcher`) — Google's consent screen never renders inside the app. After the user finishes there, the backend shows a plain "you can close this window" page; this app doesn't use a deep link to detect that automatically yet (see `/docs/decisions.md` ADR-015) — it re-checks connection status when the app resumes (switching back) and via a manual refresh. Functional, not seamless; a deep-link upgrade is tracked in `/docs/progress.md`. Tapping **Apply to Google Calendar** on the Plan tab follows the same external-browser pattern when the connection still needs the `calendar.events` write scope — `getWriteScopeConnectUrl()` requests `GET /calendar/connect?scope=write`, the existing read-only scopes are preserved (same connection row, no disconnect).

## Plan my day/week and Apply Schedule

`PlanScreen` calls `ScheduleApiClient.proposeSchedule(horizonStart:, horizonEnd:)` with today's or the next 7 days' range for "Plan my day"/"Plan my week", then, on Apply, sends the reviewed items as-is (task id, start, end) to `POST /tasks/schedule/apply` — the backend, not this client, decides what's actually still valid. The result renders as `created`/`already_applied` (re-applying is safe, never duplicates)/`failed` counts plus a per-item tile with a reason on failure. `TaskDetailScreen`'s "Reschedule" action uses the same endpoint for a single task (`applySingleItem`), via a date+time picker.

## Two-way Calendar sync

`CalendarApiClient.syncCalendar()` calls `POST /calendar/sync` — the manual reconciliation/renewal call, since push notifications need a public HTTPS webhook URL this app itself has no part in. `ScheduleApiClient.listNeedsAttention()` calls `GET /tasks/schedule/needs-attention` to populate the "Needs attention" banners on Today and Calendar; `CalendarApiClient.getExternalEvents()` calls `GET /calendar/external-events` for the cached busy-block list.

## Local testing

```bash
flutter analyze
flutter test
```

47 tests across `mobile/test/`, mirroring `lib/`'s structure:

- `test/design/` — pure-logic unit tests (`Format`, `priorityTierFor`) plus `PriorityBadge` rendering.
- `test/onboarding/` — page advance, Skip.
- `test/auth/` — sign-in/sign-up validation, success, and failure states, forgot-password, using a `_FakeAuthRepository` that overrides the network-touching methods (no real Supabase call).
- `test/tasks/` — `AddWorkScreen` (empty-input validation, natural-language title capture, example-chip fill, API-error display) and `TaskDetailScreen` (loaded/error states, Complete, scheduled-time/Calendar-status display).
- `test/scheduling/` — `PlanScreen` (proposal display with priority/reason, missing-write-scope prompt, apply-success summary, empty-proposal and error states).
- `test/calendar/` — `CalendarScreen` (not-connected/connected states, sync-now, needs-attention banner, error state).
- `test/widget_test.dart` — top-level smoke test (onboarding-then-sign-in, and onboarding-shown-first).

**Why these are real tests, not screenshots of a happy path:** `TaskApiClient`/`ScheduleApiClient`/`CalendarApiClient` all take an optional injectable `http.Client` (default: a real one); tests pass `package:http/testing.dart`'s `MockClient` and assert on real request/response handling, including error responses. Screens that own these clients take the same clients as optional constructor parameters (default: real instances) for the same reason. Screens that also subscribe to Supabase Realtime additionally take an `enableRealtime` flag (default `true`, set `false` in tests) — a real subscription needs an initialized Supabase client and opens a real websocket, neither of which a hermetic widget test should depend on. `test/helpers/fake_auth_repository.dart` provides a fixed-token `AuthRepository` for these tests, built from a standalone `SupabaseClient` (`autoRefreshToken: false`, so it never starts a background token-refresh timer that would trip `testWidgets`' "pending timer" check). See ADR-028 in `/docs/decisions.md`.

## Demo data

`database/seeds/seed.sql` seeds a small, realistic task set (a client proposal, a presentation, competitor research, documentation, a team-meeting prep) with real natural-language titles, varied deadlines, and varied durations — not placeholder text — so Today/Tasks/Plan have something worth demoing right after signing in. To try the full flow locally: create the demo Supabase Auth user (see the file's own instructions), run the seed, sign in, tap "Prioritize with AI" on a couple of tasks, then "Plan my day" on the Plan tab.

## Known issue: Windows + OneDrive

If this repo lives inside a OneDrive-synced folder on Windows, `flutter analyze` / `flutter test` / `flutter pub get` can intermittently fail with:

```
Flutter failed to delete a directory at ".../ios/Flutter/ephemeral/Packages/.packages"
```

This is OneDrive briefly locking a newly-written file, not a code issue. Workaround: delete the offending directory (`mobile/ios/Flutter/ephemeral`, or `mobile/build`) and re-run the command.
