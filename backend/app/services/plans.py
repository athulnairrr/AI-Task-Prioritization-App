"""Minimal wrapper around `public.plans`, used only so `schedule_items`
(which requires a `plan_id`, not nullable -- see 0001_init.sql) has
somewhere to attach to when a schedule is applied. This phase doesn't
implement plan management as a feature -- no create/list/edit routes --
it just needs *a* plan to exist per tenant, auto-created on first use.
"""

from __future__ import annotations

import uuid

import asyncpg


async def get_or_create_default_plan(pool: asyncpg.Pool, tenant_id: uuid.UUID, created_by: str) -> uuid.UUID:
    existing = await pool.fetchval(
        """
        select id from public.plans
        where tenant_id = $1 and status in ('draft', 'active')
        order by created_at
        limit 1
        """,
        tenant_id,
    )
    if existing is not None:
        return existing

    return await pool.fetchval(
        """
        insert into public.plans (tenant_id, created_by, name, status)
        values ($1, $2, 'My Plan', 'active')
        returning id
        """,
        tenant_id,
        created_by,
    )
