# Architecture Decision Record (ADR) Log

Record every meaningful architectural decision here, newest at the bottom. Format: Context / Decision / Consequences.

---

## ADR-001: Monorepo with per-app folders

**Date:** 2026-08-24

**Context:** The product has four deployable units (mobile, web, backend, database migrations) that will evolve together, especially during MVP.

**Decision:** Single repository with top-level `/mobile`, `/web`, `/backend`, `/database`, `/docs`, `/infra`, `/.github`. Each app is self-contained (own dependency manifest, own `.env.example`, own README) but shares one git history.

**Consequences:** Simple to clone and reason about at this stage; cross-cutting changes (e.g. an API contract change touching backend + both clients) land in one PR. CI is scoped by path filters so unrelated apps don't rebuild unnecessarily. Revisit only if the team/organization structure needs separate repos (e.g. separate access control per app).

---

## ADR-002: No microservices, queues, cache, or Kubernetes in the MVP

**Date:** 2026-08-24

**Context:** The product brief lists many eventual capabilities (AI, calendar sync, realtime, scheduling). It would be easy to over-build infrastructure up front.

**Decision:** MVP backend is a single FastAPI service talking directly to Postgres, Gemini, and Google Calendar. No Redis, no message queue, no Kubernetes. Local dev uses Docker Compose with one backend container, one web container, one throwaway Postgres container.

**Consequences:** Faster to build and reason about; less to operate. Background/async work (e.g. long-running scheduling jobs) will run in-process/synchronously until proven too slow. This is a deliberate deferral — see the MVP vs. future table in `/docs/architecture.md` — not a rejection of these tools.

---

## ADR-003: Supabase for Postgres + Auth + Realtime

**Date:** 2026-08-24

**Context:** The MVP needs a relational database, user authentication, and a way to push live updates to two clients.

**Decision:** Use Supabase-hosted Postgres as the database, Supabase Auth for authentication, and Supabase Realtime for live sync, instead of standing up separate services for each.

**Consequences:** Removes three pieces of infrastructure the team would otherwise run/maintain. Couples the project to Supabase's platform; acceptable for MVP speed. Local development against a Docker Postgres container is for schema/migration iteration only — auth and realtime are only available against the hosted Supabase project.

---

## ADR-004: FastAPI as the single backend service

**Date:** 2026-08-24

**Context:** The backend needs to call an AI API (Gemini), an external calendar API (Google Calendar), and serve two clients over REST.

**Decision:** One FastAPI (Python) service owns all business logic; clients never call Gemini, Google Calendar, or write business-rule-governed data directly.

**Consequences:** One place to enforce validation, authorization, and integration logic. Python gives easy access to both Google Calendar and Gemini SDKs/client libraries. Revisit only if a workload genuinely needs a different runtime (e.g. a CPU-bound scheduling algorithm that outgrows Python) — not before it's proven necessary.

---

## ADR-005: Flutter for mobile, Next.js + TypeScript for web

**Date:** 2026-08-24

**Context:** The product needs iOS, Android, and a web dashboard.

**Decision:** Flutter for a single mobile codebase covering iOS/Android; Next.js + TypeScript for the web dashboard. Both are thin clients over the FastAPI backend — no business logic duplicated client-side.

**Consequences:** Two client codebases instead of three (native iOS + native Android + web), lower maintenance cost for an MVP team. TypeScript on web catches API contract mismatches early once the backend's OpenAPI schema is consumed.

---

## ADR-006: Tenant-per-workspace schema, auto-provisioned on signup

**Date:** 2026-08-24

**Context:** The MVP only needs personal (single-user) workspaces, but the product will eventually need teams with managers. Bolting multi-tenancy onto a user-owns-data schema later is a painful migration.

**Decision:** Every business table is owned by a `tenant_id` (a workspace), not directly by a `user_id`. A user reaches their data through `tenant_members` (which carries a `role`: owner/admin/member). A Postgres trigger on `auth.users` (`handle_new_user`) automatically creates a personal tenant and an owner membership for every new signup — this provisioning is server-side and atomic with user creation, not a follow-up API call the client/backend could forget to make.

**Consequences:** Team support later (inviting members, manager roles, shared workspaces) is additive — new `tenant_members` rows and a UI to manage them — not a schema rewrite. Every query needs an explicit, verified `tenant_id`; there is no implicit "current tenant," which is more verbose but makes tenant isolation an explicit, auditable property of every query rather than an assumption.

---

## ADR-007: RLS as defense in depth; backend service role owns business writes

**Date:** 2026-08-24

**Context:** Supabase Row Level Security can enforce tenant isolation at the database layer, independent of application code. The architecture already establishes that the FastAPI backend is the only place business logic (AI calls, scheduling, calendar sync) runs.

**Decision:** RLS is enabled on every tenant-owned table. Authenticated clients get `SELECT` policies scoped to tenants they belong to (via a `SECURITY DEFINER` `is_tenant_member()` helper, to avoid RLS self-recursion on `tenant_members`) — this lets clients read their own data directly (useful for Supabase Realtime) without every read round-tripping through the backend. There are no `INSERT`/`UPDATE`/`DELETE` policies for the `authenticated` role on business tables (`tasks`, `plans`, `schedule_items`, AI results, calendar data, usage records); all writes to those go through the backend using the Supabase **service role** key, which bypasses RLS by platform default. The `anon` role has all grants explicitly revoked.

**Consequences:** Two independent layers enforce tenant isolation — the backend's own `tenant_members` check (`require_tenant_membership`) and Postgres RLS — so a bug in one doesn't expose cross-tenant data. Clients cannot bypass backend validation by writing to Supabase directly, since they have no write grant on those tables regardless of RLS. This was verified live: an authenticated client's direct `INSERT` attempt against `tasks` returns `403 row-level security policy` from PostgREST; cross-tenant reads return an empty set, not an error (so as not to leak existence); requests with no token return `401 permission denied` (the `anon` role has zero grants, not just an empty-results policy).

---

## ADR-008: Supabase JWT verification via JWKS (asymmetric), not a shared secret

**Date:** 2026-08-24

**Context:** Supabase projects created with the newer API key format (`sb_publishable_...` / `sb_secret_...`, this project included) sign access tokens with an asymmetric key (ES256), publishing the public key at a JWKS endpoint, rather than the older shared HS256 "JWT secret."

**Decision:** `app/core/security.py` verifies incoming bearer tokens against `SUPABASE_JWKS_URL` using `PyJWKClient` (cached), checking signature, `exp`, and `aud == "authenticated"`. The backend holds no symmetric JWT secret at all.

**Consequences:** The backend needs network access to fetch (and cache) the JWKS document, and must handle key rotation gracefully (the client re-fetches on an unknown `kid`) — both handled by `PyJWKClient`. This is forward-compatible with Supabase's direction for new projects and slightly more operationally complex than a shared secret, which is an acceptable trade for not holding a symmetric signing secret in backend config. Verified against real, live-issued Supabase tokens during this phase; unit tests mint a locally-signed ES256 token and patch the JWKS lookup so verification logic is tested without needing network access or a live project in CI.

---

## ADR-009: `category` and `priority` are not user-editable task fields (yet)

**Date:** 2026-08-24

**Context:** Phase 2's brief asks the task API to support `category` and `priority` alongside `title`/`description`/`status`/`deadline`/`estimated duration`. Neither column exists on `public.tasks` — `0001_init.sql` (Phase 1) deliberately put `category`, `priority_score`, `urgency`, and `reasoning` on `task_ai_results` instead, modeling them as AI-generated output, not user input.

**Decision:** The Phase 2 task API (`TaskCreate`/`TaskUpdate`/`TaskOut`) exposes only fields that actually exist on `tasks`: `title`, `description`, `raw_input`, `status`, `due_at`, `estimated_minutes`. `category` and `priority` are not added as columns or API fields in this phase.

**Consequences:** No schema change was needed to ship task CRUD (honors "use the existing schema, don't redesign it"). Category/priority arrive once the Gemini integration phase populates `task_ai_results` for a task — at that point the API can surface them as read-only, AI-derived fields alongside the task, which is a cleaner shape than letting a user's manually-typed "priority" collide with the AI's computed one. If a manual override turns out to be genuinely needed later, it's an additive column, not a redesign.

---

## ADR-010: JWT verification tolerates ~10s of clock skew

**Date:** 2026-08-24

**Context:** Live task-endpoint tests intermittently failed with `401: The token is not yet valid (iat)` against freshly-issued, genuinely valid Supabase tokens — the dev machine's clock was about one second behind Supabase's, and PyJWT's default `iat`/`exp`/`nbf` validation has zero tolerance for that.

**Decision:** `decode_supabase_jwt` (`app/core/security.py`) now passes `leeway=10` (seconds) to `jwt.decode`, applied to all time-based claim checks.

**Consequences:** Small, deliberate tolerance for real-world clock drift between this backend and Supabase's token issuer, which is standard JWT practice, not a security weakening (10s doesn't meaningfully extend a stolen token's usable window). The existing expired-token test was widened from -10s to -120s so it stays unambiguously outside the leeway. Verified stable across three consecutive full live test runs after the fix.

---

## ADR-011: Gemini structured output — two independent validation layers, not one

**Date:** 2026-08-24

**Context:** Phase 3 needs Gemini's assessment of a task (category, urgency, importance, priority, confidence, duration, reasoning) without letting Gemini's output reach storage unchecked, and without treating every minor deviation as a hard failure. `task_ai_results` (Phase 1 schema) has dedicated columns for `category`/`urgency`/`priority_score`/`effort_estimate_minutes`/`reasoning` but not `importance` or `confidence_score`.

**Decision:**
- `GeminiTaskAnalysis` (`app/schemas/ai.py`) is passed directly to Gemini as `response_schema` (structured JSON output; free-form text is never hand-parsed) and is also the model the response is parsed into.
- Its enum-typed fields (`category`/`urgency`/`importance`) and required-field/type checks are enforced by Pydantic **at parse time** — a response that doesn't follow the requested shape at all is genuinely unusable and raises `AiPrioritizationError` (see app/services/ai.py); nothing is stored.
- Its numeric fields (`priority_score`, `confidence_score`, `estimated_minutes`) deliberately have **no** `ge`/`le` Pydantic constraint, so a slightly-out-of-range value doesn't blow up parsing. A separate, fully deterministic `clamp()` method (plain Python, not Gemini, not Pydantic validators) is applied afterward and guarantees every numeric field ends up within its defined range before the route ever calls the database.
- `importance` and `confidence_score` are stored inside `raw_response` (jsonb) rather than adding two columns to `task_ai_results` — honors "use the existing schema" — and are read back out of it to populate the API response (`TaskAiResultOut`).

**Consequences:** "Structurally wrong" (wrong type, unknown enum value, missing field) and "numerically imprecise" (a score slightly outside 0-100) are handled differently on purpose: the former fails the whole request (no corrupted/partial data ever reaches `task_ai_results`), the latter is silently corrected. This matches the brief's actual intent — deterministic bounds enforcement, not brittle rejection of anything Gemini gets slightly wrong. Verified via 12 hermetic unit tests (`tests/test_ai_schemas.py`) that exercise both layers without any network call, plus live-verified against the real Gemini API.

---

## ADR-012: Direct REST calls to Google via httpx, not google-api-python-client

**Date:** 2026-08-24

**Context:** Phase 4 needs a handful of narrow, read-only calls to Google's OAuth and Calendar APIs (token exchange/refresh/revoke, userinfo, calendarList, events.list, freeBusy.query) from an async FastAPI backend.

**Decision:** `app/services/google_calendar.py` makes these calls directly with `httpx.AsyncClient`, against Google's documented REST endpoints, rather than using the official `google-api-python-client` (+ `google-auth`) SDK.

**Consequences:** `google-api-python-client` is synchronous — using it from async route handlers would mean wrapping every call in a thread pool (`asyncio.to_thread`), adding complexity for no real benefit at this call volume, plus a large dependency footprint (both packages plus their own transitive deps) for a handful of endpoints. The tradeoff is that this backend owns the REST request/response shape directly rather than getting it validated by a maintained client library — mitigated by `GoogleApiError` centralizing error parsing and by the live smoke tests (`tests/test_calendar_live_google.py`) pinning the actual response shapes Google returns. Revisit if a future phase needs write access or a much larger Google API surface, where the official SDK's coverage would start to pay for its weight.

---

## ADR-013: OAuth `state` is a signed token, not a database table

**Date:** 2026-08-24

**Context:** The connect → Google consent → callback round trip needs CSRF protection and a way to carry "which tenant/user initiated this" across a request that has no session or Authorization header (Google's redirect back to `/calendar/callback` is a plain browser navigation).

**Decision:** `state` is a short-lived (10 minute), HS256-signed JWT (`app/core/oauth_state.py`, using PyJWT — already a dependency) encoding `tenant_id`/`user_id`, signed with a dedicated `OAUTH_STATE_SECRET` distinct from the Supabase JWT verification path. No `oauth_states` (or similar "pending authorization attempts") table was added.

**Consequences:** Zero extra schema, zero cleanup-of-expired-rows housekeeping, and the trust model is identical to how the rest of the backend already treats JWTs ("only a verified signature is trusted, not the claims by themselves"). The tradeoff is that a `state` value cannot be revoked early or single-use-enforced server-side (a stolen, still-valid `state` could theoretically be replayed within its 10-minute window) — acceptable for this MVP's threat model (the value is transmitted only over HTTPS to Google and back, mirrors what a stolen `code` could already do, and 10 minutes is a narrow window). Revisit if a future phase needs stronger replay protection.

---

## ADR-014: Google tokens encrypted at the application layer; access_token becomes a refresh-on-demand cache

**Date:** 2026-08-24

**Context:** Phase 1's schema stored `access_token`/`refresh_token` as plaintext `not null` columns. Phase 4's brief explicitly requires encrypting tokens before storing real Google credentials, and minimizing how long/widely an access token is persisted.

**Decision:** `database/migrations/0002_calendar_tokens.sql`: `access_token` becomes nullable (it's a cache of the current short-lived token, refreshed on demand — see `get_valid_access_token()` in `app/services/calendar_connections.py`); a `status` enum (`connected`/`reauth_required`/`error`) and `last_error` column are added so a broken connection has a queryable state instead of only surfacing as a failed request; both token columns are documented (via `comment on column`) as encrypted ciphertext; direct Supabase client access to those two columns is revoked entirely (`grant select (...)` naming every other column explicitly — see ADR-007's "backend owns writes" pattern extended to "backend owns token columns, period"). Encryption itself is Fernet (`app/core/crypto.py`), keyed by `TOKEN_ENCRYPTION_KEY`.

**Consequences:** A leaked read-only database credential (or a bug in a future feature that queries this table more broadly) can no longer expose a usable Google token, encrypted or not — the column simply isn't selectable outside the backend's own direct Postgres connection. The access-token-as-cache pattern means most requests need zero Google calls (reads the cached value until `token_expires_at` approaches), and a revoked/expired refresh token surfaces as a stored `reauth_required` status rather than repeated failed requests. Key rotation has no built-in multi-key support (documented in `app/core/crypto.py`); rotating `TOKEN_ENCRYPTION_KEY` at this MVP's scale means treating existing connections as needing reconnection, an accepted tradeoff given how few real connections will exist before a proper KMS-backed rotation story is worth building.

---

## ADR-015: Mobile OAuth uses an external browser + manual/resume-triggered status refresh, not a deep link

**Date:** 2026-08-24

**Context:** Phase 4 asks for "a secure mechanism for launching the backend OAuth flow and returning to the mobile application." The complete version of "returning to the app" is a custom URL scheme deep link (e.g. via `flutter_web_auth_2`) that the OS routes back into the app the moment Google's consent flow finishes, closing the browser automatically.

**Decision:** This phase opens the authorization URL in an external browser (`url_launcher`) and, rather than a deep link, refreshes `GET /calendar/connection` automatically when the app resumes (`WidgetsBindingObserver.didChangeAppLifecycleState`) plus a manual refresh button. The backend's callback page is a plain "you can close this window" confirmation, not a redirect into a custom scheme.

**Consequences:** No native platform configuration (Android intent-filter, iOS `CFBundleURLTypes`) was added or needed, which also means nothing here could be *silently* broken by an untestable native config mistake — this environment has no device/emulator to verify a deep link actually round-trips. The user experience has one extra manual step compared to a deep link (switching back to the app themselves, or tapping refresh) but is fully functional and was verified through `flutter analyze`/`flutter test`. Upgrading to `flutter_web_auth_2` + scheme registration is a reasonable follow-up once there's a device to verify it on — tracked in `/docs/progress.md`, not silently deferred.

---

## ADR-016: Deterministic greedy scheduler; Gemini never picks the timestamp

**Date:** 2026-08-24

**Context:** Phase 5 combines task priority (from Gemini, Phase 3), duration, deadlines, and calendar availability (Phase 4) to propose when a task should happen. The brief is explicit: "Do NOT let Gemini choose the final timestamp" and "do not build a complicated optimization solver."

**Decision:** `app/services/scheduling.py` is a pure, deterministic module — no I/O, no Gemini calls, no Google calls, just functions over plain Python data (tasks, busy intervals, a `SchedulingConstraints` dataclass). The algorithm is a single-pass greedy/ranked-slot approach, not a solver: sort tasks by (priority DESC, deadline ASC, task_id), then for each task in that order pick the highest-scoring valid candidate interval and consume it from the free-interval pool before moving to the next task. Gemini's role stops at `priority_score`/`estimated_minutes` (Phase 3); everything from there — which interval, which day, whether it fits before the deadline — is this module's decision alone, expressed as ordinary arithmetic and comparisons, not a model call.

**Consequences:** The whole engine is hermetically unit-testable (25 tests in `tests/test_scheduling.py`, zero network/database) and every decision has a one-line, inspectable reason (`_score_candidate`'s `reason` string). Being greedy rather than a global optimizer means it isn't guaranteed to find the theoretically optimal assignment across *all* tasks simultaneously (a genuinely optimal solver would need to consider swapping already-placed tasks) — an accepted MVP tradeoff explicitly sanctioned by the brief ("a deterministic greedy/ranked-slot algorithm is sufficient"). `POST /tasks/schedule` never writes anything (no `schedule_items` row, no Google Calendar event) — this phase returns a proposal only, matching "do not write anything to Google Calendar yet."

---

## ADR-017: Incremental OAuth authorization for the future write scope

**Date:** 2026-08-24

**Context:** Phase 4's calendar connection requests only `calendar.readonly`. A later phase ("Apply Schedule") will need to create Calendar events, requiring `https://www.googleapis.com/auth/calendar.events`. The brief requires the architecture to support requesting that additional scope later without breaking the existing read-only connection, using incremental authorization rather than requesting write access upfront.

**Decision:** `app/services/google_calendar.py` now defines `READ_ONLY_SCOPES` (unchanged, still the only scopes any route in this phase requests) and `WRITE_SCOPES = [*READ_ONLY_SCOPES, "https://www.googleapis.com/auth/calendar.events"]`, and `build_authorization_url()` takes an optional `scopes` parameter (defaulting to `READ_ONLY_SCOPES`) plus always sends `include_granted_scopes=true`. No route in this phase passes `WRITE_SCOPES` or exposes a way to request it — that's deliberately left for the "Apply Schedule" phase to wire up (e.g. a "Grant Calendar write access" action that calls `/calendar/connect` with an upgrade flag). No schema change was made to track which scopes are currently granted; the future phase can add that if it turns out to be needed (a `POST /tasks/{id}/apply` attempt that fails on Google's side for lacking the scope is itself a discoverable, self-correcting signal).

**Consequences:** Google's incremental-authorization model means a future write-scope request re-uses the existing connection rather than requiring the user to disconnect and reconnect — `include_granted_scopes=true` tells Google to fold the new scope into what's already granted. This phase's read-only connection is completely unaffected: no migration, no behavior change to `/calendar/connect`'s default call. The design is deliberately minimal (two constants + one optional parameter) rather than building unused plumbing (a scope-tracking column, an "upgrade" endpoint) for a capability this phase explicitly must not use yet.

---

## ADR-018: Working hours resolved in the connected calendar's own timezone, via `zoneinfo` + `tzdata`

**Date:** 2026-08-24

**Context:** Phase 5's scheduling engine treated "9am" as 9am UTC unconditionally — a documented MVP simplification, not fit to actually write real Calendar events against, since a user in any non-UTC timezone would get events at the wrong local hour.

**Decision:** `SchedulingConstraints` gained a `working_hours_timezone` field (IANA name, e.g. `"America/New_York"`); `compute_free_intervals()` resolves each day's working-hours window via direct `datetime(year, month, day, hour, tzinfo=ZoneInfo(tz))` construction (not midnight + a fixed `timedelta`, which silently misplaces the boundary by an hour across a DST transition — verified by a dedicated DST-boundary test in `tests/test_scheduling.py`). The timezone itself comes from Google's `calendars.get` (`app/services/google_calendar.py:get_calendar_timezone`), fetched once at connect time and cached on `google_calendar_connections.calendar_timezone` (`0003_calendar_write_scope.sql`) — the connected calendar is the source of truth, not a user-entered preference or a guess from IP/locale. `tzdata` (Python's pip-installable IANA database) was added as a dependency after discovering neither this Windows dev machine nor `python:3.11-slim` (the Docker base image) ship a system tz database, so stdlib `zoneinfo` would otherwise raise `ZoneInfoNotFoundError` even for `"UTC"`.

**Consequences:** Every route that builds a schedule (`/tasks/schedule`, `/tasks/schedule/apply`) now passes the connection's real timezone into the engine; a connection with no cached timezone yet (e.g. `get_calendar_timezone` failed non-fatally at connect time) falls back to UTC rather than crashing. `create_event()` additionally localizes the event's own start/end into that timezone before sending them to Google, so the stored event's wall-clock time matches what the working-hours calculation assumed. `tzdata` must be present in every environment this backend runs in (dev, CI, Docker) — it's a normal pip dependency now, not an OS assumption.

---

## ADR-019: `granted_scopes` tracked on the connection row, not inferred

**Date:** 2026-08-24

**Context:** Phase 6 needs to answer "does this connection currently have Calendar write access?" before attempting to apply a schedule, without guessing or making an extra Google API call just to find out.

**Decision:** `google_calendar_connections.granted_scopes` (`0003_calendar_write_scope.sql`) stores exactly what Google's token endpoint reported in its `scope` field on the most recent successful token exchange — not merely what was requested. `ConnectionRecord.has_write_scope` (`app/services/calendar_connections.py`) checks membership of `CALENDAR_EVENTS_WRITE_SCOPE` in that string. `GET /calendar/connection` surfaces this as `has_write_access: bool` so clients don't parse raw scope strings. `POST /tasks/schedule/apply` checks it up front and returns `403 {"code": "CALENDAR_WRITE_SCOPE_REQUIRED"}` before attempting anything if it's missing, rather than letting individual event-creation calls fail with a confusing 403 from Google.

**Consequences:** Trusting Google's own report of what was granted (rather than the scopes this app most recently *requested*) is the only correct source of truth — a user can decline part of a consent screen, or Google's policy can differ from what's asked. `upsert_connection()`'s `granted_scopes` update uses "leave existing value alone if the new one is empty" (some token responses omit `scope` when nothing changed) so a read-only-then-write upgrade never accidentally downgrades what's recorded.

---

## ADR-020: Idempotency via "no mapping row" instead of a failed-state sentinel

**Date:** 2026-08-24

**Context:** Phase 6 must not create a duplicate Google Calendar event if the same schedule is applied twice, and must allow a genuinely failed item to be retried without duplicating an item that already succeeded. `google_calendar_event_mappings.google_event_id` is `NOT NULL` with `UNIQUE(connection_id, google_event_id)` (from Phase 1's schema) — there is no valid sentinel value (like an empty string) that could represent "attempted but failed, no real event id yet" without risking two unrelated failures colliding on that same unique constraint.

**Decision:** A `google_calendar_event_mappings` row is only ever inserted **after** `create_event()` succeeds, using the real `google_event_id` Google returned. A failed attempt leaves the `schedule_items` row in place (so a retry updates it rather than duplicating it) but creates **no** mapping row. `apply_schedule_item()` (`app/services/schedule_apply.py`) treats "a `schedule_items` row exists for this `(tenant_id, task_id)` with a `synced` mapping" as idempotent-skip (returns `already_applied`, no Google call); "a `schedule_items` row exists with no mapping, or a non-`synced` one" as a genuine retry (attempts `create_event()` again, using the same `schedule_items` row).

**Consequences:** No new column, no sentinel value, no sync-status enum expansion was needed — "does a mapping exist" already means exactly "was an event actually created," for free from the existing schema shape. A task that was already successfully applied cannot be moved to a new time via this same endpoint (re-submitting it just returns `already_applied` with the original time) — correct given this phase explicitly excludes modifying an already-created event; "update an applied schedule" is future work, not silently half-supported here.

---

## ADR-021: The apply endpoint fully revalidates client-supplied timestamps; a default `plans` row is auto-created

**Date:** 2026-08-24

**Context:** `POST /tasks/schedule/apply` receives `(task_id, start, end)` per item from the client — whatever it last displayed from a proposal, possibly stale by the time the user taps Apply. The brief requires the backend to never trust these blindly. Separately, `schedule_items.plan_id` is `NOT NULL` (Phase 1 schema) but this MVP has no plan-management feature yet, so nothing has ever created a `plans` row.

**Decision:** Before creating anything, `apply_schedule_item()` re-checks: the task actually belongs to the caller's tenant (looked up fresh, not trusted from the request), `end > start`, the slot ends before the task's real `due_at`, the slot doesn't overlap a *freshly re-queried* `freeBusy.query` snapshot, and the slot doesn't overlap another item in the same apply request (since freshly-created events from earlier items in the same batch wouldn't appear in that one stale freebusy snapshot). Any failure here rejects just that one item with a specific reason, not the whole request. Separately, `get_or_create_default_plan()` (`app/services/plans.py`) transparently creates one `"My Plan"` (`status='active'`) row per tenant on first use — no plan-management UI or API was built; this exists solely so `schedule_items` has a valid, non-null `plan_id` to attach to.

**Consequences:** A time that looked free when the proposal was generated but got booked in the meantime (by this app or externally) is caught and reported, not silently double-booked. The plan auto-creation is intentionally minimal — one plan reused indefinitely per tenant — and not a general planning feature; building real multi-plan support is future work, not blocked by this shim.

## ADR-022: Google Calendar change detection via `google_updated_at`, not a write-back loop

**Date:** 2026-08-24

**Context:** Phase 7 needs to detect when an app-created Calendar event was moved or deleted externally, and to do so without creating the sync loop the brief explicitly warns about ("our app changes event → webhook → backend changes event → webhook → ..."). A naive "always apply what Google reports" approach would also re-process the same event on every duplicate/replayed webhook notification, since Google's delivery is at-least-once, not exactly-once.

**Decision:** Two things, together: (1) this app's sync logic *only ever writes to its own database* in response to a detected external change — nothing in `app/services/calendar_sync.py` calls Google's API to create/update/delete an event, so the specific cycle the brief describes cannot occur (there's no write-back step for a webhook to react to). (2) `google_calendar_event_mappings.google_updated_at` records Google's own `updated` timestamp for the event, set both right after this app creates it (from the `events.insert` response) and after every sync that touches it; a sync entry whose `updated` is not strictly newer than the stored value is treated as an echo of the app's own last write or a replayed notification, and skipped.

**Consequences:** Loop prevention is structural, not a heuristic bolted onto a bidirectional sync — there is no bidirectional sync to begin with, only "Google → app" is live-synced, "app → Google" only happens through the explicit, user-initiated `POST /tasks/schedule/apply`. The tradeoff: if a future phase adds automatic rescheduling or two-way editing, this asymmetry has to be revisited deliberately (with the `google_updated_at` comparison still doing the heavy lifting against duplicate/replayed notifications either way) rather than assumed to already be safe.

## ADR-023: A mapping row is the source of truth for "is this event ours" — not `extendedProperties`

**Date:** 2026-08-24

**Context:** Incoming sync events need to be routed to one of three outcomes: update an app-created schedule item, cache an external event, or (rarely) self-heal a mapping that failed to get written. Google event metadata (`extendedProperties.private`) is client-settable and, in principle, edge cases exist where it could be missing, stale, or (in a multi-app future) set by something else entirely.

**Decision:** `_apply_event_change()` looks the incoming event up by `(connection_id, google_event_id)` in `google_calendar_event_mappings` *first* — that lookup, not the event's own metadata, decides whether it's treated as an app-created event. `extendedProperties.private` is only consulted as a fallback, and only to *adopt* an untracked event into a mapping the app already knows the corresponding `schedule_item_id` for (itself re-verified against `schedule_items` for the correct tenant before being trusted) — never to independently decide "this event is ours" on metadata alone.

**Consequences:** The correctness of the moved/deleted/external classification depends on this app's own database, which it fully controls, rather than on trusting round-tripped data it wrote into a third-party API. The adoption fallback exists specifically to self-heal the one realistic failure mode (Google's `events.insert` succeeds, the mapping `INSERT` right after it doesn't) without that failure mode permanently miscategorizing a real app-created event as an external one.

## ADR-024: Watch-channel renewal and sync reconciliation are opportunistic, not cron-driven

**Date:** 2026-08-24

**Context:** Google Calendar watch channels expire and must be re-registered; a missed webhook notification needs some fallback to still converge. The $0-cost constraint rules out a paid scheduler/queue, and this backend has no existing background-worker process — every prior phase's "background" work has been request-triggered.

**Decision:** No cron job or standalone worker process. `ensure_watch_channel()` is called (and cheaply no-ops unless actually due) at the two natural points a connection is already being touched: right after `/calendar/callback` succeeds, and at the top of every `POST /calendar/sync` call. Reconciliation is the same `POST /calendar/sync` endpoint, called explicitly by a client on mount/foreground and via a manual "Sync now" action — never on a timer.

**Consequences:** A connection that a user never reopens the app for, past its watch channel's expiration, temporarily stops getting push updates until the next time they do open the app (at which point `ensure_watch_channel()` and a fresh sync both run automatically). This is an accepted tradeoff for staying entirely within free-tier, request-driven infrastructure — documented as a known limitation rather than treated as a bug. It also means the whole feature degrades gracefully to "sync on open" in an environment with no public webhook URL at all (e.g. this local dev setup), rather than being unusable without one.

## ADR-025: Realtime propagation relies on Supabase's built-in Postgres-changes publication, not an application-level publish step

**Date:** 2026-08-24

**Context:** The brief's diagram ends in "PostgreSQL → Supabase Realtime → Flutter + Web" and asks that clients update without a manual refresh, without adding paid infrastructure or aggressive polling.

**Decision:** Add `schedule_items`, `google_calendar_event_mappings`, `google_calendar_external_events`, and `google_calendar_connections` to the `supabase_realtime` publication (`database/migrations/0004_calendar_sync.sql`). Because these tables already have an RLS `SELECT` policy scoping rows to tenant members (Phase 1), and Supabase's realtime engine enforces that same RLS when deciding what to push to a given authenticated client, no application code needs to "publish" anything — the backend's existing writes (via its own service-role Postgres connection) are automatically what the realtime engine reads from Postgres's logical replication stream. Both clients subscribe via `postgres_changes` and refetch on any change, rather than diffing payloads.

**Consequences:** This is a genuinely free, already-provisioned Supabase platform feature, not a new service — satisfies the $0 constraint by construction. It depends on the target Supabase project actually having the `supabase_realtime` publication (true for every real Supabase project; the migration wraps the `ALTER PUBLICATION` calls in an existence check so it doesn't fail against a plain local/Docker Postgres that lacks one). The "refetch on any change" approach is coarser than diffing individual payloads, deliberately traded for simplicity given how small a single tenant's calendar-sync dataset is.

## ADR-026: Two minimal read-joined endpoints (`/tasks/prioritized`, `/tasks/schedule/items`) instead of client-side aggregation

**Date:** 2026-08-24

**Context:** The mobile Today/Tasks/Calendar/task-detail screens all need "tasks with their AI priority" and/or "what's actually scheduled in this range" — data that already exists across `tasks`, `task_ai_results`, `schedule_items`, and `google_calendar_event_mappings`, but not as a single call. The brief is explicit that the mobile app should call FastAPI and not duplicate business logic in Flutter.

**Decision:** Add two new, read-only, tenant-scoped GET endpoints that each do one `LEFT JOIN LATERAL` server-side and return an already-combined shape: `GET /tasks/prioritized` (tasks + latest AI result) and `GET /tasks/schedule/items` (applied schedule items + task title + latest priority + Calendar mapping status, optionally filtered to one `task_id`). Neither calls Gemini or Google; both reuse the exact same tenant-membership dependency (`get_tenant_context`) as every other route.

**Consequences:** Avoids an N+1 fetch pattern (one call per task to get its AI result, or guessing which schedule item belongs to which task) that would otherwise be pushed into Flutter as ad-hoc client-side joining — exactly the "business logic in the client" the brief warns against. The tradeoff is two more routes to register correctly ahead of `/tasks/{task_id}`'s path-parameter pattern (same ordering discipline as `/tasks/schedule`, documented at each call site) — a small, well-understood cost for real screens that would otherwise need three or four sequential requests just to render one list.

## ADR-027: Working hours and timezone are shown, not made editable, in Settings

**Date:** 2026-08-24

**Context:** The brief's Settings screen asks for "working hours" and "timezone." Neither is currently a per-user, editable preference anywhere in the backend — working hours are `SchedulingConstraints`' fixed default, and the timezone the scheduling engine actually uses is the connected Google Calendar's own `calendar_timezone` (Phase 6), not a separate stored preference. Building real editable versions of either is a backend feature (a new settings table/endpoint, plus wiring the scheduling engine to read it), which this phase's brief explicitly scopes down to "the mobile MVP polished and convincing," not new backend capability.

**Decision:** Settings displays both, read-only: working hours as the documented fixed default, timezone as whatever the connected Calendar reports (or "UTC (default)" if not yet connected). No new backend endpoint or migration was added for either.

**Consequences:** The screen is honest about what's actually configurable today rather than presenting a settings control that silently does nothing. Making working hours (and per-user timezone override, if ever needed beyond the connected calendar's own) genuinely editable remains a real next-phase backend feature, tracked in `/docs/progress.md`, not something this phase pretends to have solved with UI alone.

## ADR-028: Injectable `http.Client` + `enableRealtime` flag for mobile testability, not a mocking framework

**Date:** 2026-08-24

**Context:** The brief asks for real Flutter test coverage of authentication states, task creation/completion, AI result display, scheduling proposal/apply states, calendar connection/sync states, and error states — none of which existing screens supported testing without hitting a real network, since every API client called `http.get`/`post`/etc. as bare top-level functions and every screen instantiated its own API clients internally with no way to substitute a fake.

**Decision:** Give each API client (`TaskApiClient`, `ScheduleApiClient`, `CalendarApiClient`) an optional `http.Client` constructor parameter (defaulting to a real `http.Client()`), and give each screen that owns one an optional constructor parameter for the same client (defaulting to a real instance) instead of hardcoding `SomeApiClient()` internally. Tests construct clients backed by `package:http/testing.dart`'s `MockClient` and pass them in. Screens that also open a Supabase Realtime subscription additionally take an `enableRealtime` bool (default `true`, set `false` in tests) — constructing even a fake-token `SupabaseClient` for auth tests already required disabling `AuthClientOptions.autoRefreshToken` to avoid a real periodic `Timer` tripping `testWidgets`' "pending timer after dispose" check, and a real Realtime channel subscription would attempt an actual websocket connection with the same risk. No mocking library (mockito/mocktail) was added — `http.testing.MockClient` and hand-written fake subclasses (e.g. a `FakeAuthRepository` overriding just `accessToken`) were sufficient and keep the dependency list unchanged.

**Consequences:** Every screen's constructor now carries a few more optional, test-only parameters that production code never sets — a small readability cost, consistently applied, in exchange for widget tests that exercise real request/response handling (including malformed/error responses) end to end through the actual screen code, not a rewritten test-only code path. This pattern should be followed for any new screen with its own API client going forward.
