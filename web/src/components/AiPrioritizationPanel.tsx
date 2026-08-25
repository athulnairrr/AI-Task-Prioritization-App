"use client";

import { useEffect, useState } from "react";
import { ApiError, getLatestAiResult, prioritizeTask } from "@/lib/api/tasks";
import type { TaskAiResult } from "@/lib/api/types";

type Status = "loadingExisting" | "idle" | "running" | "error";

/**
 * "Prioritize with AI" panel for the task edit form. Loads any existing
 * result on mount (a plain GET, no Gemini call) and only calls Gemini when
 * the user explicitly clicks the button -- never automatically.
 */
export function AiPrioritizationPanel({ taskId }: { taskId: string }) {
  const [status, setStatus] = useState<Status>("loadingExisting");
  const [result, setResult] = useState<TaskAiResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLatestAiResult(taskId)
      .then((r) => {
        if (!cancelled) {
          setResult(r);
          setStatus("idle");
        }
      })
      .catch(() => {
        if (!cancelled) setStatus("idle");
      });
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  async function handlePrioritize() {
    setStatus("running");
    setError(null);
    try {
      const r = await prioritizeTask(taskId);
      setResult(r);
      setStatus("idle");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
      setStatus("error");
    }
  }

  if (status === "loadingExisting") {
    return <p style={{ fontSize: 13, color: "#888" }}>Loading AI result…</p>;
  }

  const busy = status === "running";

  return (
    <div style={{ border: "1px solid #eee", borderRadius: 8, padding: 12, marginTop: 8 }}>
      <div style={{ fontWeight: 600, marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
        <span aria-hidden>✨</span> AI prioritization
      </div>

      {status === "error" && (
        <p role="alert" style={{ color: "#c0392b", fontSize: 13 }}>
          {error}
        </p>
      )}

      {result && (
        <div style={{ marginBottom: 8 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
            <StatChip label="Priority" value={result.priority_score?.toFixed(0) ?? "—"} />
            <StatChip
              label="Confidence"
              value={result.confidence_score != null ? `${Math.round(result.confidence_score * 100)}%` : "—"}
            />
            {result.category && <StatChip label="Category" value={result.category} />}
            {result.effort_estimate_minutes != null && (
              <StatChip label="Est." value={`${result.effort_estimate_minutes} min`} />
            )}
          </div>
          {result.reasoning && <p style={{ fontSize: 13, color: "#444", margin: 0 }}>{result.reasoning}</p>}
        </div>
      )}

      <button type="button" onClick={handlePrioritize} disabled={busy} style={outlineButtonStyle}>
        {busy ? "Analyzing…" : result ? "Re-prioritize with AI" : "Prioritize with AI"}
      </button>
    </div>
  );
}

function StatChip({ label, value }: { label: string; value: string }) {
  return (
    <span
      style={{
        fontSize: 12,
        background: "#f2f2f2",
        borderRadius: 999,
        padding: "2px 10px",
      }}
    >
      {label}: {value}
    </span>
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
