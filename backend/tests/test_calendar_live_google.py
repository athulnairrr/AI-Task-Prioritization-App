"""Controlled live smoke test against the real Google OAuth/Calendar APIs.

A *complete* end-to-end OAuth test (through Google's actual consent screen)
requires a human clicking "Allow" in a real browser -- that isn't something
this suite can script, and isn't attempted here. What this file verifies
against the real Google endpoints, without needing that:

  1. The registered OAuth client (`GOOGLE_OAUTH_CLIENT_ID` +
     `GOOGLE_OAUTH_REDIRECT_URI`) is actually valid and accepted by Google --
     i.e. the Cloud Console setup (client, redirect URI, Calendar API
     enabled) is correct, not just internally self-consistent.
  2. This backend's error-shape assumptions (`invalid_grant` from the token
     endpoint, 401 from the Calendar/userinfo APIs) match what Google
     actually returns -- these were captured from real responses during
     development of this phase and are pinned here as regression coverage.

Skipped (not failed) unless Google OAuth credentials are configured.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core.config import get_settings
from app.core.oauth_state import create_state
from app.services import google_calendar

settings = get_settings()

GOOGLE_CONFIGURED = bool(
    settings.google_oauth_client_id
    and settings.google_oauth_client_secret
    and settings.google_oauth_redirect_uri
    and settings.oauth_state_secret
)

pytestmark = pytest.mark.skipif(
    not GOOGLE_CONFIGURED,
    reason="Google OAuth credentials not configured (GOOGLE_OAUTH_CLIENT_ID/SECRET, GOOGLE_OAUTH_REDIRECT_URI)",
)


def test_authorization_url_is_accepted_by_google():
    """Fetches the real authorization URL and confirms Google's server
    returns its sign-in/consent page rather than an OAuth client error
    (invalid_client, redirect_uri_mismatch) -- this is the strongest check
    possible without a human completing the consent screen."""
    state = create_state(tenant_id="live-smoke-test-tenant", user_id="live-smoke-test-user")
    url = google_calendar.build_authorization_url(state=state)

    resp = httpx.get(url, follow_redirects=True, timeout=15)
    assert resp.status_code == 200

    text_lower = resp.text.lower()
    for error_indicator in ("invalid_client", "redirect_uri_mismatch", "error 400"):
        assert error_indicator not in text_lower, (
            f"Google rejected the OAuth client/redirect URI ({error_indicator}) -- "
            "check GOOGLE_OAUTH_CLIENT_ID and the redirect URI registered on the "
            "Cloud Console OAuth client match GOOGLE_OAUTH_REDIRECT_URI exactly."
        )


def test_refresh_with_invalid_token_returns_invalid_grant():
    async def _run():
        with pytest.raises(google_calendar.GoogleApiError) as exc_info:
            await google_calendar.refresh_access_token(refresh_token="not-a-real-refresh-token")
        assert exc_info.value.error_code == "invalid_grant"
        assert exc_info.value.status_code == 400

    asyncio.run(_run())


def test_userinfo_with_invalid_token_returns_401():
    async def _run():
        with pytest.raises(google_calendar.GoogleApiError) as exc_info:
            await google_calendar.get_userinfo(access_token="not-a-real-access-token")
        assert exc_info.value.status_code == 401

    asyncio.run(_run())


def test_freebusy_with_invalid_token_returns_401():
    from datetime import datetime, timedelta, timezone

    async def _run():
        now = datetime.now(timezone.utc)
        with pytest.raises(google_calendar.GoogleApiError) as exc_info:
            await google_calendar.query_freebusy(
                access_token="not-a-real-access-token",
                calendar_id="primary",
                time_min=now,
                time_max=now + timedelta(days=1),
            )
        assert exc_info.value.status_code == 401

    asyncio.run(_run())
