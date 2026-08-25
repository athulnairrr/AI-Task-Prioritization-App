export type TaskStatus = "pending" | "in_progress" | "done" | "cancelled";

/** Mirrors the backend's TaskOut schema (see backend/app/schemas/task.py). */
export interface Task {
  id: string;
  tenant_id: string;
  created_by: string;
  title: string;
  description: string | null;
  raw_input: string | null;
  status: TaskStatus;
  due_at: string | null;
  estimated_minutes: number | null;
  created_at: string;
  updated_at: string;
}

export interface TaskCreateInput {
  title: string;
  description?: string;
  due_at?: string;
  estimated_minutes?: number;
}

export interface TaskUpdateInput {
  title?: string;
  description?: string | null;
  status?: TaskStatus;
  due_at?: string | null;
  estimated_minutes?: number | null;
}

/** Mirrors the backend's TaskAiResultOut schema (backend/app/schemas/ai.py). */
export interface TaskAiResult {
  id: string;
  task_id: string;
  model: string;
  category: string | null;
  urgency: string | null;
  importance: string | null;
  priority_score: number | null;
  confidence_score: number | null;
  effort_estimate_minutes: number | null;
  reasoning: string | null;
  created_at: string;
}

/** Mirrors the backend's CalendarConnectionOut schema (backend/app/schemas/calendar.py). */
export type CalendarConnectionStatus = "not_connected" | "connected" | "reauth_required" | "error";

export interface CalendarConnection {
  status: CalendarConnectionStatus;
  google_account_email: string | null;
  calendar_id: string | null;
  connected_at: string | null;
  last_error: string | null;
  calendar_timezone: string | null;
  has_write_access: boolean;
  /** Phase 7: when the last incremental/full sync completed. */
  last_synced_at: string | null;
  /** Whether a Google push-notification (watch) channel is currently
   * registered and unexpired. False just means updates rely on the
   * POST /calendar/sync fallback instead of push notifications. */
  watch_active: boolean;
}

/** Mirrors the backend's ExternalCalendarEventOut schema -- a Calendar
 * event this app did not create, from the locally-synced cache. Shown as
 * a busy block, never turned into a task. */
export interface ExternalCalendarEvent {
  google_event_id: string;
  title: string | null;
  start: string;
  end: string;
  all_day: boolean;
  status: string;
}

/** Mirrors the backend's CalendarSyncResultOut schema (POST /calendar/sync). */
export interface CalendarSyncResult {
  synced: boolean;
  reason: string | null;
  full_resync: boolean;
  processed: number;
  counts: Record<string, number>;
  watch_active: boolean;
  last_synced_at: string | null;
}

/** Mirrors the backend's NeedsAttentionItemOut schema -- a previously-
 * applied schedule item whose Google Calendar event was deleted
 * externally. Never auto-recreated. */
export interface NeedsAttentionItem {
  task_id: string;
  schedule_item_id: string;
  title: string;
  reason: string | null;
  starts_at: string;
  ends_at: string;
}

export interface BusyInterval {
  start: string;
  end: string;
}

export interface Availability {
  range_start: string;
  range_end: string;
  calendar_id: string;
  busy: BusyInterval[];
}

/** Mirrors the backend's scheduling schemas (backend/app/schemas/scheduling.py). */
export interface ScheduleRequestInput {
  task_ids?: string[];
  horizon_start?: string;
  horizon_end?: string;
}

export interface ProposedScheduleItem {
  task_id: string;
  title: string;
  start: string;
  end: string;
  priority_score: number;
  score: number;
  reason: string;
}

export interface UnscheduledTask {
  task_id: string;
  title: string;
  reason: string;
}

export interface ScheduleProposal {
  horizon_start: string;
  horizon_end: string;
  scheduled: ProposedScheduleItem[];
  unscheduled: UnscheduledTask[];
}

export interface ScheduleApplyItemInput {
  task_id: string;
  start: string;
  end: string;
}

export type AppliedItemStatus = "created" | "already_applied" | "failed";

export interface AppliedItemResult {
  task_id: string;
  status: AppliedItemStatus;
  google_event_id: string | null;
  start: string | null;
  end: string | null;
  reason: string | null;
}

export interface ScheduleApplyResult {
  created: number;
  already_applied: number;
  failed: number;
  results: AppliedItemResult[];
}
