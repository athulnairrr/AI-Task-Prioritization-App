# Progress Log

Update this file after every major implementation phase.

## Current phase

**Phase 8: Flutter Mobile MVP Experience** — complete.

## Android release build (2026-08-25, operational task, not a numbered phase)

Prepared and built the mobile app as a release APK for direct installation on a physical Android device (Pixel 10) — no new product features, only the build/runtime configuration fixes needed to make a release build actually work on real hardware.

- **Found and fixed a real release-blocking bug**: `android/app/src/main/AndroidManifest.xml` (the manifest merged into every build variant, including release) never declared `android.permission.INTERNET` — only the debug/profile manifests did (Flutter's own template puts it there for the dev protocol, not for the app's own HTTP calls). Every release build before this fix would have installed and launched fine but failed **every** network call (Supabase auth, the FastAPI backend) with a permission-denied socket error, with no build-time warning. Added the permission to the main manifest with a comment explaining why. (Caught a second, trivial bug while fixing it: an XML comment containing `--` fails Android's manifest parser — `SAXParseException: The string "--" is not permitted within comments" — fixed by rewording.)
- **`mobile/.env`** (gitignored, not committed): `API_BASE_URL` changed from `http://localhost:8000` (meaningless on a physical device — resolves to the device itself) to the dev machine's LAN IP, `http://192.168.0.140:8000`, so a Pixel 10 on the same Wi-Fi network can reach the backend running on this machine. `SUPABASE_URL`/`SUPABASE_ANON_KEY` (the anon/publishable key — safe by design) were already correct; `GOOGLE_OAUTH_CLIENT_ID` is blank (Calendar connect isn't required to exercise the core MVP flow).
- **Verified no secrets are bundled**: `.env` is a Flutter asset (declared in `pubspec.yaml`), so its contents are literally embedded in the APK, extractable by unzipping it — confirmed by unzipping the built APK and inspecting `assets/flutter_assets/.env` directly, plus a pattern scan (`sb_secret_`, `GOCSPX-`, `AIzaSy`, `service_role`, a non-empty `GEMINI_API_KEY`) across every extracted file. Only the Supabase URL, the Supabase **anon/publishable** key, and the backend base URL are present — no Gemini key, no Google OAuth client secret, no Supabase service-role key, consistent with every prior phase's rule that only the anon key ever goes in a client `.env`.
- **Build**: `flutter build apk --release` succeeded — `mobile/build/app/outputs/flutter-apk/app-release.apk`, **51.7 MB** (54,218,380 bytes). Confirmed via `aapt dump badging`: `applicationId=com.aiplanner.mobile`, `versionCode=1`, `versionName=1.0.0`, `minSdkVersion=24`, `targetSdkVersion=36`, `INTERNET` permission present. Release build is still signed with the debug keystore (`signingConfig = signingConfigs.getByName("debug")` in `android/app/build.gradle.kts`, a pre-existing `TODO` in the Flutter template) — fine for sideloading onto a personal device, **not** suitable for Play Store distribution (needs a real release keystore first).
- **Installation was not tested from this session at build time** (no physical device reachable from the sandbox) — see the follow-up below for what happened once the user installed it on their own Pixel 10 and, later, on an Android Studio emulator this session could actually drive.
- APK not committed — no documented release-artifact convention exists in this repo (`.gitignore` already excludes Flutter build output), and one wasn't added; this stays a locally-built artifact.

### Follow-up: on-device testing, two more localhost traps, and a real Today/Calendar bug (2026-08-25, same day)

The user sideloaded the APK onto their Pixel 10 (same Wi-Fi network as this machine) and worked through it live with this session:

- **Windows Firewall blocked the phone from reaching the backend.** The Wi-Fi network was classified `Public` by Windows, which blocks unsolicited inbound connections by default — even from another device on the same network. Fixed by the user adding an inbound rule for TCP 8000 (`New-NetFirewallRule ... -LocalPort 8000 -Action Allow -Profile Any`, run as Administrator; this agent has no admin rights in this environment and could not add it directly).
- **Supabase's email-confirmation link is a second "localhost" trap.** After sign-up, the confirmation link redirects to whatever the Supabase project's Site URL is configured as — `http://localhost:3000` here (the web app's dev URL) — which fails to load on the phone. Harmless: confirmation itself happens server-side against Supabase *before* that final redirect, so the account is already confirmed by the time the phone shows "site can't be reached." Verified directly via `auth.users.confirmed_at` rather than guessing.
- **`GOOGLE_OAUTH_REDIRECT_URI` was the same trap a third time**, for the Google Calendar connect flow specifically: `backend/.env` had it set to `http://localhost:8000/calendar/callback`, which would fail on the phone's browser the same way. Changed to `http://192.168.0.140:8000/calendar/callback` and the backend restarted to pick it up (`Settings` is `@lru_cache`d, so a running process doesn't see an `.env` edit until restart). **This redirect URI must also be added to the OAuth client's "Authorized redirect URIs" in Google Cloud Console**, or Google rejects it with `redirect_uri_mismatch` — that's a one-time step only the project owner can do (this agent has no Google Cloud Console access).
- **Google's OAuth consent screen blocked the connect attempt** with "Access blocked: AI Work Planner has not completed the Google verification process" — expected for an OAuth client still in Testing publishing status; only accounts explicitly added as **Test users** (Google Cloud Console → APIs & Services → OAuth consent screen) can complete consent while in that status. Also only fixable by the project owner, not this agent.
- **Found and fixed a real bug**, not a config issue: the Today and Calendar screens each load 3-4 API calls in one `Future.wait(...)` and treated *any* single failure as a hard error for the whole screen. `GET /calendar/external-events` correctly 404s ("Calendar not connected") for a user who hasn't connected Calendar yet — an expected, common state, not an error — but that one 404 was enough to make the entire screen show "Could not load today's plan. Pull down to retry." for every brand-new user, every time, with no way to fix it since Settings/Calendar (where "Connect" lives) were equally broken by the same bug. Fixed in `today_screen.dart` and `calendar_screen.dart` by having that one call degrade to an empty list on failure instead of failing the whole `Future.wait`; added a regression test to `test/calendar/calendar_screen_test.dart` (now asserting a real 404, not a 200 with an empty body, matching what the backend actually returns) and a new `test/today/today_screen_test.dart`. All 48 Flutter tests pass; `flutter analyze` clean.
- **Verified live**, not just via widget tests: rebuilt the release APK with the fix, installed it on a locally-driven Android Studio emulator (`emulator-5554`, reachable from this sandbox via `adb`/`flutter devices` — the first on-device access this session actually had), created a fully-confirmed test account via the Supabase Admin API (`qa.emulator.aiplanner@gmail.com` — the public sign-up endpoint's own email-domain deliverability check rejects `@example.com`, and repeated sign-up attempts while diagnosing that hit Supabase's email rate limit, so the Admin API path — the same one used for this project's other demo accounts — was used instead of retrying the public endpoint), signed in, and confirmed both Today (focus-time card + "Nothing on the calendar yet") and Calendar ("Google Calendar not connected" + "Nothing scheduled") now render correctly with no error, where before the fix both showed the same generic failure. Screenshots were captured via `adb exec-out screencap` at each step to confirm actual rendered state, not assumed from logs alone.
- Not yet confirmed: whether the *physical* Pixel 10, after the firewall rule and both redirect-URI/env fixes, now loads Today/Calendar correctly too (the emulator verification above used the same fixed build, so it's expected to, but hasn't been separately confirmed on the physical device by the user as of this writing).

### Follow-up 2: full end-to-end flow test on the emulator, one more real bug, and an environment limitation (2026-08-25, same day)

Continued the live verification further into the app's core flow, same emulator/build as above:

- **Add Work → task creation → AI prioritization, verified live end-to-end**: typed a real task ("Finish the client proposal by Friday") through the natural-language field, submitted, landed on the task detail screen, tapped "Prioritize with AI," and got a genuine Gemini response rendered correctly (Priority 95, Confidence 95%, Category work, Est. 120 min, real reasoning text). Confirms the whole create → prioritize path works on-device, not just in mocked tests.
- **Found and fixed a real (non-blocking) UI bug**: after prioritizing a task from its detail screen and navigating back, the Tasks list still showed "Not prioritized" for that task until a manual pull-to-refresh — the list wasn't invalidating/refetching after the mutation. Confirmed via direct comparison that the data itself was correct (pull-to-refresh immediately showed Priority 95/95%/2h), so this is a stale-cache/missing-refresh issue, not a persistence bug. Left as a known minor issue (not fixed this session — discovered near the end of the session after touch input broke; see below) — worth a small fix (refresh the Tasks list on return from the task detail screen, e.g. via a result callback or a shared refresh signal) next time this screen is touched.
- **Confirmed the Today/Calendar fix survives a cold app restart**, not just the original session: relaunched the app fresh (`monkey -p com.aiplanner.mobile ...`) and Today rendered correctly on first paint with no error, same as immediately after sign-in.
- **Hit an environment limitation, not an app bug, and could not get further**: partway through the flow (attempting to reach the Plan tab), `adb shell input tap`/`input swipe`/`monkey --pct-touch` all stopped producing any visible effect on the emulator — confirmed via repeated screenshots showing no state change, and ruled out being an app-level freeze by checking `dumpsys window`/`dumpsys input` (window focus was correctly on the app throughout) and by testing taps against unrelated system UI (status bar) and a system-level keyevent (`KEYCODE_APP_SWITCH`, which should always trigger the recent-apps overview regardless of the foreground app) — neither had any effect either. Restarting the adb server and a full `adb reboot` of the emulator (with a clean relaunch afterward) did not resolve it. This looks like the sandboxed environment's connection to the emulator's touch-injection pipeline wedging, not anything wrong in the Flutter app.
- **Not yet exercised as a result**: Plan tab (Plan my day / Plan my week), reviewing a proposed schedule, "Apply to Google Calendar" (expected to hit the still-outstanding Google Cloud Console Test-user/redirect-URI gaps from Follow-up 1), and Sign out. These remain open for a future session — either with a working emulator input pipeline, or via the user testing manually on their physical Pixel 10 (which already has the same fixed build).

## Completed work

### Phase 0 (foundation), Phase 1 (schema + auth), Phase 2 (task management), Phase 3 (Gemini prioritization), Phase 4 (Google Calendar integration), Phase 5 (scheduling engine), Phase 6 (Apply Schedule), Phase 7 (two-way Calendar sync) — complete, see summaries below.

### Phase 8

The mobile app is the primary product deliverable (client requires simultaneous iOS + Android builds); this phase rebuilds it around a real product flow instead of a screen per backend resource, while touching the backend only where the UX genuinely needed it.

- **Two minimal new backend endpoints** (ADR-026): `GET /tasks/prioritized` (tasks + latest AI result, avoiding an N+1 fetch) and `GET /tasks/schedule/items?start&end&task_id` (applied schedule items + task title/priority/Calendar mapping status for a date range, optionally one task). Both read-only, tenant-scoped, registered ahead of `/tasks/{task_id}`'s path pattern. No other backend changes this phase.
- **Navigation**: a 5-tab `NavigationBar` (Today · Tasks · Plan · Calendar · Settings) over an `IndexedStack` (`lib/src/navigation/root_shell.dart`) — each tab keeps its state instead of rebuilding on switch.
- **Onboarding**: a 3-page carousel (`lib/src/onboarding/`), shown once (flag in `shared_preferences`), gating `AuthGate` before sign-in.
- **Auth**: `SignInScreen` restyled (same Supabase Auth SDK calls); new `ResetPasswordScreen` wired to `AuthChangeEvent.passwordRecovery` (functional, same deep-link gap as ADR-015 — documented in the screen's own docstring).
- **Today** (`lib/src/today/`): greeting + date, a focus-time-available card (computed client-side from applied schedule items + external events against a 9am–6pm window), high-priority tasks, a merged chronological agenda (AI-scheduled blocks + Calendar events), and a needs-attention banner.
- **Add Work** (`lib/src/tasks/presentation/add_work_screen.dart`): one natural-language text field is the primary interaction (becomes the task title), with due-date/duration/description tucked behind a collapsed "Add details" — not a traditional form up front. Navigates straight into the new task's detail screen, where "Prioritize with AI" is the obvious next action.
- **Prioritized Tasks** (the Tasks tab): every open task sorted by priority, showing priority/confidence/deadline/duration and a one-line AI explanation per card.
- **Plan** (`lib/src/scheduling/presentation/plan_screen.dart`, replacing the old embedded `SchedulePanel`): "Plan my day" / "Plan my week" → review (grouped by day for the week view, each item showing time/priority/reason) → Apply to Google Calendar (prompting the incremental-OAuth write-scope upgrade if needed) → progress → a ✓/✗ per-item success summary.
- **Calendar** (`lib/src/calendar/presentation/calendar_screen.dart`): a single day's merged agenda (Google events + AI-created work blocks + external busy periods) plus connection/last-synced/watch-active status and a manual "Sync now" — deliberately not a full calendar grid.
- **Task detail** (`lib/src/tasks/presentation/task_detail_screen.dart`): title/description/deadline/duration/priority/confidence/AI reasoning/scheduled time/Calendar status, with Edit/Complete/Reschedule (date+time picker → `POST /tasks/schedule/apply` for that one task, server-revalidated same as a batch apply)/Delete actions.
- **Settings** (`lib/src/settings/presentation/settings_screen.dart`): account + sign out, Google Calendar connection/sync status, and read-only working-hours/timezone display (ADR-027 — neither is an editable preference anywhere in the backend yet; this phase surfaces them honestly rather than adding a settings-write feature not required by the brief).
- **Design system** (`lib/src/design/`): one seed color + tuned `TextTheme` (`app_theme.dart`), a fixed spacing/radius scale (`tokens.dart`), shared date/time/duration formatting (`format.dart`), and reusable `EmptyState`/`ErrorState`/`SkeletonBox`/`PriorityBadge`/`SectionLabel` widgets — no new packages (the skeleton loader is a plain `AnimationController`, not a shimmer library).
- **Testability refactor** (ADR-028): `TaskApiClient`/`ScheduleApiClient`/`CalendarApiClient` now take an optional injectable `http.Client`; the screens that own them take the same clients as optional constructor params (defaulting to real ones); realtime-subscribing screens additionally take an `enableRealtime` flag. Enables real widget tests via `package:http/testing.dart`'s `MockClient` with no new test-mocking dependency.
- **Demo data**: `database/seeds/seed.sql` now seeds a realistic task set (client proposal, presentation, competitor research, documentation, team-meeting prep) with real deadlines/durations instead of placeholder text.
- **Tests**: 47 Flutter tests across 9 files (`mobile/test/`) — pure-logic (`Format`, `priorityTierFor`), onboarding, sign-in (validation + success/failure states), task creation, task detail (load/error/Complete/scheduled-Calendar-status), planning (proposal display, missing-write-scope prompt, apply summary, empty/error states), and Calendar (not-connected/connected/sync-now/needs-attention/error). `flutter analyze`: no issues. Backend: two new hermetic-pattern integration test files (`test_prioritized_tasks.py`, `test_schedule_items.py`) written and passing `ruff`, but **not verified live** this phase — see Known issues (sandbox clock skew).
- `docs/architecture.md`: added "Mobile MVP" section. `docs/decisions.md`: ADR-026 through ADR-028. `mobile/README.md`: rewritten for the new screen structure, navigation, design conventions, local testing, and demo flow.

## Mobile MVP quick reference

| Method | Path | Notes |
|---|---|---|
| GET | `/tasks/prioritized` | Tasks + latest AI result, sorted by priority desc. Powers Today's high-priority list and the Tasks tab. |
| GET | `/tasks/schedule/items?start&end&task_id` | Applied schedule items in a range (or for one task) + title/priority/Calendar status. Powers Today, Calendar, and task detail. |

### Phase 7

- **Watch channels** (`app/services/calendar_sync.py` `ensure_watch_channel()`): registers a Google Calendar `events.watch` push-notification channel per connection, opportunistically (after `/calendar/callback`, and at the top of every `POST /calendar/sync`) — a no-op unless the channel is missing or within 24h of expiring. Requires `GOOGLE_CALENDAR_WEBHOOK_URL` (a real public HTTPS endpoint); unset in this local environment, so watch registration is exercised only against a mocked Google API in tests, never live. Old channels are best-effort stopped (`channels.stop`) when renewed or on disconnect.
- **Migration** `database/migrations/0004_calendar_sync.sql`: `sync_token`/`last_synced_at`/`watch_channel_id`/`watch_resource_id`/`watch_token`/`watch_expires_at` added to `google_calendar_connections`; `google_updated_at` added to `google_calendar_event_mappings`; `needs_attention`/`attention_reason` added to `schedule_items`; new `google_calendar_external_events` cache table (RLS + column-level grants, same discipline as every prior migration); `'deleted'` added to the `calendar_sync_status` enum; and `schedule_items`/`google_calendar_event_mappings`/`google_calendar_external_events`/`google_calendar_connections` added to the `supabase_realtime` publication.
- **Webhook endpoint** `POST /calendar/webhook` (ADR-023): no user JWT — trusts only a channel id/resource id/token match against what was stored at registration, never the request body (Google's push notifications never carry the changed event itself). Always returns `200`, including for an unrecognized or mismatched channel, to avoid both leaking valid channel ids and inviting Google retry storms.
- **Incremental sync** `app/services/calendar_sync.sync_connection()`: Google's `syncToken` mechanism — a bounded full sync (`calendar_sync_window_days_past`/`_future`, default -7d/+90d) when there's no token yet, an incremental `events.list?syncToken=...` call otherwise. `410 Gone` (invalid token) triggers the documented recovery: clear the token, redo as a full sync, store the new token. A Postgres advisory lock serializes concurrent syncs of the same connection.
- **Event mapping** (ADR-022, ADR-023): every synced event is looked up by `(connection_id, google_event_id)` against `google_calendar_event_mappings` first, not by trusting its own `extendedProperties`. App-created event moved externally → `schedule_items.starts_at`/`ends_at` updated. Deleted externally → mapping marked `'deleted'`, `schedule_items.needs_attention`/`attention_reason` set, never silently recreated (surfaced via `GET /tasks/schedule/needs-attention`). Genuinely external events are normalized and cached in `google_calendar_external_events` as busy blocks, never turned into a task. A rare self-heal path adopts an untracked-but-tagged event into a mapping if the app's own insert crashed before recording it.
- **Loop prevention** (ADR-022): the sync logic never writes back to Google in response to a detected external change — only the explicit, user-initiated `POST /tasks/schedule/apply` ever does — so the specific cycle the brief warns about has no write-back step to react to. What remains (duplicate/replayed webhook notifications) is handled via `google_calendar_event_mappings.google_updated_at`: an incoming event whose `updated` timestamp isn't strictly newer than what's already recorded is a no-op.
- **Realtime propagation** (ADR-025): the four tables this phase writes to are in the `supabase_realtime` publication and already RLS-scoped to tenant members — no application-level "publish" call needed, the backend's normal writes are what the realtime engine reads from Postgres's logical replication stream. Both clients subscribe (`web/src/lib/supabase/realtime.ts`, `mobile/lib/src/core/calendar_realtime.dart`) and refetch on any change.
- **Reconciliation fallback, no polling** (ADR-024): `POST /calendar/sync` renews the watch channel if needed and runs one sync pass inline — called by both clients when the calendar panel mounts, plus a manual "Sync now" action. This is also exactly how a missed webhook notification gets caught up; there's no code-level distinction between a webhook-triggered sync and a reconciliation sync.
- **`GET /tasks/schedule/needs-attention`**: lists schedule items flagged by an externally-deleted event, so both clients can surface "this task's calendar event was deleted — re-apply to recreate it" instead of the deletion silently vanishing.
- **Web UI**: `CalendarConnectionPanel` shows live-sync status + last-synced time + a "Sync now" action; `SchedulePanel` shows a "Needs attention" banner. Both subscribe to realtime changes and refetch automatically.
- **Mobile UI**: same additions, Flutter widgets (`CalendarConnectionSection`, `SchedulePanel`), realtime via `supabase_flutter`'s existing channel API (already a dependency, no new package needed).
- **Tests**: 23 new backend integration tests (`tests/test_calendar_sync.py`, real Supabase project + mocked Google) — new/updated/deleted external events, app-created-event moved/deleted (+ needs-attention surfaced, never silently recreated), loop prevention, duplicate webhook, missed-webhook-then-reconciliation, invalid-syncToken/410 recovery, watch-channel registration/renewal, revoked-authorization graceful skip, cross-tenant isolation (external-event cache and needs-attention list), and webhook security (unknown channel, wrong token, sync-handshake, valid notification triggering a background sync). **192 passed, 1 skipped, full suite.** No live push-notification round trip was exercised (needs a public HTTPS URL + a real Google server calling in — not available in this environment, same category of limitation as every prior phase's live-OAuth-consent step).
- `docs/architecture.md`: added "Two-way Calendar synchronization" section. `docs/decisions.md`: ADR-022 through ADR-025.

## Two-way sync quick reference

| Method | Path | Notes |
|---|---|---|
| POST | `/calendar/sync` | Renews the watch channel if needed, runs one sync pass inline. The reconciliation fallback + missed-webhook recovery. |
| POST | `/calendar/webhook` | Google's push-notification callback. No user JWT; trusted via channel id/resource id/token match. |
| GET | `/calendar/external-events?start&end` | Cached, normalized busy blocks from events this app didn't create. |
| GET | `/tasks/schedule/needs-attention` | Schedule items whose Google event was deleted externally. |

### Phase 6

- **Timezone fix** (`backend/app/services/scheduling.py`, ADR-018): the Phase 5 UTC-only working-hours limitation is fixed — `SchedulingConstraints.working_hours_timezone` (IANA name) is resolved via direct `datetime(..., tzinfo=ZoneInfo(tz))` construction, verified correct across a real DST transition by a dedicated test. Source of truth is the connected Google Calendar's own `timeZone` (fetched via `calendars.get`, cached on `google_calendar_connections.calendar_timezone`), not a guess. Discovered and fixed a genuine environment gap along the way: neither this Windows dev machine nor `python:3.11-slim` (the Docker image) ship a system IANA tz database, so stdlib `zoneinfo` needs the `tzdata` pip package — added as a normal dependency.
- **Migration** `database/migrations/0003_calendar_write_scope.sql`: `calendar_timezone` and `granted_scopes` columns added to `google_calendar_connections`; same column-level grant discipline as `0002` (new columns explicitly listed for client `SELECT`, nothing implicit).
- **Incremental OAuth write scope** (ADR-019): `GET /calendar/connect?scope=write` requests `calendar.events` in addition to the existing read scopes (`include_granted_scopes=true` upgrades the same connection row in place — no disconnect/reconnect). `granted_scopes` (Google's own report of what was actually granted, from the token response) is stored and checked via `ConnectionRecord.has_write_scope`; `GET /calendar/connection` now returns `has_write_access`/`calendar_timezone`.
- **Apply endpoint** `POST /tasks/schedule/apply` (`backend/app/api/scheduling.py`, `app/services/schedule_apply.py`): authenticated, tenant-scoped. Checks write scope up front (`403 CALENDAR_WRITE_SCOPE_REQUIRED` if missing) and refresh-token validity (`409 REAUTH_REQUIRED`), then independently revalidates every item — task ownership, deadline, a freshly-requeried free/busy snapshot, and overlap with other items in the same batch — before creating anything. Client-supplied start/end is a request, never a fact (ADR-021).
- **Google event creation**: `google_calendar.create_event()` sets title/description/start/end/timeZone plus `extendedProperties.private` (`app`, `task_id`, `tenant_id`, `schedule_item_id`) for app-only metadata, invisible in Google's UI. Never touches any event it didn't just create.
- **Idempotency** (ADR-020): a `google_calendar_event_mappings` row is only ever inserted *after* `create_event()` succeeds, using the real Google-assigned event id — a failed attempt leaves the `schedule_items` row (so a retry updates it) but no mapping (since `google_event_id` is `NOT NULL` + unique per connection, there's no safe sentinel for "failed, no id yet"). Applying the same task twice returns `already_applied` with zero Google calls; a genuinely failed item can be retried without duplicating a sibling that already succeeded.
- **Partial failure**: every item processed independently; `ScheduleApplyResult` reports `created`/`already_applied`/`failed` counts plus a per-item reason. Verified by test with a deliberately-flaky mock (item 2 of 3 fails) asserting the exact per-item status order.
- **Plan auto-creation** (`backend/app/services/plans.py`, ADR-021): `get_or_create_default_plan()` transparently creates one `"My Plan"` row per tenant so `schedule_items.plan_id` (`NOT NULL`) has somewhere to attach — no plan-management feature was built, just enough to satisfy the existing schema.
- **Web UI**: `SchedulePanel` now supports the full flow — Plan my day → Review Schedule → (Connect Calendar permissions, if needed) → Apply to Google Calendar → progress → success summary with ✓/✗ per item.
- **Mobile UI**: same flow, Flutter widgets, incremental-auth write-scope connect via `url_launcher` (same external-browser pattern as Phase 4's read-only connect, ADR-015).
- **Tests**: 44 new backend tests — 4 hermetic timezone tests (including the DST-boundary one) + 18 mocked integration tests (`test_schedule_apply.py`: successful creation w/ metadata & timezone assertions, duplicate-apply idempotency, retry-after-failure, partial failure, revalidation rejecting stale/past-deadline slots, revoked token, missing write scope, incremental auth, cross-tenant, unauthenticated) + 1 optional live test that creates and deletes one real Calendar event (skipped without a manually-obtained write-scope refresh token — full human OAuth consent still isn't scriptable, same limitation as Phase 4). **169 passed, 1 skipped**, full suite.
- `docs/architecture.md`: added "Apply Schedule" section (timezone strategy, OAuth write-scope flow, apply endpoint + revalidation, event metadata, idempotency, partial failure, cost, testing). `docs/decisions.md`: ADR-018 through ADR-021.

## Apply Schedule quick reference

| Method | Path | Notes |
|---|---|---|
| `GET` | `/calendar/connect?scope=write` | Incremental OAuth: adds calendar.events to the existing connection |
| `POST` | `/tasks/schedule/apply` | `{items: [{task_id, start, end}]}` → `{created, already_applied, failed, results}`. Revalidates everything; writes real Calendar events. |

Full details, revalidation rules, idempotency, and partial-failure behavior: `/docs/architecture.md` § Apply Schedule.

### Phase 5

- **Scheduling engine** (`backend/app/services/scheduling.py`): pure, deterministic, zero I/O — no Gemini, no Google, no database inside it. Deterministic greedy/ranked-slot algorithm (not a solver, per the brief): free-interval computation (working hours minus busy intervals, per day), sort tasks by (priority DESC, deadline ASC, task_id), walk them in order picking the best-scoring valid candidate and consuming it from the free pool. `SchedulingConstraints` is a single dataclass threading every default (working hours 09:00–18:00 UTC, 30-min minimum block, scoring bonuses) through the algorithm — nothing hardcoded inline. See ADR-016.
- **Scoring**: `score = min(100, priority_score + earliest_slot_bonus(+3) + snug_fit_bonus(+2))` — priority dominates by design; bonuses reflect slot quality (earliest available, tight fit vs. fragmenting a larger block). Every scheduled item carries a plain-English `reason` built from the same inputs, not templated boilerplate.
- **API**: `POST /tasks/schedule` (`backend/app/api/scheduling.py`) — authenticated, tenant-scoped, accepts explicit `task_ids` or auto-selects every `status='pending'` task with at least one AI result. Loads tasks + latest AI results + calendar availability (reusing Phase 4's connection/token machinery), runs the engine, returns a proposal. **Writes nothing** — no `schedule_items` row, no Google Calendar event, matching "return a proposal first."
- **Verified against the product brief's exact example**, not hand-calculated: Tuesday 10:00–12:00, score 99 (94 priority + earliest + snug-fit bonuses) — see `/docs/architecture.md` § Scheduling engine for the real engine output.
- **Incremental OAuth write-scope groundwork** (`backend/app/services/google_calendar.py`, ADR-017): `READ_ONLY_SCOPES` (unchanged, still all any route requests) and `WRITE_SCOPES` (+ `calendar.events`) now both defined; `build_authorization_url()` takes an optional `scopes` param and always sends `include_granted_scopes=true`, so a future "Apply Schedule" phase can request the write scope as an incremental upgrade to the existing connection — no disconnect/reconnect, no change to this phase's default (read-only) connect flow.
- **Web UI**: `SchedulePanel` — "Plan my day" button, proposed schedule (title/time/priority/score/reason), unscheduled tasks with reasons, "Apply Schedule (coming soon)" disabled button.
- **Mobile UI**: `SchedulePanel` — same functionality, Flutter widgets.
- **Tests**: 34 new — 25 hermetic engine tests (`tests/test_scheduling.py`: working-hour boundaries, one task/one slot, multiple tasks, priority ordering, deadline ordering including no-deadline and mid-interval-truncation cases, exact-fit, duration-longer-than-slot, insufficient availability, overlapping busy intervals, never-overlapping placement, snug-fit preference, determinism, configurable constraints) + 9 integration tests (`tests/test_schedule_api.py`: unauthenticated, validation, not-connected, end-to-end proposal, no-free-slot, missing-duration, unknown-task-id, cross-tenant, auto-select scoping). **147/147 backend tests passing.**
- **Real bug found and fixed during this phase**: `app/api/scheduling.py` initially imported `query_freebusy`/`GoogleApiError` by name (`from app.services.google_calendar import ...`) instead of importing the module and calling `google_calendar.query_freebusy(...)` — this silently broke `monkeypatch`-based test isolation (the route kept calling the *real* function even after a test patched the module attribute), a subtle bug the other Phase 4 route file (`calendar.py`) didn't have because it already used the module-qualified pattern. Caught immediately by 3 failing tests; fixed by switching to the same module-qualified import style used elsewhere.
- `docs/architecture.md`: added "Scheduling engine" section (algorithm, scoring model, constraints/defaults, failure cases, API contract, verified example, incremental OAuth strategy, cost, testing strategy). `docs/decisions.md`: ADR-016 (deterministic greedy scheduler), ADR-017 (incremental OAuth).

## Scheduling API quick reference

| Method | Path | Notes |
|---|---|---|
| `POST` | `/tasks/schedule` | `{task_ids?, horizon_start?, horizon_end?}` → `{scheduled: [...], unscheduled: [...]}`. Writes nothing. |

Full details, algorithm, scoring model, and a verified worked example: `/docs/architecture.md` § Scheduling engine.

- **OAuth flow** (`backend/app/api/calendar.py`, `app/services/google_calendar.py`, `app/core/oauth_state.py`): full authorization-code flow. `GET /calendar/connect` (authenticated) mints a signed, 10-minute `state` and returns Google's authorization URL; `GET /calendar/callback` (no auth header available — hit directly by Google's redirect) verifies `state`, exchanges the code, fetches the connected account's email, and stores the connection. CSRF via signed `state` (ADR-013), not a database table. Denied consent, invalid/expired state, and a missing refresh token all show a friendly HTML page with no DB write.
- **Token security** (`backend/app/core/crypto.py`, ADR-014): `refresh_token`/`access_token` encrypted with Fernet before every write, keyed by `TOKEN_ENCRYPTION_KEY`. `access_token` is treated as a refresh-on-demand cache (`get_valid_access_token()` in `app/services/calendar_connections.py`), not a persisted secret — most requests are served from cache, refreshed only within 60s of expiry. Never logged, never in any API response, and direct Supabase client `SELECT` on both token columns is revoked at the database level (`0002_calendar_tokens.sql`) — enforced twice, not just in application code.
- **Migration** `database/migrations/0002_calendar_tokens.sql`: `access_token` made nullable; `status` (`connected`/`reauth_required`/`error`) and `last_error` columns added; column-level grants tightened so `authenticated` can never `SELECT` the token columns, even encrypted, via a direct Supabase query.
- **API**: `GET /calendar/connection`, `GET /calendar/connect`, `GET /calendar/callback`, `DELETE /calendar/connection` (best-effort revoke at Google, always deletes locally), `GET /calendar/calendars`, `GET /calendar/events?start=&end=`, `GET /calendar/availability?start=&end=`. All tenant-scoped like the task API; cross-tenant access verified impossible by test.
- **REAUTH_REQUIRED handling**: a rejected refresh token (revoked/expired) updates the connection's stored `status` to `reauth_required` (not just a one-off error response) and every calendar-data route returns `409 {"detail": {"code": "REAUTH_REQUIRED", ...}}` instead of a generic failure — verified by test that the DB status actually changes, not just the response.
- **Calendar data model** (`backend/app/services/calendar_events.py`): raw Google events/free-busy normalized into `CalendarEventOut`/`BusyIntervalOut` — only event_id/title/start/end/all_day/status/is_recurring, dropping everything else Google returns (attendees, conference data, description, location, ...).
- **Direct REST via httpx, not `google-api-python-client`** (ADR-012) — smaller footprint, async-native, sufficient for this phase's narrow read-only surface.
- **Web UI**: `CalendarConnectionPanel` — connect button (redirects to the real Google consent screen), status, disconnect, a 7-day busy-interval preview.
- **Mobile UI**: `CalendarConnectionSection` — same functionality via `url_launcher` (external browser) since full deep-link return-to-app wasn't built this phase (ADR-015) — status refreshes automatically on app resume plus a manual refresh button.
- **Tests**: 50 new backend tests — 5 hermetic crypto, 6 hermetic OAuth state, 8 hermetic event/free-busy normalization, 27 integration tests against the real Supabase project with every Google call mocked (connect/callback/disconnect, reconnect-upserts, cross-tenant isolation, revoked-token → REAUTH_REQUIRED with DB verification, normalized events/availability/calendars, Google 429/5xx → 429/502), and 4 live tests against the **real** Google API (registered OAuth client accepted by Google, real `invalid_grant`/`401` error shapes). **113/113 backend tests passing.**
- **Real bug found via live testing** (not caught by any mock): Google's userinfo endpoint returns `"error"` as a plain string in some responses, not always a nested object — the original error parser assumed the latter and crashed with `AttributeError`. Fixed in `app/services/google_calendar.py` to handle both shapes; live-reverified after the fix.
- **Dependency cleanup**: the user-provided Google OAuth client JSON (downloaded from Cloud Console) was moved into `backend/.env` and the raw file deleted — never committed; `.gitignore` also updated with a `client_secret*.json` backstop rule.
- `docs/architecture.md`: added "Google Calendar integration" section (OAuth flow diagram, required Cloud config, scopes, token security, API, data model, cost strategy, error handling, testing strategy). `docs/decisions.md`: ADR-012 through ADR-015.

## Calendar API quick reference

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/calendar/connection` | Required | Status: not_connected / connected / reauth_required / error |
| `GET` | `/calendar/connect` | Required | Returns the Google authorization URL |
| `GET` | `/calendar/callback` | None (trusts signed `state`) | Google's redirect target; stores the connection |
| `DELETE` | `/calendar/connection` | Required | Revokes at Google (best-effort) + deletes locally |
| `GET` | `/calendar/calendars` | Required | Connected account's calendar list |
| `GET` | `/calendar/events?start=&end=` | Required | Normalized events, max 90-day range |
| `GET` | `/calendar/availability?start=&end=` | Required | Normalized busy intervals, max 90-day range |

Full details: `/docs/architecture.md` § Google Calendar integration.

### Phase 3

- **Gemini integration** (`backend/app/services/ai.py`): `AiPrioritizationService` wraps the official `google-genai` SDK (async client), model name `gemini-3.1-flash-lite` (Phase brief's specified model, confirmed working live), read from config (`Settings.gemini_model`, env `GEMINI_MODEL`) rather than hardcoded. Structured JSON output only — Gemini's response is parsed via its own SDK-native `response.parsed` (a `GeminiTaskAnalysis` Pydantic instance), never hand-parsed free text.
- **Two-layer validation** (`backend/app/schemas/ai.py`, see ADR-011): Pydantic enforces structure (enum values, required fields, types) at parse time — a genuine failure here is a hard error, nothing is stored. A separate deterministic `clamp()` method (plain Python) then bounds `priority_score` to [0, 100], `confidence_score` to [0.0, 1.0], `estimated_minutes` to [5, 480], and truncates `reasoning` — always applied, always succeeds, independent of what Gemini actually returned.
- **API** (`backend/app/api/tasks.py`): `POST /tasks/{id}/prioritize` (the only route that calls Gemini — explicit, user-triggered, never automatic) and `GET /tasks/{id}/ai-result` (returns the latest stored result, or 404; never calls Gemini). Both require auth + tenant membership exactly like the Phase 2 task routes; a cross-tenant `/prioritize` call returns 404 *without* calling Gemini at all (verified by test — the fake AI service's call counter stays at 0).
- **Storage**: `task_ai_results` (Phase 1 schema, unchanged) — `category`/`urgency`/`priority_score`/`effort_estimate_minutes`/`reasoning` in dedicated columns; `importance`/`confidence_score` inside `raw_response` (jsonb), read back out for the API response (see ADR-011 for why no new columns were added).
- **Mobile UI**: `AiPrioritizationSection` (`mobile/lib/src/tasks/presentation/`) — a card on the task edit screen with a "Prioritize with AI" / "Re-prioritize with AI" button, loading/error/retry states, and a result view (priority, confidence, category, estimate, reasoning). Loads any existing result on open via a plain GET (no Gemini call); only calls Gemini on explicit tap.
- **Web UI**: `AiPrioritizationPanel` (`web/src/components/`) — same behavior/shape as mobile, embedded in the inline task edit form.
- **Tests**: `backend/tests/test_ai_schemas.py` (12 hermetic tests, no network — structural rejection + clamp() bounds) + `backend/tests/test_prioritize.py` (11 tests against the real Supabase project with Gemini itself replaced via `app.dependency_overrides[get_ai_service]` — deterministic, free: success, clamping, malformed output, API failure with no DB write, unauthenticated, task-not-found, cross-tenant 404 with zero Gemini calls, re-prioritization) + `backend/tests/test_prioritize_live_gemini.py` (1 optional test, real Gemini call, skipped unless `GEMINI_API_KEY` set). **63/63 backend tests passing** (24 new this phase), confirmed stable across 2 consecutive full runs including the live Gemini call.
- **Dependency cleanup**: removed the unused `supabase` Python package (never actually imported anywhere — the backend uses `asyncpg` + `pyjwt` + direct `httpx` calls instead) after it conflicted with `google-genai`'s `httpx` requirement. Bumped `pydantic` 2.7.4 → 2.13.4 (required by `google-genai`; re-verified full suite green after).
- `docs/architecture.md`: added "Gemini integration" section (model/SDK, structured output schema, scoring logic, failure handling, API, cost strategy, testing strategy). `docs/decisions.md`: ADR-011 (two-layer validation).

## AI prioritization quick reference

| Method | Path | Calls Gemini? |
|---|---|---|
| `POST` | `/tasks/{id}/prioritize` | **Yes** — the only route that does, explicit user action only |
| `GET` | `/tasks/{id}/ai-result` | No — latest stored result, or 404 |

Full details, structured output schema, and scoring logic: `/docs/architecture.md` § Gemini integration.

## Task API quick reference

| Method | Path | Notes |
|---|---|---|
| `POST` | `/tasks` | title required; description/raw_input/due_at/estimated_minutes optional |
| `GET` | `/tasks` | `?status=`, `?limit=`, `?offset=`; ordered by due_at (nulls last), then created_at |
| `GET` | `/tasks/{id}` | 404 if not found or not caller's tenant |
| `PATCH` | `/tasks/{id}` | partial update; only fields sent are changed |
| `DELETE` | `/tasks/{id}` | 204; 404 if not found/not yours |
| `POST` | `/tasks/{id}/complete` | shortcut for status → done |

Full details: `/docs/architecture.md` § Task API.

## Remaining phases (not started)

1. Team calendars, manager dashboards, subscriptions, advanced analytics, Gmail/Drive integration, complex recurring-event intelligence — explicitly out of scope through Phase 8.
2. Automatic rescheduling in response to an external Calendar change — Phase 7 detects and surfaces the change (`needs_attention`), it never re-schedules on its own.
3. Editable working hours / per-user timezone preference — Phase 8's Settings screen shows both read-only (ADR-027); making either editable is a backend feature (new settings storage + wiring the scheduling engine to read it), not yet built.
4. Mobile OAuth deep-link return (flutter_web_auth_2 + native scheme registration) — used an external-browser + manual/resume-refresh approach instead (ADR-015); upgrading needs a device/emulator to verify. The new `ResetPasswordScreen` (Phase 8) has the same gap for the password-recovery email link.
5. Per-user working-days configuration for the scheduling engine — timezone is real (Phase 6), but every day of the week is still considered a working day, a documented MVP default.
6. Editing/moving an already-applied schedule item via a batch/bulk API (Phase 8's task-detail "Reschedule" covers the single-task case via `POST /tasks/schedule/apply`; there's still no dedicated "move" endpoint distinct from "apply").
7. A real background-worker/cron process — Phase 7's watch-channel renewal and reconciliation are opportunistic (request-triggered), not scheduled, by design (ADR-024); a real worker would let renewal happen even for connections nobody is actively using the app for.
8. CI/CD deployment automation (hosting target not yet chosen) — also the blocker for a real public HTTPS webhook URL, needed for Google push notifications to work outside of tests (see Known issues).
9. Production hardening: rate limiting on AI/Calendar calls, structured error monitoring, KMS-backed token-encryption-key rotation.
10. Deciding whether any AI operations should become automatic (e.g. auto-prioritize on task creation) — explicitly deferred, not decided, per Phase 3's brief.
11. Polished web dashboard — Phase 8 was mobile-only by design (the client's primary deliverable); the web client's plain-HTML-form UI is unchanged.

## Important technical decisions

See `/docs/decisions.md` for the full ADR log (ADR-001 through ADR-028). This phase: two minimal read-joined endpoints instead of client-side aggregation (ADR-026), working hours/timezone shown read-only rather than a new editable-settings feature (ADR-027), injectable `http.Client` + `enableRealtime` flag enabling real Flutter widget tests without a mocking framework (ADR-028).

## Known issues

- **Next.js 14.2.5 → 14.2.35**: bumped to the latest 14.2.x patch in Phase 1 after a security advisory; full remediation needs Next 16 (breaking), deliberately deferred.
- **Windows + OneDrive**: `flutter analyze`/`test`/`pub get` and `npm run build` can intermittently fail with a resource-busy/directory-delete error (OneDrive briefly locking a newly-written file) inside `mobile/ios/Flutter/ephemeral/...`, `mobile/build/...`, or `web/.next/...`. Workaround: delete the offending directory and re-run. Not a code issue.
- **Backend `.env` uses the Supabase connection pooler**, not the direct `db.<ref>.supabase.co:5432` host — see Phase 1 notes; unchanged this phase.
- `TaskUpdate` in the web client's inline edit form doesn't expose `estimated_minutes` (backend supports it) — simple UI was explicitly in scope, not full field coverage.
- Gemini free-tier rate limits are not explicitly handled with a friendly message beyond the generic 502 — if a real rate limit is hit, the user sees a generic "AI prioritization is temporarily unavailable" error and can retry manually. No automatic retry/backoff (deliberately, per Phase 3's cost-protection requirement).
- **Mobile calendar OAuth (read and write) has no deep-link return** (ADR-015) — the user must manually switch back to the app (or the app auto-refreshes on resume) rather than being routed back automatically. Functional, not seamless; upgrade path documented. Phase 8's `ResetPasswordScreen` has the analogous gap for the password-recovery email.
- A full human-interactive Google consent flow was **not** tested end-to-end by this agent in any phase that needed one (not scriptable without a real browser + real Google account) — every OAuth code path *was* verified live/via mocked-Google integration tests. The one live "create a real event" test needs a manually-obtained write-scope refresh token and is skipped without one.
- **No real Google push-notification round trip was exercised** (Phase 7): registering a watch channel requires a public HTTPS URL Google can reach, which this local dev environment does not have (`GOOGLE_CALENDAR_WEBHOOK_URL` is unset here). Every watch-channel and webhook code path is verified against a mocked Google API (`tests/test_calendar_sync.py`); the app is designed to degrade gracefully without one, relying entirely on the `POST /calendar/sync` reconciliation fallback, but the actual "Google calls our real server" leg needs a deployed environment (or a tunnel like ngrok in dev) to confirm live.
- **Watch-channel renewal and sync reconciliation are opportunistic, not cron-driven** (ADR-024) — a connection nobody has opened the app for since its watch channel expired temporarily stops getting push updates until the app is next opened (at which point it self-heals automatically). Accepted tradeoff for staying on $0-cost, request-driven infrastructure with no background-worker process.
- A caught-during-development bug (Phase 5): `app/api/scheduling.py` initially imported Google functions by name instead of via the module, which silently broke test mocking. Fixed before commit.
- **Scheduling still assumes every day of the week is a working day** (no per-user working-days configuration) — timezone is real (Phase 6), that specific default remains, documented in `SchedulingConstraints`.
- `tzdata` is a real (non-dev) dependency since Phase 6 — neither this Windows dev machine nor the `python:3.11-slim` Docker base image ship a system IANA tz database; `zoneinfo` needs it even for `"UTC"`.
- **This session's sandbox clock drifted ~17 days behind real time during Phase 8** (confirmed via an external HTTP response `Date` header and a direct diagnostic showing JWT verification failing with "the token is not yet valid (iat)" and `flutter pub get`/TLS handshakes failing with `CERTIFICATE_VERIFY_FAILED: certificate is not yet valid"). This is a sandbox/environment issue, not fixable from inside the session (no permission to change the system clock) and not caused by any code change. Concretely: the two new backend endpoints (`/tasks/prioritized`, `/tasks/schedule/items`) have their integration tests written and `ruff`-clean but were **not run successfully against the live Supabase project** this phase — every other already-passing live test (confirmed by re-running a known-good file) fails the same way right now, confirming the cause is the clock, not the new code. `flutter analyze`/`test` were unaffected (cached packages, no live network needed) and all 47 mobile tests pass. Re-run `pytest tests/test_prioritized_tasks.py tests/test_schedule_items.py` once the environment's clock is correct to confirm live.

## Deferred work (intentional, not forgotten)

- Redis, message queues, Kubernetes — no proven need yet (see ADR-002).
- CI/CD deployment (only lint/build/test exist so far, no deploy step) — also what's needed to get a real public HTTPS URL for Google push notifications.
- Next.js major-version upgrade (14 → 16) to fully clear `npm audit` — deliberately deferred.
- Polished/full-featured web dashboard UI — explicitly out of scope through Phase 8 (mobile was this phase's focus).
- A deterministic weighted-scoring formula that combines/overrides Gemini's `priority_score` (e.g. with deadline proximity) — not implemented; current bounds-enforcement is the only deterministic layer (ADR-011).
- Automatic AI prioritization (e.g. on task creation) — explicitly deferred; kept an explicit user action only.
- Team calendars, manager dashboards, subscriptions, advanced analytics, Gmail/Drive integration, complex recurring-event intelligence — all out of scope through Phase 8 by design.
- Automatic rescheduling in response to a detected external Calendar change — Phase 7 surfaces it (`needs_attention`), doesn't act on it.
- Mobile deep-link return from the OAuth browser flow (ADR-015) — needs a device/emulator to verify; deferred to a later phase. Same gap applies to Phase 8's password-recovery flow.
- KMS-backed multi-key rotation for `TOKEN_ENCRYPTION_KEY` — deferred until there are enough real connections for the tradeoff to matter (ADR-014).
- A global optimization pass across all tasks (vs. the greedy, one-task-at-a-time engine) — explicitly not required by the brief; see ADR-016.
- Per-user working-days scheduling configuration — timezone is real (Phase 6); working-*days* (vs. hours) is still a fixed "every day" default.
- Editable working hours / per-user timezone preference in Settings — Phase 8 shows both read-only; see ADR-027.
- Real plan management (multiple named plans, editing, archiving) — the apply endpoint only auto-creates one default plan per tenant to satisfy the schema; see ADR-021.
- A real cron/background-worker process for watch-channel renewal — Phase 7 renewal is opportunistic/request-triggered instead; see ADR-024.

---

## Phase 0 summary (for reference)

- Monorepo structure created: `/mobile`, `/web`, `/backend`, `/database`, `/docs`, `/infra`, `/.github`.
- `mobile`: Flutter project initialized. `web`: Next.js 14 + TypeScript hand-scaffolded. `backend`: FastAPI with a `/health` endpoint.
- `infra`: `docker-compose.yml` for local dev (not run — Docker not installed on the dev machine).
- `.github/workflows`: CI for backend/web/mobile, each scoped by path.
- Root `README.md`, `/docs/architecture.md`, `/docs/decisions.md` (ADR-001 through ADR-005) written.
- Verified: `flutter analyze` clean, `npm run build`/`lint`/`typecheck` clean, `pytest`/`ruff` clean, `uvicorn` boot + live `/health` check.

## Phase 1 summary (for reference)

- Schema (`0001_init.sql`): 10 tables, 5 enums, 20 FKs, 35 indexes, `updated_at` triggers, `handle_new_user` auto-provisioning trigger (personal tenant + owner membership on signup).
- RLS enabled on all tenant-owned tables; members `SELECT` via a `SECURITY DEFINER` helper; business-table writes restricted to the backend's service role; `anon` has all grants revoked.
- Backend: JWKS-based Supabase JWT verification, `get_current_user`/`require_tenant_membership` dependencies, `GET /me`, `GET /tenants/me`, `GET /tenants/{tenant_id}`.
- Mobile/web: Supabase Auth SDK wiring (no screens yet).
- Verified live against a real Supabase project: migration applied, FKs/indexes/triggers/policies confirmed, RLS isolation (cross-tenant reads empty, direct client writes 403, no-token 401), signup/login/reset-request/logout all tested for real.

## Phase 2 summary (for reference)

- Task CRUD API (`POST/GET /tasks`, `GET/PATCH/DELETE /tasks/{id}`, `POST /tasks/{id}/complete`), tenant-scoped via a new `get_tenant_context` dependency for flat (non-path-scoped) routes.
- Mobile task feature (list/create/edit/complete/delete, loading/empty/error states) plus a minimal sign-in screen so it's reachable. Equivalent, simpler web UI.
- Found and fixed a real bug: JWT verification had zero clock-skew tolerance, causing intermittent false 401s (ADR-010).
- 39/39 backend tests passing (9 hermetic schema tests + 30 live integration tests against the real Supabase project), stable across 3 consecutive runs. Also live-smoke-tested through an actual running `uvicorn` server.

## Phase 3 summary (for reference)

- Gemini integration (`gemini-3.1-flash-lite`, official `google-genai` SDK, structured JSON output only) via `POST /tasks/{id}/prioritize` — the only route that calls Gemini, explicit user action only. `GET /tasks/{id}/ai-result` returns the latest stored result without calling Gemini.
- Two independent validation layers: Pydantic structural checks (hard failure, nothing stored) vs. deterministic `clamp()` bounds enforcement (always succeeds) — ADR-011.
- Storage reused the existing `task_ai_results` schema unchanged; `importance`/`confidence_score` live in `raw_response` jsonb rather than new columns.
- "Prioritize with AI" UI on both clients, loading/error/retry states.
- 63/63 backend tests passing (12 hermetic + 11 mocked integration + 1 real live Gemini call), stable across 2 consecutive full runs.

## Phase 4 summary (for reference)

- Full Google OAuth authorization-code flow (`connect`/`callback`/`connection`/disconnect), signed stateless `state` for CSRF (ADR-013), Fernet-encrypted tokens with access_token treated as a refresh-on-demand cache (ADR-014), direct httpx calls to Google instead of the official SDK (ADR-012).
- Migration `0002_calendar_tokens.sql`: `status`/`last_error` columns, `access_token` nullable, column-level grants revoked so direct Supabase clients can never read token columns.
- Read-only calendar API: connection status, calendars, events, availability — normalized to an internal model, never exposing raw Google payload shape.
- Real bug found via live testing (not caught by mocks): Google's userinfo endpoint returns `"error"` as a string in some cases, not always a dict — fixed and reverified live.
- Connect/status/disconnect + busy-interval preview UI on both clients; mobile uses an external browser (no deep-link return this phase, ADR-015).
- 113/113 backend tests passing (50 new: hermetic crypto/state/normalization + mocked integration + real Google API smoke tests), stable across 2 consecutive full runs.

## Phase 5 summary (for reference)

- Deterministic, dependency-free scheduling engine (`app/services/scheduling.py`): a greedy/ranked-slot algorithm over each task's Gemini priority + duration and Google Calendar free/busy intervals — Gemini never picks a timestamp (ADR-016).
- `SchedulingConstraints` dataclass: working hours, slot granularity, default priority score for tasks with no AI result yet — all configurable, not hardcoded (UTC-only at this point; timezone-aware from Phase 6 on).
- `POST /tasks/schedule` (`app/api/scheduling.py`): reads tasks + latest AI results + a fresh `freeBusy.query`, proposes a schedule, writes nothing to the database or to Google Calendar. "Apply Schedule" was visible but disabled this phase.
- Real bug found via testing (not shipped): `app/api/scheduling.py` initially imported Google Calendar functions by name instead of via the module, which silently broke `monkeypatch`-based test mocking — fixed before commit, and re-confirmed still fixed through Phase 7.
- Web/mobile `SchedulePanel`: "Plan my day" → proposed schedule with priority/score/reason per item, and unscheduled tasks with a reason.
- 147/147 backend tests passing (34 new: hermetic scheduling-engine tests + mocked integration tests against the real Supabase project), full suite.

## Phase 6 summary (for reference)

- Fixed the Phase 5 UTC-only limitation: `SchedulingConstraints.working_hours_timezone` resolved via `zoneinfo`/`tzdata`, sourced from the connected Google Calendar's own `timeZone` — verified correct across a real DST transition (ADR-018).
- Incremental OAuth: `GET /calendar/connect?scope=write` requests `calendar.events` in addition to existing scopes on the same connection row, no disconnect/reconnect (ADR-017, ADR-019); `granted_scopes` persisted and checked via `has_write_scope`.
- `POST /tasks/schedule/apply` (`app/services/schedule_apply.py`): the only route that writes to Google Calendar. Fully revalidates every item — task ownership, deadline, fresh free/busy, in-batch overlap — before creating anything (ADR-021); never trusts client-supplied timestamps.
- Idempotency via "no mapping row until the Google API call actually succeeds" (ADR-020) — safe duplicate-apply, safe retry-after-failure, no risk of colliding on `google_event_id`'s `NOT NULL` + unique constraint with a sentinel value.
- Partial-failure-safe: every item processed independently, `created`/`already_applied`/`failed` counts plus a per-item reason.
- Web/mobile: full Review Schedule → Apply to Google Calendar → progress → success-summary flow, replacing Phase 5's disabled action.
- 169/170 backend tests passing (1 skipped: the opt-in live-Google event-creation test, no manually-provided write-scope refresh token in this environment), full suite.

## Phase 7 summary (for reference)

- Google Calendar watch (push-notification) channels: opportunistic registration/renewal (`ensure_watch_channel()`), best-effort `channels.stop` on renewal/disconnect. Requires a public HTTPS `GOOGLE_CALENDAR_WEBHOOK_URL`, unset in local dev.
- `POST /calendar/webhook`: no user JWT, trusted via channel id/resource id/token match; triggers a background incremental sync, never trusts the request body.
- Incremental sync via Google's `syncToken` (bounded full sync when absent, `410 Gone` recovery), serialized per-connection via a Postgres advisory lock.
- Event mapping keyed by `(connection_id, google_event_id)` against `google_calendar_event_mappings` first, not `extendedProperties` — moved/deleted app-created events update/flag `schedule_items`; external events cached in the new `google_calendar_external_events` table as busy blocks.
- Loop prevention by construction (sync never writes back to Google) plus `google_updated_at` comparison for idempotent duplicate/replayed notifications (ADR-022, ADR-023).
- Realtime propagation via the `supabase_realtime` publication + existing RLS, no application-level publish step (ADR-025); both clients subscribe and refetch on change.
- `POST /calendar/sync`: the manual reconciliation fallback + missed-webhook recovery, no polling (ADR-024).
- 192/193 backend tests passing (1 skipped, same live-Google limitation as Phase 6), full suite.
