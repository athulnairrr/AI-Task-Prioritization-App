"use client";

import { useEffect, useState } from "react";
import {
  connectGoogleCalendar,
  disconnectGoogleCalendar,
  getAvailability,
  getConnection,
  syncCalendar,
} from "@/lib/api/calendar";
import { ApiError } from "@/lib/api/client";
import { subscribeToCalendarChanges } from "@/lib/supabase/realtime";
import type { Availability, CalendarConnection } from "@/lib/api/types";

type Status = "loading" | "idle" | "connecting" | "disconnecting" | "syncing" | "error";

/**
 * Connection status + connect/disconnect + a basic 7-day availability
 * preview, plus (Phase 7) last-synced time, watch-channel status, a
 * manual "Sync now" fallback, and a live refresh whenever the backend's
 * Calendar sync changes something in Postgres (via Supabase Realtime --
 * no polling here).
 */
export function CalendarConnectionPanel() {
  const [status, setStatus] = useState<Status>("loading");
  const [connection, setConnection] = useState<CalendarConnection | null>(null);
  const [availability, setAvailability] = useState<Availability | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setStatus("loading");
    setError(null);
    try {
      const conn = await getConnection();
      setConnection(conn);
      setStatus("idle");
      if (conn.status === "connected") {
        loadAvailability();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load calendar connection.");
      setStatus("error");
    }
  }

  async function loadAvailability() {
    try {
      const now = new Date();
      const weekFromNow = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000);
      setAvailability(await getAvailability(now, weekFromNow));
    } catch {
      // Availability preview is a bonus, not critical -- silently skip on failure.
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // A Calendar change synced by the backend (webhook-triggered or via
    // /calendar/sync) updates Postgres; this just refetches connection
    // status when that happens, rather than diffing payloads itself.
    return subscribeToCalendarChanges(() => {
      load();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSyncNow() {
    setStatus("syncing");
    setError(null);
    try {
      await syncCalendar();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sync failed.");
      setStatus("error");
    }
  }

  async function handleConnect() {
    setStatus("connecting");
    setError(null);
    try {
      await connectGoogleCalendar(); // navigates away to Google
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start Google connection.");
      setStatus("error");
    }
  }

  async function handleDisconnect() {
    setStatus("disconnecting");
    setError(null);
    try {
      await disconnectGoogleCalendar();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to disconnect.");
      setStatus("error");
    }
  }

  if (status === "loading") {
    return <p style={{ fontSize: 13, color: "#888" }}>Loading calendar connection…</p>;
  }

  const busy = status === "connecting" || status === "disconnecting" || status === "syncing";

  return (
    <div style={{ border: "1px solid #eee", borderRadius: 8, padding: 16, marginBottom: 24 }}>
      <div style={{ fontWeight: 600, marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
        <span aria-hidden>📅</span> Google Calendar
      </div>

      {error && (
        <p role="alert" style={{ color: "#c0392b", fontSize: 13 }}>
          {error}
        </p>
      )}

      {connection?.status === "connected" && (
        <>
          <p style={{ fontSize: 13, color: "#444" }}>
            Connected as <strong>{connection.google_account_email}</strong>
          </p>
          <p style={{ fontSize: 12, color: "#888" }}>
            {connection.watch_active ? "Live sync active" : "Live sync off (using manual sync)"}
            {" · Last synced: "}
            {connection.last_synced_at ? new Date(connection.last_synced_at).toLocaleString() : "never"}
          </p>
          <div style={{ display: "flex", gap: 12 }}>
            <button type="button" onClick={handleDisconnect} disabled={busy} style={linkButtonStyle}>
              {status === "disconnecting" ? "Disconnecting…" : "Disconnect"}
            </button>
            <button type="button" onClick={handleSyncNow} disabled={busy} style={linkButtonStyle}>
              {status === "syncing" ? "Syncing…" : "Sync now"}
            </button>
          </div>

          {availability && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Busy in the next 7 days</div>
              {availability.busy.length === 0 ? (
                <p style={{ fontSize: 13, color: "#666" }}>No busy events found.</p>
              ) : (
                <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                  {availability.busy.slice(0, 10).map((b, i) => (
                    <li key={i}>
                      {new Date(b.start).toLocaleString()} – {new Date(b.end).toLocaleString()}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </>
      )}

      {connection?.status === "reauth_required" && (
        <>
          <p style={{ fontSize: 13, color: "#b45309" }}>
            Your Google Calendar connection needs to be reconnected (access was revoked or expired).
          </p>
          <button type="button" onClick={handleConnect} disabled={busy} style={outlineButtonStyle}>
            {status === "connecting" ? "Redirecting…" : "Reconnect Google Calendar"}
          </button>
        </>
      )}

      {(connection?.status === "not_connected" || connection?.status === "error") && (
        <button type="button" onClick={handleConnect} disabled={busy} style={outlineButtonStyle}>
          {status === "connecting" ? "Redirecting…" : "Connect Google Calendar"}
        </button>
      )}
    </div>
  );
}

const outlineButtonStyle: React.CSSProperties = {
  padding: "6px 12px",
  cursor: "pointer",
  border: "1px solid #2563eb",
  color: "#2563eb",
  background: "none",
  borderRadius: 6,
  fontSize: 13,
};

const linkButtonStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#2563eb",
  cursor: "pointer",
  padding: 0,
  fontSize: 13,
};
