from fastapi import APIRouter, Depends

from app.api.deps import get_current_user, require_tenant_membership
from app.core.db import get_pool
from app.schemas.auth import AuthenticatedUser, TenantMembership

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=AuthenticatedUser)
async def read_current_user(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    """Returns the identity derived from the caller's verified JWT.

    Exists so auth can be tested end-to-end (issue a token -> call this
    route) without any task/tenant feature work.
    """
    return user


@router.get("/tenants/me", response_model=list[TenantMembership])
async def list_my_tenants(
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[TenantMembership]:
    """Lists tenants the authenticated user belongs to, looked up by the
    verified user id -- never by anything the client claims about itself."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        select t.id as tenant_id, t.name as tenant_name, tm.role
        from public.tenant_members tm
        join public.tenants t on t.id = tm.tenant_id
        where tm.user_id = $1
        order by t.created_at
        """,
        user.id,
    )
    return [
        TenantMembership(tenant_id=str(r["tenant_id"]), tenant_name=r["tenant_name"], role=r["role"])
        for r in rows
    ]


@router.get("/tenants/{tenant_id}", response_model=TenantMembership)
async def get_tenant(
    membership: TenantMembership = Depends(require_tenant_membership),
) -> TenantMembership:
    """Returns the caller's membership in `tenant_id`, or 403 if they don't
    belong to it -- proves tenant membership is re-checked server-side and
    never inferred from the URL alone."""
    return membership
