import { createClient } from "./client";

const WATCHED_TABLES = [
  "schedule_items",
  "google_calendar_event_mappings",
  "google_calendar_external_events",
  "google_calendar_connections",
] as const;

/**
 * Subscribes to Postgres changes on the tables Phase 7's Calendar sync
 * writes to. No manual "publish" step is needed on the backend -- these
 * tables are already in the `supabase_realtime` publication (see
 * database/migrations/0004_calendar_sync.sql) and already have an RLS
 * SELECT policy scoping rows to the caller's tenant, so a change only
 * ever reaches a client who could already read that row.
 *
 * Deliberately coarse: `onChange` is called (debounced) for *any* insert/
 * update/delete on any of these tables, and the caller just refetches
 * whatever it displays, rather than this module trying to diff payloads
 * itself -- simpler, and safe for what are always small per-tenant
 * datasets. Returns an unsubscribe function.
 */
export function subscribeToCalendarChanges(onChange: () => void): () => void {
  const supabase = createClient();
  const channel = supabase.channel("calendar-sync-changes");
  let debounce: ReturnType<typeof setTimeout> | null = null;

  const scheduleRefresh = () => {
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(onChange, 300);
  };

  for (const table of WATCHED_TABLES) {
    channel.on("postgres_changes", { event: "*", schema: "public", table }, scheduleRefresh);
  }

  channel.subscribe();

  return () => {
    if (debounce) clearTimeout(debounce);
    supabase.removeChannel(channel);
  };
}
