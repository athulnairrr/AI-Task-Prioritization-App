import { request } from "./client";
import type {
  NeedsAttentionItem,
  ScheduleApplyItemInput,
  ScheduleApplyResult,
  ScheduleProposal,
  ScheduleRequestInput,
} from "./types";

/**
 * Proposes a schedule -- never writes anything to Google Calendar and
 * never persists the proposal. "Plan my day"/"Plan my work" calls this
 * with no task_ids to consider every unscheduled, already-prioritized task.
 */
export function proposeSchedule(input: ScheduleRequestInput = {}): Promise<ScheduleProposal> {
  return request<ScheduleProposal>("/tasks/schedule", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/**
 * Applies approved schedule items to Google Calendar. The backend
 * revalidates every item against fresh availability and the task's real
 * deadline before writing anything -- these start/end values are a
 * request, not a fact the backend trusts blindly.
 */
export function applySchedule(items: ScheduleApplyItemInput[]): Promise<ScheduleApplyResult> {
  return request<ScheduleApplyResult>("/tasks/schedule/apply", {
    method: "POST",
    body: JSON.stringify({ items }),
  });
}

/**
 * Previously-applied schedule items whose Google Calendar event was
 * deleted externally (Phase 7 two-way sync) -- never auto-recreated;
 * re-applying the task creates a fresh event and clears this.
 */
export function listNeedsAttention(): Promise<NeedsAttentionItem[]> {
  return request<NeedsAttentionItem[]>("/tasks/schedule/needs-attention");
}
