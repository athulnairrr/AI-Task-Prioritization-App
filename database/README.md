# database

PostgreSQL schema, managed via Supabase. Hosted Supabase Postgres is the source of truth in all environments; local Docker Postgres (see `/infra`) is only used to develop/test migrations before they're applied to Supabase.

## Structure

- `migrations/` — ordered, numbered SQL migration files (`0001_init.sql`, `0002_...sql`, ...). Never edit an already-applied migration; add a new one.
- `seeds/` — optional SQL/scripts for local development seed data. Never contains real user data.

## Prerequisites

- [Supabase CLI](https://supabase.com/docs/guides/cli) (recommended), or `psql` if applying migrations directly.

## Local setup

```bash
# Option A: Supabase CLI, against a linked hosted project
supabase link --project-ref your-project-ref
supabase db push

# Option B: apply directly to a local/dev Postgres via psql
psql "$DATABASE_URL" -f migrations/0001_init.sql
```

## Conventions

- One logical change per migration file, numbered sequentially, never renumbered.
- Migrations are additive/forward-only. Rollback is handled by writing a new corrective migration, not by editing history.
- Table/column names: `snake_case`. Every table gets `id uuid primary key default gen_random_uuid()` and `created_at timestamptz not null default now()`.
- Row Level Security (RLS) policies are added alongside the tables they protect (see `0001_init.sql`).

## Migrations

| File | What it does |
|---|---|
| `0001_init.sql` | Core schema: profiles, tenants, tenant_members, tasks, task_ai_results, plans, schedule_items, google_calendar_connections, google_calendar_event_mappings, usage_records. RLS, `handle_new_user` auto-provisioning trigger. |
| `0002_calendar_tokens.sql` | Google Calendar token security: `access_token` made nullable (treated as a refresh-on-demand cache), `status`/`last_error` columns added, column-level grants tightened so `authenticated` can never `SELECT` the (encrypted) token columns directly. See `/docs/architecture.md` § Google Calendar integration and ADR-014. |
| `0003_calendar_write_scope.sql` | Adds `calendar_timezone` (the connected calendar's own IANA timezone, used by the scheduling engine instead of assuming UTC) and `granted_scopes` (space-separated scopes actually granted, so the backend can tell a read-only connection from one upgraded to write) to `google_calendar_connections`. See `/docs/architecture.md` "Apply Schedule" section and ADR-018/ADR-019. |
| `0004_calendar_sync.sql` | Two-way Calendar sync: `sync_token`/`last_synced_at`/`watch_channel_id`/`watch_resource_id`/`watch_token`/`watch_expires_at` on `google_calendar_connections`; `google_updated_at` on `google_calendar_event_mappings`; `needs_attention`/`attention_reason` on `schedule_items`; new `google_calendar_external_events` cache table (RLS + column-level grants); `'deleted'` added to the `calendar_sync_status` enum; `schedule_items`/`google_calendar_event_mappings`/`google_calendar_external_events`/`google_calendar_connections` added to the `supabase_realtime` publication. See `/docs/architecture.md` "Two-way Calendar synchronization" and ADR-022 through ADR-025. |
