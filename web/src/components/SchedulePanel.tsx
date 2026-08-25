"use client";

import { useEffect, useState } from "react";
import { applySchedule, listNeedsAttention, proposeSchedule } from "@/lib/api/scheduling";
import { connectGoogleCalendarWriteAccess, getConnection } from "@/lib/api/calendar";
import { ApiError } from "@/lib/api/client";
import { subscribeToCalendarChanges } from "@/lib/supabase/realtime";
import type { CalendarConnection, NeedsAttentionItem, ScheduleApplyResult, ScheduleProposal } from "@/lib/api/types";

type Status = "idle" | "planning" | "reviewing" | "applying" | "applied" | "error";

/**
 * "Plan my day" -> review the proposal -> "Apply to Google Calendar" ->
 * progress -> success summary. Real writes happen here (Phase 6) -- the
 * backend independently revalidates every item before creating anything,
 * so this component just displays what came back.
 */
export function SchedulePanel() {
  const [status, setStatus] = useState<Status>("idle");
  const [proposal, setProposal] = useState<ScheduleProposal | null>(null);
  const [connection, setConnection] = useState<CalendarConnection | null>(null);
  const [applyResult, setApplyResult] = useState<ScheduleApplyResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | undefined>(undefined);
  const [needsAttention, setNeedsAttention] = useState<NeedsAttentionItem[]>([]);

  function loadNeedsAttention() {
    listNeedsAttention()
      .then(setNeedsAttention)
      .catch(() => {
        // Non-fatal -- just an informational list.
      });
  }

  useEffect(() => {
    getConnection()
      .then(setConnection)
      .catch(() => {
        // Non-fatal here -- the apply button just won't know write-access
        // status until the user tries; the backend still enforces it.
      });
    loadNeedsAttention();
  }, []);

  useEffect(() => {
    // A schedule item this app applied can be moved or deleted directly in
    // Google Calendar (Phase 7 two-way sync) -- refresh what needs
    // attention whenever the backend's sync changes something, instead of
    // polling.
    return subscribeToCalendarChanges(() => {
      loadNeedsAttention();
    });
  }, []);

  async function handlePlan() {
    setStatus("planning");
    setError(null);
    setErrorCode(undefined);
    setApplyResult(null);
    try {
      const result = await proposeSchedule();
      setProposal(result);
      setStatus("reviewing");
    } catch (err) {
      handleError(err);
    }
  }

  async function handleConnectWriteAccess() {
    try {
      await connectGoogleCalendarWriteAccess(); // navigates away to Google
    } catch (err) {
      handleError(err);
    }
  }

  async function handleApply() {
    if (!proposal || proposal.scheduled.length === 0) return;
    setStatus("applying");
    setError(null);
    setErrorCode(undefined);
    try {
      const result = await applySchedule(
        proposal.scheduled.map((item) => ({ task_id: item.task_id, start: item.start, end: item.end }))
      );
      setApplyResult(result);
      setStatus("applied");
    } catch (err) {
      handleError(err);
    }
  }

  function handleError(err: unknown) {
    if (err instanceof ApiError) {
      setError(err.message);
      setErrorCode(err.code);
    } else {
      setError("Something went wrong. Please try again.");
    }
    setStatus("error");
  }

  const busy = status === "planning" || status === "applying";
  const titleByTaskId = new Map((proposal?.scheduled ?? []).map((i) => [i.task_id, i.title]));

  return (
    <div style={{ border: "1px solid #eee", borderRadius: 8, padding: 16, marginBottom: 24 }}>
      <div style={{ fontWeight: 600, marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
        <span aria-hidden>🗓️</span> Plan my day
      </div>

      {status === "error" && (
        <p role="alert" style={{ color: "#c0392b", fontSize: 13 }}>
          {errorCode === "REAUTH_REQUIRED"
            ? "Your Google Calendar connection needs to be reconnected."
            : errorCode === "CALENDAR_WRITE_SCOPE_REQUIRED"
              ? "Calendar write permission is required. Connect Calendar permissions below, then try again."
              : error}
        </p>
      )}

      {needsAttention.length > 0 && (
        <div
          style={{
            background: "#fef3c7",
            border: "1px solid #f59e0b",
            borderRadius: 6,
            padding: 10,
            marginBottom: 12,
            fontSize: 13,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Needs attention</div>
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {needsAttention.map((item) => (
              <li key={item.schedule_item_id} style={{ padding: "2px 0" }}>
                <strong>{item.title}</strong> — {item.reason ?? "This event was changed outside the app."} Re-run
                &quot;Plan my day&quot; and apply again to recreate it.
              </li>
            ))}
          </ul>
        </div>
      )}

      {status !== "applied" && (
        <button type="button" onClick={handlePlan} disabled={busy} style={outlineButtonStyle}>
          {status === "planning" ? "Planning…" : "Plan my day"}
        </button>
      )}

      {(status === "reviewing" || status === "applying") && proposal && (
        <div style={{ marginTop: 16 }}>
          {proposal.scheduled.length === 0 && proposal.unscheduled.length === 0 && (
            <p style={{ fontSize: 13, color: "#666" }}>
              No unscheduled, prioritized tasks found. Add a task and run &quot;Prioritize with AI&quot; first.
            </p>
          )}

          {proposal.scheduled.length > 0 && (
            <>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Review Schedule</div>
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {proposal.scheduled.map((item) => (
                  <li
                    key={item.task_id}
                    style={{ padding: "8px 0", borderBottom: "1px solid #f0f0f0", fontSize: 13 }}
                  >
                    <div style={{ fontWeight: 600 }}>{item.title}</div>
                    <div style={{ color: "#444" }}>
                      {new Date(item.start).toLocaleString()} – {new Date(item.end).toLocaleTimeString()}
                    </div>
                    <div style={{ color: "#666" }}>
                      priority {item.priority_score.toFixed(0)} · score {item.score.toFixed(0)}
                    </div>
                    <div style={{ color: "#888", fontStyle: "italic" }}>{item.reason}</div>
                  </li>
                ))}
              </ul>
            </>
          )}

          {proposal.unscheduled.length > 0 && (
            <>
              <div style={{ fontSize: 12, fontWeight: 600, marginTop: 12, marginBottom: 6 }}>
                Could not schedule
              </div>
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {proposal.unscheduled.map((item) => (
                  <li key={item.task_id} style={{ padding: "4px 0", fontSize: 13, color: "#b45309" }}>
                    <strong>{item.title || "(task)"}</strong>: {item.reason}
                  </li>
                ))}
              </ul>
            </>
          )}

          {proposal.scheduled.length > 0 && (
            <div style={{ marginTop: 12 }}>
              {connection?.has_write_access ? (
                <button type="button" onClick={handleApply} disabled={busy} style={primaryButtonStyle}>
                  {status === "applying" ? "Applying…" : "Apply to Google Calendar"}
                </button>
              ) : (
                <button type="button" onClick={handleConnectWriteAccess} style={outlineButtonStyle}>
                  Connect Calendar permissions
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {status === "applied" && applyResult && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>
            {applyResult.created} task{applyResult.created === 1 ? "" : "s"} scheduled
            {applyResult.already_applied > 0 ? ` (${applyResult.already_applied} already applied)` : ""}
            {applyResult.failed > 0 ? `, ${applyResult.failed} failed` : ""}
          </div>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, fontSize: 13 }}>
            {applyResult.results.map((r) => (
              <li key={r.task_id} style={{ padding: "4px 0" }}>
                <span style={{ color: r.status === "failed" ? "#c0392b" : "#16a34a" }}>
                  {r.status === "failed" ? "✗" : "✓"}
                </span>{" "}
                {titleByTaskId.get(r.task_id) || r.task_id}
                {r.start && r.end && (
                  <span style={{ color: "#666" }}>
                    {" — "}
                    {new Date(r.start).toLocaleString()} – {new Date(r.end).toLocaleTimeString()}
                  </span>
                )}
                {r.status === "failed" && r.reason && (
                  <div style={{ color: "#c0392b", marginLeft: 18 }}>{r.reason}</div>
                )}
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => {
              setStatus("idle");
              setProposal(null);
              setApplyResult(null);
            }}
            style={{ ...outlineButtonStyle, marginTop: 12 }}
          >
            Done
          </button>
        </div>
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

const primaryButtonStyle: React.CSSProperties = {
  padding: "6px 12px",
  cursor: "pointer",
  border: "1px solid #2563eb",
  color: "#fff",
  background: "#2563eb",
  borderRadius: 6,
  fontSize: 13,
};
