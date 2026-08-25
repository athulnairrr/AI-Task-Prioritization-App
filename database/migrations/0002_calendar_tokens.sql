-- 0002_calendar_tokens.sql
-- Phase 4 (Google Calendar integration): connection-level status tracking,
-- and relaxes `access_token` to optional now that it's treated as a
-- short-lived, refresh-on-demand cache rather than a required persisted
-- value. `refresh_token` (and `access_token`, when present) now hold
-- ciphertext, encrypted at the application layer before every write --
-- see backend/app/core/crypto.py and /docs/architecture.md "Token
-- security". No column type change was needed for that (both were
-- already `text`), but it's a meaningful behavioral change worth
-- recording in a migration and in the column comments below.

do $$ begin
  create type calendar_connection_status as enum ('connected', 'reauth_required', 'error');
exception when duplicate_object then null;
end $$;

alter table public.google_calendar_connections
  add column if not exists status calendar_connection_status not null default 'connected',
  add column if not exists last_error text;

alter table public.google_calendar_connections
  alter column access_token drop not null;

comment on column public.google_calendar_connections.access_token is
  'Short-lived Google access token, encrypted at the application layer (see backend/app/core/crypto.py) before storage. Treated as a cache: refreshed on demand from refresh_token when missing or past token_expires_at, not guaranteed to be present. Never returned via any API response.';

comment on column public.google_calendar_connections.refresh_token is
  'Long-lived Google refresh token, encrypted at the application layer (Fernet) before storage. The only credential actually required to keep a connection usable. Never returned via any API response, never logged.';

comment on column public.google_calendar_connections.status is
  'connected: usable. reauth_required: the refresh token was rejected by Google (revoked/expired) -- the user must reconnect. error: an unexpected failure occurred; see last_error.';

-- Defense in depth: even though access_token/refresh_token are encrypted
-- ciphertext, never let a direct Supabase client query (bypassing the
-- backend entirely) read them at all -- "never send tokens to Flutter/web"
-- should hold even for a client that would only ever see ciphertext it
-- can't decrypt. The backend itself connects via DATABASE_URL with its own
-- Postgres role, not through this `authenticated` grant, so it's unaffected.
revoke select on public.google_calendar_connections from authenticated;
grant select (
  id, tenant_id, user_id, google_account_email, calendar_id,
  token_expires_at, connected_at, updated_at, status, last_error
) on public.google_calendar_connections to authenticated;
