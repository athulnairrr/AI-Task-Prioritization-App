"""Persistence for `public.task_ai_results`.

`category`, `urgency`, `priority_score`, `effort_estimate_minutes`, and
`reasoning` map to dedicated columns from Phase 1's schema. `importance`
and `confidence_score` don't have dedicated columns (see ADR-011) and are
stored inside `raw_response` (jsonb) alongside the full validated/clamped
analysis, then read back out for the API response.
"""

from __future__ import annotations

import json
import uuid

import asyncpg

from app.schemas.ai import GeminiTaskAnalysis, TaskAiResultOut

_COLUMNS = (
    "id, task_id, tenant_id, model, priority_score, urgency, "
    "effort_estimate_minutes, category, reasoning, raw_response, created_at"
)


def _to_result_out(row: asyncpg.Record) -> TaskAiResultOut:
    raw = row["raw_response"]
    raw_data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    return TaskAiResultOut(
        id=row["id"],
        task_id=row["task_id"],
        tenant_id=row["tenant_id"],
        model=row["model"],
        category=row["category"],
        urgency=row["urgency"],
        importance=raw_data.get("importance"),
        priority_score=(
            float(row["priority_score"]) if row["priority_score"] is not None else None
        ),
        confidence_score=raw_data.get("confidence_score"),
        effort_estimate_minutes=row["effort_estimate_minutes"],
        reasoning=row["reasoning"],
        created_at=row["created_at"],
    )


async def save_ai_result(
    pool: asyncpg.Pool,
    tenant_id: uuid.UUID,
    task_id: uuid.UUID,
    model: str,
    analysis: GeminiTaskAnalysis,
) -> TaskAiResultOut:
    row = await pool.fetchrow(
        f"""
        insert into public.task_ai_results
            (task_id, tenant_id, model, priority_score, urgency, effort_estimate_minutes, category, reasoning, raw_response)
        values ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        returning {_COLUMNS}
        """,
        task_id,
        tenant_id,
        model,
        analysis.priority_score,
        analysis.urgency,
        analysis.estimated_minutes,
        analysis.category,
        analysis.reasoning,
        json.dumps(analysis.model_dump()),
    )
    return _to_result_out(row)


async def get_latest_ai_result(
    pool: asyncpg.Pool, tenant_id: uuid.UUID, task_id: uuid.UUID
) -> TaskAiResultOut | None:
    row = await pool.fetchrow(
        f"""
        select {_COLUMNS} from public.task_ai_results
        where task_id = $1 and tenant_id = $2
        order by created_at desc
        limit 1
        """,
        task_id,
        tenant_id,
    )
    return _to_result_out(row) if row else None
