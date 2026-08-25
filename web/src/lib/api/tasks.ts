import { ApiError, request } from "./client";
import type { Task, TaskAiResult, TaskCreateInput, TaskStatus, TaskUpdateInput } from "./types";

export { ApiError };

export function listTasks(status?: TaskStatus): Promise<Task[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return request<Task[]>(`/tasks${query}`);
}

export function createTask(input: TaskCreateInput): Promise<Task> {
  return request<Task>("/tasks", { method: "POST", body: JSON.stringify(input) });
}

export function updateTask(taskId: string, input: TaskUpdateInput): Promise<Task> {
  return request<Task>(`/tasks/${taskId}`, { method: "PATCH", body: JSON.stringify(input) });
}

export function completeTask(taskId: string): Promise<Task> {
  return request<Task>(`/tasks/${taskId}/complete`, { method: "POST" });
}

export function deleteTask(taskId: string): Promise<void> {
  return request<void>(`/tasks/${taskId}`, { method: "DELETE" });
}

/**
 * Explicit, user-triggered AI prioritization. Never called automatically
 * (not on load, not on refresh) -- only from a direct "Prioritize with AI"
 * click, to keep Gemini usage predictable and free-tier-safe.
 */
export function prioritizeTask(taskId: string): Promise<TaskAiResult> {
  return request<TaskAiResult>(`/tasks/${taskId}/prioritize`, { method: "POST" });
}

/**
 * Fetches the most recent AI result, if any -- does not call Gemini.
 * Returns null if the task hasn't been prioritized yet.
 */
export async function getLatestAiResult(taskId: string): Promise<TaskAiResult | null> {
  try {
    return await request<TaskAiResult>(`/tasks/${taskId}/ai-result`);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}
