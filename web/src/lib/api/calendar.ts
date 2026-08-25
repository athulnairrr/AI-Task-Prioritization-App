import { request } from "./client";
import type { Availability, CalendarConnection, CalendarSyncResult, ExternalCalendarEvent } from "./types";

export function getConnection(): Promise<CalendarConnection> {
  return request<CalendarConnection>("/calendar/connection");
}

/**
 * Gets the Google authorization URL from the backend, then performs the
 * actual top-level browser redirect. The /calendar/connect call itself is
 * a normal authenticated fetch; the redirect to Google is a separate step
 * this function does after getting the URL back.
 */
export async function connectGoogleCalendar(): Promise<void> {
  const { authorization_url } = await request<{ authorization_url: string }>("/calendar/connect");
  window.location.href = authorization_url;
}

/**
 * Requests the additional calendar.events (write) scope via incremental
 * authorization -- the existing read-only connection is upgraded in
 * place, not replaced. Used only by the "Apply Schedule" flow, and only
 * when the current connection doesn't already have write access.
 */
export async function connectGoogleCalendarWriteAccess(): Promise<void> {
  const { authorization_url } = await request<{ authorization_url: string }>(
    "/calendar/connect?scope=write"
  );
  window.location.href = authorization_url;
}

export function disconnectGoogleCalendar(): Promise<void> {
  return request<void>("/calendar/connection", { method: "DELETE" });
}

export function getAvailability(start: Date, end: Date): Promise<Availability> {
  const params = new URLSearchParams({ start: start.toISOString(), end: end.toISOString() });
  return request<Availability>(`/calendar/availability?${params}`);
}

/**
 * Explicit reconciliation (Phase 7): renews the push-notification watch
 * channel if it's missing/expiring, then runs an incremental (or full,
 * the first time) sync inline. Call this when the calendar panel mounts
 * or the user taps refresh -- not on a timer; push notifications (when a
 * public webhook URL is configured) are the primary path, this is the
 * $0-cost fallback for local dev and missed notifications.
 */
export function syncCalendar(): Promise<CalendarSyncResult> {
  return request<CalendarSyncResult>("/calendar/sync", { method: "POST" });
}

export function getExternalEvents(start: Date, end: Date): Promise<ExternalCalendarEvent[]> {
  const params = new URLSearchParams({ start: start.toISOString(), end: end.toISOString() });
  return request<ExternalCalendarEvent[]>(`/calendar/external-events?${params}`);
}
