"""Scheduling API request/response shapes. See app/services/scheduling.py
for the actual algorithm -- these models are just its I/O boundary."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class ScheduleRequest(BaseModel):
    """Omit `task_ids` to consider every unscheduled, already-prioritized
    task in the tenant (status='pending' with at least one AI result).
    Omit `horizon_start`/`horizon_end` to default to now .. now+14 days."""

    task_ids: list[uuid.UUID] | None = None
    horizon_start: datetime | None = None
    horizon_end: datetime | None = None


class ProposedScheduleItem(BaseModel):
    task_id: uuid.UUID
    title: str
    start: datetime
    end: datetime
    priority_score: float
    score: float
    reason: str


class UnscheduledTaskOut(BaseModel):
    task_id: uuid.UUID
    title: str
    reason: str


class ScheduleProposal(BaseModel):
    horizon_start: datetime
    horizon_end: datetime
    scheduled: list[ProposedScheduleItem]
    unscheduled: list[UnscheduledTaskOut]


class ScheduleApplyItem(BaseModel):
    """One item the user approved from a proposal. The backend re-validates
    `start`/`end` against the task's real deadline/duration and fresh
    calendar availability before ever writing anything -- these values are
    a *request*, not a fact (see /docs/architecture.md "Apply endpoint
    revalidation"; never trusted blindly per the phase brief)."""

    task_id: uuid.UUID
    start: datetime
    end: datetime


class ScheduleApplyRequest(BaseModel):
    items: list[ScheduleApplyItem]


class AppliedItemStatus(str, Enum):
    created = "created"
    already_applied = "already_applied"
    failed = "failed"


class AppliedItemResult(BaseModel):
    task_id: uuid.UUID
    status: AppliedItemStatus
    google_event_id: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    reason: str | None = None


class ScheduleApplyResult(BaseModel):
    created: int
    already_applied: int
    failed: int
    results: list[AppliedItemResult]


class ScheduleItemOut(BaseModel):
    """One applied schedule item in a date range -- the source of truth
    for "what's on my plan today" (mobile Today/Calendar screens), joining
    in just enough from the task/AI-result/Calendar-mapping tables that
    clients don't have to make three separate calls and stitch it
    together themselves."""

    schedule_item_id: uuid.UUID
    task_id: uuid.UUID
    title: str
    starts_at: datetime
    ends_at: datetime
    status: str
    needs_attention: bool
    attention_reason: str | None = None
    google_event_id: str | None = None
    sync_status: str | None = None
    priority_score: float | None = None


class NeedsAttentionItemOut(BaseModel):
    """A previously-applied schedule item whose Google Calendar event was
    deleted externally (see app/services/calendar_sync.py). The app never
    silently recreates it -- this is how it's surfaced instead; re-applying
    the task (POST /tasks/schedule/apply) creates a fresh event and clears
    this."""

    task_id: uuid.UUID
    schedule_item_id: uuid.UUID
    title: str
    reason: str | None = None
    starts_at: datetime
    ends_at: datetime
