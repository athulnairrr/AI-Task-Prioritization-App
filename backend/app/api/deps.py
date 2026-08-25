"""Shared FastAPI dependencies for authentication and tenant authorization.

Rule of thumb enforced here: a user id or tenant id is only ever trusted once
it has been derived from a verified JWT (`get_current_user`) or looked up in
the database against that verified user id (`require_tenant_membership`,
`get_tenant_context`). Client-supplied ids in the path/query/body are
treated as *requests*, never as *facts*.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import Depends, HTTPException, Path, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.db import get_pool
from app.core.security import decode_supabase_jwt
from app.schemas.auth import AuthenticatedUser, TenantMembership

_bearer_scheme = HTTPBearer(auto_error=True)

_MEMBERSHIP_BY_TENANT_SQL = """
    select t.id as tenant_id, t.name as tenant_name, tm.role
    from public.tenant_members tm
    join public.tenants t on t.id = tm.tenant_id
    where tm.tenant_id = $1 and tm.user_id = $2
    """

_ALL_MEMBERSHIPS_SQL = """
    select t.id as tenant_id, t.name as tenant_name, tm.role
    from public.tenant_members tm
    join public.tenants t on t.id = tm.tenant_id
    where tm.user_id = $1
    order by t.created_at
    """


def _row_to_membership(row: asyncpg.Record) -> TenantMembership:
    return TenantMembership(
        tenant_id=str(row["tenant_id"]),
        tenant_name=row["tenant_name"],
        role=row["role"],
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    """Verify the bearer token and return the authenticated user's identity.

    This is the only place a "current user" is established -- every other
    dependency/route builds on this rather than reading a user id from the
    request itself.
    """
    claims = decode_supabase_jwt(credentials.credentials)
    return AuthenticatedUser(id=claims["sub"], email=claims.get("email"))


async def require_tenant_membership(
    tenant_id: uuid.UUID = Path(...),
    user: AuthenticatedUser = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> TenantMembership:
    """Confirm the authenticated user actually belongs to `tenant_id`.

    The tenant id still comes from the URL path, but it is never trusted on
    its own -- membership is re-checked against the database on every call
    using the verified user id from the token, not anything the client
    asserts about itself.
    """
    row = await pool.fetchrow(_MEMBERSHIP_BY_TENANT_SQL, tenant_id, user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this tenant.",
        )
    return _row_to_membership(row)


async def get_tenant_context(
    tenant_id: uuid.UUID | None = Query(
        default=None,
        description="Tenant to operate in. Optional if the account only belongs to one tenant.",
    ),
    user: AuthenticatedUser = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> TenantMembership:
    """Resolve which tenant a flat, non-tenant-scoped route (e.g. `/tasks`)
    should operate on.

    If `tenant_id` is supplied, membership is verified exactly like
    `require_tenant_membership` -- a client can send any id, but only a real
    membership row lets the request through. If it's omitted, the caller's
    own memberships are looked up and used only when unambiguous (most MVP
    accounts belong to exactly one, their personal tenant); an account
    belonging to more than one tenant must specify `tenant_id` explicitly.
    """
    if tenant_id is not None:
        row = await pool.fetchrow(_MEMBERSHIP_BY_TENANT_SQL, tenant_id, user.id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this tenant.",
            )
        return _row_to_membership(row)

    rows = await pool.fetch(_ALL_MEMBERSHIPS_SQL, user.id)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This account has no tenant.",
        )
    if len(rows) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account belongs to multiple tenants; specify ?tenant_id=.",
        )
    return _row_to_membership(rows[0])
