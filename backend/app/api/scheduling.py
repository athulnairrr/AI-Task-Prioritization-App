"""POST /tasks/schedule -- proposes a schedule; writes nothing.

This route only *reads*: tasks, their latest AI results, and calendar
availability (via the same connection/token machinery as the Phase 4
calendar routes). It never writes to `schedule_items`, never touches
Google Calendar, and never lets Gemini pick a timestamp -- see
app/services/scheduling.py for the actual (deterministic) decision logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_current_user, get_tenant_context
from app.core.db import get_pool
from app.schemas.auth import AuthenticatedUser, TenantMembership
from app.schemas.scheduling import (
    AppliedItemResult,
    AppliedItemStatus,
    NeedsAttentionItemOut,
    ProposedScheduleItem,
    ScheduleApplyRequest,
    ScheduleApplyResult,
    ScheduleItemOut,
    ScheduleProposal,
    ScheduleRequest,
    UnscheduledTaskOut,
)
from app.schemas.ai import TaskAiResultOut
from app.services import ai_results as ai_results_service
from app.services import calendar_connections as conn_service
from app.services import google_calendar
from app.services import plans as plans_service
from app.services import schedule_apply as schedule_apply_service
from app.services import scheduling as scheduling_engine
from app.services import tasks as task_service

router = APIRouter(prefix="/tasks", tags=["scheduling"])

DEFAULT_HORIZON_DAYS = 14
MAX_HORIZON_DAYS = 60


@router.post("/schedule", response_model=ScheduleProposal)
async def propose_schedule(
    payload: ScheduleRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ScheduleProposal:
    tenant_id = uuid.UUID(tenant.tenant_id)

    now = datetime.now(timezone.utc)
    horizon_start = payload.horizon_start or now
    horizon_end = payload.horizon_end or (horizon_start + timedelta(days=DEFAULT_HORIZON_DAYS))
    if horizon_end <= horizon_start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "horizon_end must be after horizon_start.")
    if horizon_end - horizon_start > timedelta(days=MAX_HORIZON_DAYS):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Horizon too large; request at most {MAX_HORIZON_DAYS} days at a time.",
        )

    schedulable, unscheduled_out, titles = await _load_candidate_tasks(pool, tenant_id, payload.task_ids)

    if not schedulable:
        return ScheduleProposal(
            horizon_start=horizon_start, horizon_end=horizon_end, scheduled=[], unscheduled=unscheduled_out
        )

    connection = await conn_service.get_connection(pool, tenant_id, user.id)
    if connection is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Calendar not connected. Connect Google Calendar to schedule tasks against your availability.",
        )

    try:
        access_token = await conn_service.get_valid_access_token(pool, connection)
    except conn_service.ReauthRequiredError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "REAUTH_REQUIRED", "message": str(exc)},
        ) from exc

    try:
        raw_busy = await google_calendar.query_freebusy(
            access_token=access_token,
            calendar_id=connection.calendar_id,
            time_min=horizon_start,
            time_max=horizon_end,
        )
    except google_calendar.GoogleApiError as exc:
        raise _map_google_error(exc) from exc

    busy_intervals = [
        scheduling_engine.Interval(
            start=datetime.fromisoformat(b["start"]), end=datetime.fromisoformat(b["end"])
        )
        for b in raw_busy
    ]

    constraints = scheduling_engine.SchedulingConstraints(working_hours_timezone=connection.timezone_or_utc)
    result = scheduling_engine.build_schedule(
        schedulable, busy_intervals, horizon_start, horizon_end, constraints
    )

    scheduled_out = [
        ProposedScheduleItem(
            task_id=item.task_id,
            title=titles.get(item.task_id, ""),
            start=item.start,
            end=item.end,
            priority_score=item.priority_score,
            score=item.score,
            reason=item.reason,
        )
        for item in result.scheduled
    ]
    unscheduled_out = unscheduled_out + [
        UnscheduledTaskOut(task_id=u.task_id, title=titles.get(u.task_id, ""), reason=u.reason)
        for u in result.unscheduled
    ]

    return ScheduleProposal(
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        scheduled=scheduled_out,
        unscheduled=unscheduled_out,
    )


@router.post("/schedule/apply", response_model=ScheduleApplyResult)
async def apply_schedule(
    payload: ScheduleApplyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ScheduleApplyResult:
    """Actually writes to Google Calendar -- the only route in the whole
    app that does. Every item is independently revalidated against the
    task's real deadline and freshly-fetched calendar availability before
    anything is created (see app/services/schedule_apply.py); a client's
    start/end is a request, never a fact. One item failing never stops the
    rest -- see /docs/architecture.md "Partial failure behavior"."""
    tenant_id = uuid.UUID(tenant.tenant_id)

    if not payload.items:
        return ScheduleApplyResult(created=0, already_applied=0, failed=0, results=[])

    connection = await conn_service.get_connection(pool, tenant_id, user.id)
    if connection is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Calendar not connected. Connect Google Calendar before applying a schedule.",
        )
    if not connection.has_write_scope:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "CALENDAR_WRITE_SCOPE_REQUIRED",
                "message": "Calendar write permission is required to apply a schedule. "
                "Connect Calendar permissions, then try again.",
            },
        )

    try:
        access_token = await conn_service.get_valid_access_token(pool, connection)
    except conn_service.ReauthRequiredError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "REAUTH_REQUIRED", "message": str(exc)},
        ) from exc

    # One fresh availability snapshot covering the full span of requested
    # items -- each item is still checked individually against it (and
    # against the other items in this same batch) inside apply_schedule_item.
    range_start = min(item.start for item in payload.items)
    range_end = max(item.end for item in payload.items)
    try:
        raw_busy = await google_calendar.query_freebusy(
            access_token=access_token,
            calendar_id=connection.calendar_id,
            time_min=range_start,
            time_max=range_end,
        )
    except google_calendar.GoogleApiError as exc:
        raise _map_google_error(exc) from exc

    fresh_busy = [
        scheduling_engine.Interval(
            start=datetime.fromisoformat(b["start"]), end=datetime.fromisoformat(b["end"])
        )
        for b in raw_busy
    ]

    plan_id = await plans_service.get_or_create_default_plan(pool, tenant_id, user.id)
    other_items = [(item.task_id, item.start, item.end) for item in payload.items]

    results: list[AppliedItemResult] = []
    for item in payload.items:
        result = await schedule_apply_service.apply_schedule_item(
            pool,
            tenant_id=tenant_id,
            connection=connection,
            access_token=access_token,
            item_task_id=item.task_id,
            requested_start=item.start,
            requested_end=item.end,
            plan_id=plan_id,
            fresh_busy=fresh_busy,
            other_items_in_batch=other_items,
        )
        results.append(result)

    created = sum(1 for r in results if r.status == AppliedItemStatus.created)
    already_applied = sum(1 for r in results if r.status == AppliedItemStatus.already_applied)
    failed = sum(1 for r in results if r.status == AppliedItemStatus.failed)

    return ScheduleApplyResult(
        created=created, already_applied=already_applied, failed=failed, results=results
    )


@router.get("/schedule/items", response_model=list[ScheduleItemOut])
async def list_schedule_items(
    start: datetime,
    end: datetime,
    task_id: uuid.UUID | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[ScheduleItemOut]:
    """Applied schedule items overlapping `[start, end)` -- the single
    source of truth the mobile Today/Calendar/task-detail screens read
    "what's on my plan" from, joining in the task title, latest AI
    priority score, and Calendar mapping status so clients don't have to
    stitch three calls together themselves. `task_id` narrows to one
    task's schedule item (used by the task detail screen with a wide
    date range, rather than requiring the client to guess a narrow one)."""
    tenant_id = uuid.UUID(tenant.tenant_id)
    if end <= start:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "`end` must be after `start`.")
    rows = await pool.fetch(
        """
        select
            si.id as schedule_item_id, si.task_id, t.title, si.starts_at, si.ends_at,
            si.status, si.needs_attention, si.attention_reason,
            m.google_event_id, m.sync_status,
            ar.priority_score
        from public.schedule_items si
        join public.tasks t on t.id = si.task_id
        left join public.google_calendar_event_mappings m on m.schedule_item_id = si.id
        left join lateral (
            select r.priority_score
            from public.task_ai_results r
            where r.task_id = si.task_id
            order by r.created_at desc
            limit 1
        ) ar on true
        where si.tenant_id = $1 and si.starts_at < $3 and si.ends_at > $2
          and ($4::uuid is null or si.task_id = $4)
        order by si.starts_at
        """,
        tenant_id,
        start,
        end,
        task_id,
    )
    return [
        ScheduleItemOut(
            schedule_item_id=r["schedule_item_id"],
            task_id=r["task_id"],
            title=r["title"],
            starts_at=r["starts_at"],
            ends_at=r["ends_at"],
            status=r["status"],
            needs_attention=r["needs_attention"],
            attention_reason=r["attention_reason"],
            google_event_id=r["google_event_id"],
            sync_status=r["sync_status"],
            priority_score=float(r["priority_score"]) if r["priority_score"] is not None else None,
        )
        for r in rows
    ]


@router.get("/schedule/needs-attention", response_model=list[NeedsAttentionItemOut])
async def list_needs_attention(
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[NeedsAttentionItemOut]:
    """Previously-applied schedule items whose Google Calendar event was
    deleted externally (see app/services/calendar_sync.py "External
    Calendar changes: deleted") -- never auto-recreated, surfaced here so
    the client can prompt the user to re-apply or otherwise deal with it."""
    tenant_id = uuid.UUID(tenant.tenant_id)
    rows = await pool.fetch(
        """
        select si.task_id, si.id as schedule_item_id, t.title, si.attention_reason, si.starts_at, si.ends_at
        from public.schedule_items si
        join public.tasks t on t.id = si.task_id
        where si.tenant_id = $1 and si.needs_attention = true
        order by si.starts_at
        """,
        tenant_id,
    )
    return [
        NeedsAttentionItemOut(
            task_id=r["task_id"],
            schedule_item_id=r["schedule_item_id"],
            title=r["title"],
            reason=r["attention_reason"],
            starts_at=r["starts_at"],
            ends_at=r["ends_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_candidate_tasks(
    pool: asyncpg.Pool, tenant_id: uuid.UUID, task_ids: list[uuid.UUID] | None
) -> tuple[list[scheduling_engine.SchedulableTask], list[UnscheduledTaskOut], dict[uuid.UUID, str]]:
    """Returns (schedulable tasks, already-unscheduled entries e.g. "not
    found"/"no duration", a task_id -> title lookup for building the
    response). Never raises for an individual bad task id -- those are
    reported in the proposal, not a failed request."""
    schedulable: list[scheduling_engine.SchedulableTask] = []
    unscheduled: list[UnscheduledTaskOut] = []
    titles: dict[uuid.UUID, str] = {}

    if task_ids:
        for task_id in task_ids:
            task = await task_service.get_task(pool, tenant_id, task_id)
            if task is None:
                unscheduled.append(UnscheduledTaskOut(task_id=task_id, title="", reason="Task not found."))
                continue
            titles[task.id] = task.title
            ai_result = await ai_results_service.get_latest_ai_result(pool, tenant_id, task_id)
            item, error = _to_schedulable(task.id, task.due_at, task.estimated_minutes, ai_result)
            if error:
                unscheduled.append(UnscheduledTaskOut(task_id=task_id, title=task.title, reason=error))
            else:
                schedulable.append(item)
    else:
        rows = await pool.fetch(
            """
            select t.id, t.title, t.due_at, t.estimated_minutes
            from public.tasks t
            where t.tenant_id = $1 and t.status = 'pending'
              and exists (select 1 from public.task_ai_results r where r.task_id = t.id)
            order by t.created_at
            """,
            tenant_id,
        )
        for row in rows:
            titles[row["id"]] = row["title"]
            ai_result = await ai_results_service.get_latest_ai_result(pool, tenant_id, row["id"])
            item, error = _to_schedulable(row["id"], row["due_at"], row["estimated_minutes"], ai_result)
            if error:
                unscheduled.append(UnscheduledTaskOut(task_id=row["id"], title=row["title"], reason=error))
            else:
                schedulable.append(item)

    return schedulable, unscheduled, titles


def _to_schedulable(
    task_id: uuid.UUID,
    due_at: datetime | None,
    estimated_minutes: int | None,
    ai_result: TaskAiResultOut | None,
) -> tuple[scheduling_engine.SchedulableTask | None, str | None]:
    if ai_result is not None:
        priority = (
            ai_result.priority_score
            if ai_result.priority_score is not None
            else scheduling_engine.DEFAULT_CONSTRAINTS.default_priority_score
        )
        duration = ai_result.effort_estimate_minutes or estimated_minutes
    else:
        priority = scheduling_engine.DEFAULT_CONSTRAINTS.default_priority_score
        duration = estimated_minutes

    if not duration or duration <= 0:
        return None, "No duration estimate available -- run AI prioritization or set an estimated duration."

    return (
        scheduling_engine.SchedulableTask(
            task_id=task_id,
            title="",  # filled in from the `titles` lookup by the caller
            priority_score=priority,
            duration_minutes=duration,
            deadline=due_at,
            has_ai_result=ai_result is not None,
        ),
        None,
    )


def _map_google_error(exc: google_calendar.GoogleApiError) -> HTTPException:
    if exc.status_code == 429:
        return HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Google Calendar rate limit reached. Please try again shortly.",
        )
    if exc.status_code in (401, 403):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "REAUTH_REQUIRED", "message": "Google Calendar access was denied."},
        )
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "Google Calendar is temporarily unavailable.")
