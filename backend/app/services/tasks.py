"""Task persistence. Every function here takes an already-verified
`tenant_id` (from `get_tenant_context`/`require_tenant_membership`) and
scopes its query by it -- there is no code path in this module that reads
or writes a task without a tenant_id in the WHERE/SET clause.
"""

from __future__ import annotations

import json
import uuid

import asyncpg

from app.schemas.task import PrioritizedTaskOut, TaskCreate, TaskOut, TaskStatus, TaskUpdate

_COLUMNS = (
    "id, tenant_id, created_by, title, description, raw_input, "
    "status, due_at, estimated_minutes, created_at, updated_at"
)


def _to_task_out(row: asyncpg.Record) -> TaskOut:
    return TaskOut(**dict(row))


async def create_task(
    pool: asyncpg.Pool,
    tenant_id: uuid.UUID,
    created_by: str,
    data: TaskCreate,
) -> TaskOut:
    row = await pool.fetchrow(
        f"""
        insert into public.tasks
            (tenant_id, created_by, title, description, raw_input, status, due_at, estimated_minutes)
        values ($1, $2, $3, $4, $5, $6, $7, $8)
        returning {_COLUMNS}
        """,
        tenant_id,
        created_by,
        data.title,
        data.description,
        data.raw_input,
        data.status.value,
        data.due_at,
        data.estimated_minutes,
    )
    return _to_task_out(row)


async def list_tasks(
    pool: asyncpg.Pool,
    tenant_id: uuid.UUID,
    status_filter: TaskStatus | None,
    limit: int,
    offset: int,
) -> list[TaskOut]:
    if status_filter is not None:
        rows = await pool.fetch(
            f"""
            select {_COLUMNS} from public.tasks
            where tenant_id = $1 and status = $2
            order by due_at asc nulls last, created_at asc
            limit $3 offset $4
            """,
            tenant_id,
            status_filter.value,
            limit,
            offset,
        )
    else:
        rows = await pool.fetch(
            f"""
            select {_COLUMNS} from public.tasks
            where tenant_id = $1
            order by due_at asc nulls last, created_at asc
            limit $2 offset $3
            """,
            tenant_id,
            limit,
            offset,
        )
    return [_to_task_out(r) for r in rows]


async def list_prioritized_tasks(
    pool: asyncpg.Pool,
    tenant_id: uuid.UUID,
    status_filter: TaskStatus | None,
    limit: int,
    offset: int,
) -> list[PrioritizedTaskOut]:
    """Same tasks `list_tasks` would return, left-joined with each task's
    latest `task_ai_results` row (if any) -- one round trip instead of the
    N+1 a client would otherwise need to show priority alongside a task
    list. Ordered by priority score (nulls -- not yet prioritized -- last),
    then the same due-date/created-at fallback as the plain list."""
    where_status = "and t.status = $2" if status_filter is not None else ""
    params: list = [tenant_id]
    if status_filter is not None:
        params.append(status_filter.value)
    limit_idx = len(params) + 1
    offset_idx = len(params) + 2
    params.extend([limit, offset])

    rows = await pool.fetch(
        f"""
        select
            t.id, t.title, t.description, t.status, t.due_at, t.estimated_minutes, t.created_at,
            ar.priority_score, ar.urgency, ar.category, ar.effort_estimate_minutes,
            ar.reasoning, ar.raw_response
        from public.tasks t
        left join lateral (
            select r.priority_score, r.urgency, r.category, r.effort_estimate_minutes,
                   r.reasoning, r.raw_response
            from public.task_ai_results r
            where r.task_id = t.id
            order by r.created_at desc
            limit 1
        ) ar on true
        where t.tenant_id = $1 {where_status}
        order by ar.priority_score desc nulls last, t.due_at asc nulls last, t.created_at asc
        limit ${limit_idx} offset ${offset_idx}
        """,
        *params,
    )
    return [_to_prioritized_task_out(r) for r in rows]


def _to_prioritized_task_out(row: asyncpg.Record) -> PrioritizedTaskOut:
    raw = row["raw_response"]
    raw_data = (json.loads(raw) if isinstance(raw, str) else raw) or {}
    return PrioritizedTaskOut(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        status=TaskStatus(row["status"]),
        due_at=row["due_at"],
        estimated_minutes=row["estimated_minutes"],
        created_at=row["created_at"],
        priority_score=float(row["priority_score"]) if row["priority_score"] is not None else None,
        confidence_score=raw_data.get("confidence_score"),
        urgency=row["urgency"],
        importance=raw_data.get("importance"),
        category=row["category"],
        effort_estimate_minutes=row["effort_estimate_minutes"],
        reasoning=row["reasoning"],
    )


async def get_task(
    pool: asyncpg.Pool, tenant_id: uuid.UUID, task_id: uuid.UUID
) -> TaskOut | None:
    row = await pool.fetchrow(
        f"select {_COLUMNS} from public.tasks where id = $1 and tenant_id = $2",
        task_id,
        tenant_id,
    )
    return _to_task_out(row) if row else None


async def update_task(
    pool: asyncpg.Pool,
    tenant_id: uuid.UUID,
    task_id: uuid.UUID,
    data: TaskUpdate,
) -> TaskOut | None:
    fields = data.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] is not None:
        fields["status"] = TaskStatus(fields["status"]).value

    if not fields:
        return await get_task(pool, tenant_id, task_id)

    set_clauses = []
    values: list = []
    for column, value in fields.items():
        values.append(value)
        set_clauses.append(f"{column} = ${len(values)}")

    values.append(task_id)
    task_id_param = len(values)
    values.append(tenant_id)
    tenant_id_param = len(values)

    row = await pool.fetchrow(
        f"""
        update public.tasks
        set {", ".join(set_clauses)}
        where id = ${task_id_param} and tenant_id = ${tenant_id_param}
        returning {_COLUMNS}
        """,
        *values,
    )
    return _to_task_out(row) if row else None


async def delete_task(pool: asyncpg.Pool, tenant_id: uuid.UUID, task_id: uuid.UUID) -> bool:
    result = await pool.execute(
        "delete from public.tasks where id = $1 and tenant_id = $2",
        task_id,
        tenant_id,
    )
    # asyncpg returns a command tag like "DELETE 1" / "DELETE 0".
    return result.rsplit(" ", 1)[-1] != "0"


async def complete_task(
    pool: asyncpg.Pool, tenant_id: uuid.UUID, task_id: uuid.UUID
) -> TaskOut | None:
    row = await pool.fetchrow(
        f"""
        update public.tasks
        set status = 'done'
        where id = $1 and tenant_id = $2
        returning {_COLUMNS}
        """,
        task_id,
        tenant_id,
    )
    return _to_task_out(row) if row else None
