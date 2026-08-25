-- 0003_calendar_write_scope.sql
-- Phase 6 (Apply Schedule): two additions to google_calendar_connections
-- needed to (a) know which timezone the connected calendar actually uses
-- (see backend/app/services/scheduling.py's timezone-aware working hours,
-- and /docs/architecture.md "Timezone strategy"), and (b) know whether
-- the connection currently has Calendar *write* permission or only the
-- original read-only scope, without having to guess or re-request every
-- time (see /docs/decisions.md ADR-019, incremental OAuth).

alter table public.google_calendar_connections
  add column if not exists calendar_timezone text,
  add column if not exists granted_scopes text not null default '';

comment on column public.google_calendar_connections.calendar_timezone is
  'IANA timezone name (e.g. "America/New_York") of the connected Google Calendar, fetched via calendars.get at connect time. Null until first fetched; the scheduling engine falls back to UTC if still null.';

comment on column public.google_calendar_connections.granted_scopes is
  'Space-separated OAuth scopes actually granted, taken from the token endpoint''s `scope` field on the most recent successful token exchange. Used to check for calendar.events (write) access before attempting to apply a schedule -- see app/services/calendar_connections.py has_write_scope().';

-- Same column-level grant discipline as 0002_calendar_tokens.sql: these
-- two new columns are not secrets, but access still goes through the
-- explicit column list rather than a blanket re-grant, so a future column
-- added here doesn't accidentally become client-readable by default.
grant select (calendar_timezone, granted_scopes) on public.google_calendar_connections to authenticated;
