"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { ApiError, completeTask, createTask, deleteTask, listTasks, updateTask } from "@/lib/api/tasks";
import type { Task } from "@/lib/api/types";
import { AiPrioritizationPanel } from "./AiPrioritizationPanel";
import { CalendarConnectionPanel } from "./CalendarConnectionPanel";
import { SchedulePanel } from "./SchedulePanel";

type LoadState = "loading" | "loaded" | "error";

export function TaskList() {
  const supabase = createClient();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);

  async function load() {
    setState("loading");
    setError(null);
    try {
      const result = await listTasks();
      result.sort((a, b) => {
        if (!a.due_at && !b.due_at) return a.created_at.localeCompare(b.created_at);
        if (!a.due_at) return 1;
        if (!b.due_at) return -1;
        return a.due_at.localeCompare(b.due_at);
      });
      setTasks(result);
      setState("loaded");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load tasks.");
      setState("error");
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleComplete(task: Task) {
    try {
      const updated = await completeTask(task.id);
      setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to complete task.");
    }
  }

  async function handleDelete(task: Task) {
    const previous = tasks;
    setTasks((prev) => prev.filter((t) => t.id !== task.id));
    try {
      await deleteTask(task.id);
    } catch (err) {
      setTasks(previous);
      setError(err instanceof ApiError ? err.message : "Failed to delete task.");
    }
  }

  return (
    <div style={{ maxWidth: 640, margin: "32px auto", padding: 24 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>My Tasks</h1>
        <button type="button" onClick={() => supabase.auth.signOut()} style={linkButtonStyle}>
          Sign out
        </button>
      </header>

      {error && (
        <p role="alert" style={{ color: "#c0392b" }}>
          {error}
        </p>
      )}

      <CalendarConnectionPanel />

      <SchedulePanel />

      <CreateTaskForm
        onCreated={(task) => {
          setTasks((prev) => [...prev, task]);
        }}
        onError={setError}
      />

      {state === "loading" && <p>Loading tasks…</p>}

      {state === "error" && (
        <button type="button" onClick={load} style={buttonStyle}>
          Retry
        </button>
      )}

      {state === "loaded" && tasks.length === 0 && (
        <p style={{ color: "#666" }}>No tasks yet. Add your first task above.</p>
      )}

      {state === "loaded" && tasks.length > 0 && (
        <ul style={{ listStyle: "none", padding: 0, marginTop: 16 }}>
          {tasks.map((task) =>
            editingId === task.id ? (
              <EditTaskForm
                key={task.id}
                task={task}
                onSaved={(updated) => {
                  setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
                  setEditingId(null);
                }}
                onCancel={() => setEditingId(null)}
                onError={setError}
              />
            ) : (
              <li key={task.id} style={taskItemStyle}>
                <input
                  type="checkbox"
                  checked={task.status === "done"}
                  onChange={() => handleComplete(task)}
                />
                <div style={{ flex: 1 }}>
                  <div
                    style={{
                      textDecoration: task.status === "done" ? "line-through" : "none",
                      fontWeight: 600,
                    }}
                  >
                    {task.title}
                  </div>
                  {task.description && <div style={{ color: "#666" }}>{task.description}</div>}
                  <div style={{ fontSize: 12, color: "#888" }}>
                    {[
                      task.due_at ? `Due ${task.due_at.slice(0, 10)}` : null,
                      task.estimated_minutes ? `${task.estimated_minutes} min` : null,
                      task.status !== "pending" && task.status !== "done" ? task.status : null,
                    ]
                      .filter(Boolean)
                      .join(" • ")}
                  </div>
                </div>
                <button type="button" onClick={() => setEditingId(task.id)} style={linkButtonStyle}>
                  Edit
                </button>
                <button type="button" onClick={() => handleDelete(task)} style={linkButtonStyle}>
                  Delete
                </button>
              </li>
            )
          )}
        </ul>
      )}
    </div>
  );
}

function CreateTaskForm({
  onCreated,
  onError,
}: {
  onCreated: (task: Task) => void;
  onError: (message: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return;
    setSubmitting(true);
    try {
      const task = await createTask({ title: title.trim() });
      onCreated(task);
      setTitle("");
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Failed to create task.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} style={{ display: "flex", gap: 8, marginTop: 16 }}>
      <input
        type="text"
        placeholder="Add a task…"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        style={{ flex: 1, padding: 8 }}
      />
      <button type="submit" disabled={submitting || !title.trim()} style={buttonStyle}>
        Add
      </button>
    </form>
  );
}

function EditTaskForm({
  task,
  onSaved,
  onCancel,
  onError,
}: {
  task: Task;
  onSaved: (task: Task) => void;
  onCancel: () => void;
  onError: (message: string) => void;
}) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description ?? "");
  const [dueAt, setDueAt] = useState(task.due_at ? task.due_at.slice(0, 10) : "");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) {
      onError("Title is required.");
      return;
    }
    setSubmitting(true);
    try {
      const updated = await updateTask(task.id, {
        title: title.trim(),
        description: description.trim() === "" ? null : description.trim(),
        due_at: dueAt === "" ? null : new Date(dueAt).toISOString(),
      });
      onSaved(updated);
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Failed to save task.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <li style={{ ...taskItemStyle, flexDirection: "column", alignItems: "stretch", gap: 8 }}>
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} style={{ padding: 8 }} />
        <textarea
          placeholder="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          style={{ padding: 8 }}
        />
        <input type="date" value={dueAt} onChange={(e) => setDueAt(e.target.value)} style={{ padding: 8 }} />
        <div style={{ display: "flex", gap: 8 }}>
          <button type="submit" disabled={submitting} style={buttonStyle}>
            Save
          </button>
          <button type="button" onClick={onCancel} style={linkButtonStyle}>
            Cancel
          </button>
        </div>
      </form>
      <AiPrioritizationPanel taskId={task.id} />
    </li>
  );
}

const taskItemStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 8,
  padding: "12px 0",
  borderBottom: "1px solid #eee",
};

const buttonStyle: React.CSSProperties = {
  padding: "8px 16px",
  cursor: "pointer",
};

const linkButtonStyle: React.CSSProperties = {
  background: "none",
  border: "none",
  color: "#2563eb",
  cursor: "pointer",
  padding: 0,
};
