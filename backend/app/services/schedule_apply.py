"""Applies one approved schedule item: revalidates it, creates the Google
Calendar event, and persists the result -- idempotently.

Idempotency design (see /docs/architecture.md "Idempotency & partial
failure" for the full writeup): a `schedule_items` row is matched by
`(tenant_id, task_id)` -- this MVP allows at most one active schedule per
task. Its `google_calendar_event_mappings` row (joined via
`schedule_item_id`, which is UNIQUE) is the source of truth for "was the
Google event actually created": a mapping only ever gets inserted *after*
`create_event` succeeds, using the real `google_event_id` Google returned.
A failed attempt leaves the `schedule_items` row (so a retry updates it
rather than creating a duplicate) but **no** mapping row -- deliberately,
since `google_event_id` is `NOT NULL` + unique per connection, so there is
no sentinel value that could represent "failed, no event yet" without
risking a spurious unique-constraint collision between two unrelated
failures. "No mapping row" already means exactly that, for free.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg

from app.schemas.ai import TaskAiResultOut
from app.schemas.scheduling import AppliedItemResult, AppliedItemStatus
from app.schemas.task import TaskOut
from app.services import ai_results as ai_results_service
from app.services import calendar_connections as conn_service
from app.services import google_calendar
from app.services import tasks as task_service
from app.services.scheduling import Interval

APP_IDENTIFIER = "ai-work-planner"


async def apply_schedule_item(
    pool: asyncpg.Pool,
    *,
    tenant_id: uuid.UUID,
    connection: conn_service.ConnectionRecord,
    access_token: str,
    item_task_id: uuid.UUID,
    requested_start: datetime,
    requested_end: datetime,
    plan_id: uuid.UUID,
    fresh_busy: list[Interval],
    other_items_in_batch: list[tuple[uuid.UUID, datetime, datetime]],
) -> AppliedItemResult:
    task = await task_service.get_task(pool, tenant_id, item_task_id)
    if task is None:
        return AppliedItemResult(
            task_id=item_task_id, status=AppliedItemStatus.failed, reason="Task not found."
        )

    existing = await _get_existing_schedule(pool, tenant_id, item_task_id)
    if existing is not None and existing["sync_status"] == "synced":
        return AppliedItemResult(
            task_id=item_task_id,
            status=AppliedItemStatus.already_applied,
            google_event_id=existing["google_event_id"],
            start=existing["starts_at"],
            end=existing["ends_at"],
            reason="Already applied to Google Calendar.",
        )

    invalid_reason = _revalidate(
        task, requested_start, requested_end, fresh_busy, other_items_in_batch, item_task_id
    )
    if invalid_reason:
        return AppliedItemResult(task_id=item_task_id, status=AppliedItemStatus.failed, reason=invalid_reason)

    schedule_item_id = await _upsert_schedule_item(
        pool, tenant_id, plan_id, item_task_id, requested_start, requested_end, existing
    )

    ai_result = await ai_results_service.get_latest_ai_result(pool, tenant_id, item_task_id)
    try:
        event = await google_calendar.create_event(
            access_token=access_token,
            calendar_id=connection.calendar_id,
            summary=task.title,
            description=_build_description(task, ai_result),
            start=requested_start,
            end=requested_end,
            time_zone=connection.timezone_or_utc,
            private_properties={
                "app": APP_IDENTIFIER,
                "task_id": str(item_task_id),
                "tenant_id": str(tenant_id),
                "schedule_item_id": str(schedule_item_id),
            },
        )
    except google_calendar.GoogleApiError as exc:
        return AppliedItemResult(
            task_id=item_task_id, status=AppliedItemStatus.failed, reason=_reason_for(exc)
        )

    google_event_id = event["id"]
    google_updated_at = _parse_google_timestamp(event.get("updated"))
    await _mark_synced(pool, tenant_id, schedule_item_id, connection.id, google_event_id, google_updated_at)

    return AppliedItemResult(
        task_id=item_task_id,
        status=AppliedItemStatus.created,
        google_event_id=google_event_id,
        start=requested_start,
        end=requested_end,
    )


def _revalidate(
    task: TaskOut,
    start: datetime,
    end: datetime,
    fresh_busy: list[Interval],
    other_items_in_batch: list[tuple[uuid.UUID, datetime, datetime]],
    item_task_id: uuid.UUID,
) -> str | None:
    """Returns a failure reason, or None if the requested slot is valid.
    Never trusts the client-supplied start/end without checking them
    against the task's real deadline and freshly-fetched calendar
    availability -- the whole point of this function."""
    if end <= start:
        return "`end` must be after `start`."
    if task.due_at and end > task.due_at:
        return f"Proposed time is after the task's deadline ({task.due_at.isoformat()})."

    requested = Interval(start, end)
    for busy in fresh_busy:
        if requested.overlaps(busy):
            return "That time is no longer available on your calendar."

    for other_task_id, other_start, other_end in other_items_in_batch:
        if other_task_id == item_task_id:
            continue
        if start < other_end and other_start < end:
            return "Overlaps another task in this same request."

    return None


async def _get_existing_schedule(
    pool: asyncpg.Pool, tenant_id: uuid.UUID, task_id: uuid.UUID
) -> asyncpg.Record | None:
    return await pool.fetchrow(
        """
        select si.id as schedule_item_id, si.starts_at, si.ends_at,
               m.google_event_id, m.sync_status
        from public.schedule_items si
        left join public.google_calendar_event_mappings m on m.schedule_item_id = si.id
        where si.tenant_id = $1 and si.task_id = $2
        order by si.created_at desc
        limit 1
        """,
        tenant_id,
        task_id,
    )


async def _upsert_schedule_item(
    pool: asyncpg.Pool,
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    task_id: uuid.UUID,
    start: datetime,
    end: datetime,
    existing: asyncpg.Record | None,
) -> uuid.UUID:
    if existing is not None:
        await pool.execute(
            """
            update public.schedule_items
            set starts_at = $1, ends_at = $2, status = 'scheduled', needs_attention = false, attention_reason = null
            where id = $3
            """,
            start,
            end,
            existing["schedule_item_id"],
        )
        return existing["schedule_item_id"]

    return await pool.fetchval(
        """
        insert into public.schedule_items (tenant_id, plan_id, task_id, starts_at, ends_at, status)
        values ($1, $2, $3, $4, $5, 'scheduled')
        returning id
        """,
        tenant_id,
        plan_id,
        task_id,
        start,
        end,
    )


async def _mark_synced(
    pool: asyncpg.Pool,
    tenant_id: uuid.UUID,
    schedule_item_id: uuid.UUID,
    connection_id: uuid.UUID,
    google_event_id: str,
    google_updated_at: datetime | None,
) -> None:
    # google_updated_at is Google's own `updated` timestamp from the
    # create_event response -- recording it here (not just on the next
    # sync pass) means the very first incremental sync that picks up this
    # event recognizes it as "already known, no newer version" and skips
    # it, rather than misreading our own creation as an external change.
    # See app/services/calendar_sync.py "Loop prevention".
    await pool.execute(
        """
        insert into public.google_calendar_event_mappings
            (tenant_id, schedule_item_id, connection_id, google_event_id, sync_status, last_synced_at, last_error, google_updated_at)
        values ($1, $2, $3, $4, 'synced', now(), null, $5)
        on conflict (schedule_item_id) do update set
            connection_id = excluded.connection_id,
            google_event_id = excluded.google_event_id,
            sync_status = 'synced',
            last_synced_at = now(),
            last_error = null,
            google_updated_at = excluded.google_updated_at
        """,
        tenant_id,
        schedule_item_id,
        connection_id,
        google_event_id,
        google_updated_at,
    )


def _parse_google_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _reason_for(exc: google_calendar.GoogleApiError) -> str:
    if exc.status_code == 429:
        return "Google Calendar rate limit reached. Try applying again shortly."
    if exc.status_code in (401, 403):
        return "Google Calendar access was denied while creating this event."
    return f"Google Calendar error: {exc}"


def _build_description(task: TaskOut, ai_result: TaskAiResultOut | None) -> str:
    lines: list[str] = []
    if task.description:
        lines.append(task.description)
    if ai_result:
        facts = []
        if ai_result.category:
            facts.append(f"Category: {ai_result.category}")
        if ai_result.priority_score is not None:
            facts.append(f"Priority: {ai_result.priority_score:.0f}")
        if ai_result.urgency:
            facts.append(f"Urgency: {ai_result.urgency}")
        if facts:
            lines.append(" | ".join(facts))
    lines.append("Scheduled by AI Work Planner.")
    return "\n\n".join(lines)
