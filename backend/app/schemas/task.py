"""Task schemas.

Field choices are deliberately limited to what `tasks` actually has in
`database/migrations/0001_init.sql` -- see ADR-009 in /docs/decisions.md for
why "category" and "priority" (mentioned in the product brief) are not
user-editable fields here: they're modeled as AI-derived output in
`task_ai_results`, populated in a later phase, not user input on `tasks`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class TaskStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    done = "done"
    cancelled = "cancelled"


class TaskCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    raw_input: str | None = Field(
        default=None,
        max_length=10_000,
        description="Original free-text the user entered, kept for a future AI-prioritization pass.",
    )
    status: TaskStatus = TaskStatus.pending
    due_at: datetime | None = None
    estimated_minutes: int | None = Field(default=None, gt=0, le=24 * 60)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be blank")
        return stripped


class TaskUpdate(BaseModel):
    """All fields optional -- only fields the client actually sent are
    applied (see `model_dump(exclude_unset=True)` in the task service),
    so `PATCH` can distinguish "leave unchanged" from "set to null"."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    raw_input: str | None = Field(default=None, max_length=10_000)
    status: TaskStatus | None = None
    due_at: datetime | None = None
    estimated_minutes: int | None = Field(default=None, gt=0, le=24 * 60)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be blank")
        return stripped


class TaskOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    created_by: uuid.UUID
    title: str
    description: str | None
    raw_input: str | None
    status: TaskStatus
    due_at: datetime | None
    estimated_minutes: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PrioritizedTaskOut(BaseModel):
    """A task joined with its latest AI result (if any) -- the single
    listing the mobile Today/Prioritized-Tasks screens read from, so
    clients don't have to fetch every task's `/ai-result` individually
    (an N+1 pattern) just to sort/filter by priority. Never triggers a new
    Gemini call; `priority_score`/etc. are all null until the task has
    actually been prioritized."""

    id: uuid.UUID
    title: str
    description: str | None
    status: TaskStatus
    due_at: datetime | None
    estimated_minutes: int | None
    created_at: datetime
    priority_score: float | None = None
    # Gemini's `priority_score` plus a deterministic deadline-proximity
    # boost (app/services/priority.py) -- what both clients should actually
    # sort/display by, so an approaching deadline is reflected without
    # waiting for the user to re-tap "Prioritize with AI". Equal to
    # `priority_score` for a task with no due date.
    effective_priority_score: float | None = None
    confidence_score: float | None = None
    urgency: str | None = None
    importance: str | None = None
    category: str | None = None
    # Display grouping of `category` onto the brief's professional/personal/
    # educational buckets -- see app/services/priority.py.
    category_group: str | None = None
    effort_estimate_minutes: int | None = None
    reasoning: str | None = None
