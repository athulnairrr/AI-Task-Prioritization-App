"""Supabase JWT verification.

Supabase-issued access tokens are signed with the project's JWT signing key.
Newer projects (this one included) use asymmetric keys (ES256/RS256)
published at `${SUPABASE_URL}/auth/v1/.well-known/jwks.json`; we verify
against that JWKS rather than trusting any client-supplied identity.

The backend never accepts a user id or tenant id from the request body/query
string as authoritative -- the only trusted source of "who is calling" is the
`sub` claim of a token that verifies against Supabase's own signing key.
"""

from __future__ import annotations

from functools import lru_cache

import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from app.core.config import get_settings

EXPECTED_AUDIENCE = "authenticated"

# Tolerate a few seconds of clock drift between this machine and Supabase's
# token issuer when validating `iat`/`exp`/`nbf` -- without this, a token
# minted moments ago can spuriously fail as "not yet valid" whenever the
# local clock lags the issuer's by even a second or two.
CLOCK_SKEW_LEEWAY_SECONDS = 10


@lru_cache
def _jwk_client() -> PyJWKClient:
    settings = get_settings()
    if not settings.supabase_jwks_url:
        raise RuntimeError("SUPABASE_JWKS_URL is not configured")
    # Cache the JWKS response so we don't fetch it on every request.
    return PyJWKClient(settings.supabase_jwks_url, cache_keys=True, lifespan=3600)


def decode_supabase_jwt(token: str) -> dict:
    """Verify a Supabase access token and return its claims.

    Raises HTTPException(401) for any invalid/expired/malformed token.
    """
    try:
        signing_key = _jwk_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience=EXPECTED_AUDIENCE,
            options={"require": ["exp", "sub"]},
            leeway=CLOCK_SKEW_LEEWAY_SECONDS,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return claims
