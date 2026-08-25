"""Task CRUD routes.

All routes require authentication and a resolved tenant (`get_tenant_context`
-- see app/api/deps.py) before any query runs. `created_by` on create is
always the verified caller's id, never a client-supplied value.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user, get_tenant_context
from app.core.config import get_settings
from app.core.db import get_pool
from app.schemas.ai import TaskAiResultOut
from app.schemas.auth import AuthenticatedUser, TenantMembership
from app.schemas.task import PrioritizedTaskOut, TaskCreate, TaskOut, TaskStatus, TaskUpdate
from app.services import ai_results as ai_results_service
from app.services import tasks as task_service
from app.services.ai import AiPrioritizationError, AiPrioritizationService, get_ai_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> TaskOut:
    return await task_service.create_task(pool, uuid.UUID(tenant.tenant_id), user.id, payload)


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[TaskOut]:
    return await task_service.list_tasks(
        pool, uuid.UUID(tenant.tenant_id), status_filter, limit, offset
    )


@router.get("/prioritized", response_model=list[PrioritizedTaskOut])
async def list_prioritized_tasks(
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[PrioritizedTaskOut]:
    """Tasks joined with their latest AI result -- powers the mobile
    Today screen's "high priority" list and the Prioritized Tasks screen.
    Never calls Gemini; registered before `/{task_id}` so the literal path
    `prioritized` is matched first (same reasoning as `/tasks/schedule` in
    app/main.py)."""
    return await task_service.list_prioritized_tasks(
        pool, uuid.UUID(tenant.tenant_id), status_filter, limit, offset
    )


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID,
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> TaskOut:
    task = await task_service.get_task(pool, uuid.UUID(tenant.tenant_id), task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    return task


@router.patch("/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> TaskOut:
    task = await task_service.update_task(pool, uuid.UUID(tenant.tenant_id), task_id, payload)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_task(
    task_id: uuid.UUID,
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> None:
    deleted = await task_service.delete_task(pool, uuid.UUID(tenant.tenant_id), task_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")


@router.post("/{task_id}/complete", response_model=TaskOut)
async def complete_task(
    task_id: uuid.UUID,
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> TaskOut:
    task = await task_service.complete_task(pool, uuid.UUID(tenant.tenant_id), task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    return task


@router.post("/{task_id}/prioritize", response_model=TaskAiResultOut)
async def prioritize_task(
    task_id: uuid.UUID,
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    ai_service: AiPrioritizationService = Depends(get_ai_service),
) -> TaskAiResultOut:
    """Explicit, user-triggered AI prioritization -- never called from task
    creation or any read path (see /docs/architecture.md "Gemini
    integration" for the cost-control rationale). Calling this again for
    the same task re-prioritizes it (a new task_ai_results row); it does
    not overwrite the previous one.
    """
    tenant_id = uuid.UUID(tenant.tenant_id)
    task = await task_service.get_task(pool, tenant_id, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")

    try:
        analysis = await ai_service.analyze(
            title=task.title, description=task.description, raw_input=task.raw_input
        )
    except AiPrioritizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI prioritization is temporarily unavailable: {exc}",
        ) from exc

    clamped = analysis.clamp()
    settings = get_settings()
    return await ai_results_service.save_ai_result(pool, tenant_id, task_id, settings.gemini_model, clamped)


@router.get("/{task_id}/ai-result", response_model=TaskAiResultOut)
async def get_task_ai_result(
    task_id: uuid.UUID,
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> TaskAiResultOut:
    """Returns the most recent AI result for a task, if one exists -- never
    triggers a new Gemini call. Use POST .../prioritize for that."""
    tenant_id = uuid.UUID(tenant.tenant_id)
    result = await ai_results_service.get_latest_ai_result(pool, tenant_id, task_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No AI result yet for this task.",
        )
    return result
