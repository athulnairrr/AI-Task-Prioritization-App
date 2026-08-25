"""Signed, stateless OAuth `state` parameter.

The connect -> Google -> callback round trip has no session/cookie to rely
on (Google's redirect back to `/calendar/callback` is a plain browser
navigation, carrying no Authorization header), so `state` is the only
mechanism available for both CSRF protection and carrying "who initiated
this" across the trip. Rather than a database table of pending OAuth
attempts, `state` is a short-lived, HMAC-signed token (HS256, via PyJWT --
already a dependency) encoding the verified tenant/user id established at
`/calendar/connect` time. `/calendar/callback` trusts a `state` value if and
only if it verifies against `OAUTH_STATE_SECRET` and hasn't expired --
exactly the same "never trust an unverified claim" rule the rest of the
backend follows for JWTs.
"""

from __future__ import annotations

import time
import uuid

import jwt

from app.core.config import get_settings

STATE_TTL_SECONDS = 600  # 10 minutes -- long enough for a user to complete the Google consent screen


class OAuthStateError(Exception):
    """Raised for a missing, expired, tampered, or malformed state value."""


def create_state(*, tenant_id: str, user_id: str) -> str:
    settings = get_settings()
    if not settings.oauth_state_secret:
        raise OAuthStateError("OAUTH_STATE_SECRET is not configured.")
    now = int(time.time())
    payload = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "iat": now,
        "exp": now + STATE_TTL_SECONDS,
        "jti": uuid.uuid4().hex,  # not tracked server-side; just avoids identical tokens for identical requests
    }
    return jwt.encode(payload, settings.oauth_state_secret, algorithm="HS256")


def verify_state(state: str) -> tuple[str, str]:
    """Returns (tenant_id, user_id) if `state` is valid; raises OAuthStateError otherwise."""
    settings = get_settings()
    if not settings.oauth_state_secret:
        raise OAuthStateError("OAUTH_STATE_SECRET is not configured.")
    try:
        payload = jwt.decode(state, settings.oauth_state_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise OAuthStateError(f"Invalid or expired OAuth state: {exc}") from exc
    try:
        return payload["tenant_id"], payload["user_id"]
    except KeyError as exc:
        raise OAuthStateError("OAuth state is missing required fields.") from exc
