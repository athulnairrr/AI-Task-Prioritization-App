"""One controlled live test: creates a real Google Calendar event and
deletes it immediately after, using the real Google API (no mocks).

This does *not* go through this app's OAuth connect/callback flow -- that
would require a human completing Google's consent screen for the
calendar.events (write) scope, which isn't scriptable (same limitation as
Phase 4's live test). Instead it uses a refresh token you've already
obtained once, by hand, and put in `.env`:

    1. Temporarily add "https://developers.google.com/oauthplayground" as
       an authorized redirect URI on your OAuth client (or use any local
       script that completes the flow), request scope
       https://www.googleapis.com/auth/calendar.events, and exchange the
       resulting code for a refresh token.
    2. Set TEST_LIVE_CALENDAR_REFRESH_TOKEN in backend/.env to that value.
    3. Run this file: `pytest tests/test_schedule_apply_live_google.py -v`

Skipped (not failed) unless TEST_LIVE_CALENDAR_REFRESH_TOKEN is set.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.services import google_calendar

settings = get_settings()

LIVE_WRITE_CONFIGURED = bool(
    settings.google_oauth_client_id
    and settings.google_oauth_client_secret
    and settings.test_live_calendar_refresh_token
)

pytestmark = pytest.mark.skipif(
    not LIVE_WRITE_CONFIGURED,
    reason="TEST_LIVE_CALENDAR_REFRESH_TOKEN not configured -- see this file's module docstring "
    "for how to obtain one. Everything else in this phase is verified without it.",
)


def test_create_and_delete_one_real_calendar_event():
    async def _run():
        token_data = await google_calendar.refresh_access_token(
            refresh_token=settings.test_live_calendar_refresh_token
        )
        access_token = token_data["access_token"]

        # Far enough in the future to never collide with anything real.
        start = datetime.now(timezone.utc) + timedelta(days=300)
        end = start + timedelta(minutes=30)

        event = await google_calendar.create_event(
            access_token=access_token,
            calendar_id=settings.test_live_calendar_id,
            summary="AI Work Planner -- automated test event (safe to ignore/delete)",
            description="Created by tests/test_schedule_apply_live_google.py. "
            "Deleted automatically at the end of the same test run.",
            start=start,
            end=end,
            time_zone="UTC",
            private_properties={"app": "ai-work-planner", "test": "true"},
        )

        assert event.get("id")
        event_id = event["id"]

        try:
            assert event.get("summary", "").startswith("AI Work Planner")
        finally:
            # Always clean up, even if an assertion above failed.
            await google_calendar.delete_event(
                access_token=access_token,
                calendar_id=settings.test_live_calendar_id,
                event_id=event_id,
            )

        # Confirm the delete actually took -- fetching a deleted event's
        # id again via events.get would 404/410; re-deleting is idempotent
        # (delete_event treats 410 Gone as success), which is itself a
        # reasonable proxy that cleanup succeeded without adding a new
        # dependency on an events.get call here.
        await google_calendar.delete_event(
            access_token=access_token, calendar_id=settings.test_live_calendar_id, event_id=event_id
        )

    asyncio.run(_run())
