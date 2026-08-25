-- 0004_calendar_sync.sql
-- Phase 7 (Two-way Calendar synchronization): Google Calendar `watch`
-- (push notification) channels, incremental sync via syncToken, a
-- normalized cache of external (non-app-created) Calendar events used for
-- busy-block display and change detection, and enabling Supabase Realtime
-- on the tables this phase writes to. See /docs/architecture.md "Two-way
-- Calendar synchronization" and ADR-022 through ADR-025 in
-- /docs/decisions.md.

-- 'deleted': the Google event behind a schedule item's mapping was removed
-- externally. Distinct from 'failed' (Phase 6: we tried to create it and
-- Google rejected the call) -- 'deleted' means it previously existed and
-- synced fine, then disappeared on Google's side.
alter type calendar_sync_status add value if not exists 'deleted';

alter table public.google_calendar_connections
  add column if not exists sync_token text,
  add column if not exists last_synced_at timestamptz,
  add column if not exists watch_channel_id text,
  add column if not exists watch_resource_id text,
  add column if not exists watch_token text,
  add column if not exists watch_expires_at timestamptz;

comment on column public.google_calendar_connections.sync_token is
  'Google Calendar syncToken from the most recent successful events.list incremental sync. Null means "no baseline yet" -- the next sync must be a full (bounded-window) sync, per Google''s documented syncToken contract. Also cleared when Google returns 410 Gone for an expired/invalid token, forcing the same full-resync path.';

comment on column public.google_calendar_connections.watch_channel_id is
  'Our own generated id for the active Google Calendar push-notification (watch) channel. Echoed back by Google as the X-Goog-Channel-Id header on every webhook call for this channel -- the only way the webhook handler knows which connection a notification is for.';

comment on column public.google_calendar_connections.watch_resource_id is
  'Google-assigned resource id for the active watch channel, returned when the channel was registered. Must match the X-Goog-Resource-Id header on an incoming webhook call, in addition to the channel token, before it is trusted.';

comment on column public.google_calendar_connections.watch_token is
  'Random per-channel secret, sent as the watch channel''s `token` field at registration and echoed back in the X-Goog-Channel-Token header. The webhook handler rejects any request whose token does not match this value for the looked-up channel -- see /docs/architecture.md "Webhook security".';

comment on column public.google_calendar_connections.watch_expires_at is
  'Expiration of the active watch channel (Google channels are not renewable in place -- a new channel is registered and the old one stopped before this time, see app/services/calendar_sync.py ensure_watch_channel()).';

create unique index if not exists idx_gcal_connections_watch_channel_id
  on public.google_calendar_connections (watch_channel_id)
  where watch_channel_id is not null;

alter table public.google_calendar_event_mappings
  add column if not exists google_updated_at timestamptz;

comment on column public.google_calendar_event_mappings.google_updated_at is
  'Google''s `updated` timestamp for this event as of the last time this app wrote or synced it. An incoming sync entry whose `updated` is not after this value is recognized as an echo of our own last write (or a duplicate/replayed notification) and skipped rather than reprocessed -- see /docs/architecture.md "Loop prevention".';

alter table public.schedule_items
  add column if not exists needs_attention boolean not null default false,
  add column if not exists attention_reason text;

comment on column public.schedule_items.needs_attention is
  'Set when the Google Calendar event this app created for this schedule item was deleted externally. The app never silently recreates a deleted event -- this flag is how the deletion is surfaced to the user instead. Cleared by re-applying the schedule item (POST /tasks/schedule/apply), which is the explicit user action that creates a new event for it.';

create table if not exists public.google_calendar_external_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  connection_id uuid not null references public.google_calendar_connections (id) on delete cascade,
  google_event_id text not null,
  title text,
  starts_at timestamptz not null,
  ends_at timestamptz not null,
  all_day boolean not null default false,
  status text not null default 'confirmed',
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  unique (connection_id, google_event_id)
);

comment on table public.google_calendar_external_events is
  'Normalized cache of Calendar events NOT created by this app (no extendedProperties.private.app = "ai-work-planner"), refreshed by incremental/full sync. Used to render busy blocks without an extra live Google call and to detect update/delete on the next sync pass. Never automatically turned into a task -- see /docs/architecture.md "Event mapping: external events".';

create index if not exists idx_gcal_external_events_tenant_id on public.google_calendar_external_events (tenant_id);
create index if not exists idx_gcal_external_events_connection_starts_at on public.google_calendar_external_events (connection_id, starts_at);

alter table public.google_calendar_external_events enable row level security;

create policy google_calendar_external_events_select on public.google_calendar_external_events
  for select to authenticated
  using (public.is_tenant_member(tenant_id));

revoke all on public.google_calendar_external_events from anon;
grant select on public.google_calendar_external_events to authenticated;

-- updated_at trigger, same convention as every other table (see 0001_init.sql).
drop trigger if exists set_updated_at on public.google_calendar_external_events;
create trigger set_updated_at before update on public.google_calendar_external_events
  for each row execute procedure public.set_updated_at();

-- Realtime: let Flutter/web subscribe directly to changes on the tables
-- this phase writes to, instead of polling. Supabase's realtime engine
-- reads Postgres's logical replication stream for tables in this
-- publication and pushes changes to clients whose RLS SELECT policy would
-- allow them to see the row -- no application-level "publish" call is
-- needed; the backend just writes normally via its own DB connection
-- (which bypasses RLS, same as every other write in this app) and
-- Supabase does the rest. See /docs/architecture.md "Realtime propagation".
do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'schedule_items'
  ) then
    alter publication supabase_realtime add table public.schedule_items;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'google_calendar_event_mappings'
  ) then
    alter publication supabase_realtime add table public.google_calendar_event_mappings;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'google_calendar_external_events'
  ) then
    alter publication supabase_realtime add table public.google_calendar_external_events;
  end if;
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'google_calendar_connections'
  ) then
    alter publication supabase_realtime add table public.google_calendar_connections;
  end if;
exception when undefined_object then
  -- supabase_realtime publication doesn't exist in this environment (e.g. a
  -- plain local/Docker Postgres instead of a real Supabase project) --
  -- non-fatal. The app still works via the manual POST /calendar/sync
  -- reconciliation fallback; it just doesn't get push-to-client updates.
  null;
end $$;

-- Same column-level grant discipline as 0002/0003: only the fields a
-- client actually needs are exposed, never the sync token or watch
-- channel/token identifiers.
grant select (
  id, tenant_id, user_id, google_account_email, calendar_id,
  token_expires_at, connected_at, updated_at, status, last_error,
  calendar_timezone, granted_scopes, last_synced_at, watch_expires_at
) on public.google_calendar_connections to authenticated;
