"""Calendar connection / event / availability schemas.

`CalendarConnectionOut` and friends deliberately expose only what a client
needs to render connection status and read-only calendar data -- never a
token, and only the Google-specific fields worth surfacing (see
/docs/architecture.md "Calendar data model" for the full mapping from a raw
Google event to `CalendarEventOut`).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class CalendarConnectionStatus(str, Enum):
    not_connected = "not_connected"
    connected = "connected"
    reauth_required = "reauth_required"
    error = "error"


class CalendarConnectionOut(BaseModel):
    status: CalendarConnectionStatus
    google_account_email: str | None = None
    calendar_id: str | None = None
    connected_at: datetime | None = None
    last_error: str | None = None
    calendar_timezone: str | None = None
    # Whether this connection currently has calendar.events (write) scope,
    # not just the original calendar.readonly -- lets the client decide
    # whether to show "Connect Calendar permissions" before Apply Schedule
    # without parsing raw scope strings itself. See ADR-019.
    has_write_access: bool = False
    # Phase 7: when the last incremental/full sync completed, and whether
    # a Google push-notification (watch) channel is currently registered
    # and unexpired. `watch_active=False` doesn't mean sync is broken --
    # it just means updates rely on the POST /calendar/sync fallback
    # instead of push notifications (e.g. no public webhook URL configured
    # in this environment). See ADR-022.
    last_synced_at: datetime | None = None
    watch_active: bool = False


class ConnectUrlOut(BaseModel):
    authorization_url: str


class GoogleCalendarOut(BaseModel):
    """One entry from the connected account's calendar list."""

    id: str
    summary: str | None = None
    primary: bool = False


class CalendarEventOut(BaseModel):
    """Normalized event -- only what a scheduling engine would need, not
    every field Google's API returns (no attendees, no conference data,
    no raw description/location beyond the title)."""

    event_id: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    status: str  # "confirmed" | "tentative" | "cancelled"
    is_recurring: bool


class BusyIntervalOut(BaseModel):
    start: datetime
    end: datetime


class AvailabilityOut(BaseModel):
    range_start: datetime
    range_end: datetime
    calendar_id: str
    busy: list[BusyIntervalOut]


class ExternalCalendarEventOut(BaseModel):
    """One cached, normalized event this app did NOT create -- from
    `google_calendar_external_events`, kept in sync by
    app/services/calendar_sync.py. Shown to the user as a busy block, never
    converted into a task automatically."""

    google_event_id: str
    title: str | None = None
    start: datetime
    end: datetime
    all_day: bool
    status: str


class CalendarSyncResultOut(BaseModel):
    """Response of POST /calendar/sync -- both the reconciliation
    fallback and the result of an opportunistic watch-channel renewal
    check. `synced=False` with a `reason` means nothing was fetched this
    call (already syncing, not connected, or reauthorization needed) --
    not necessarily an error the client needs to show."""

    synced: bool
    reason: str | None = None
    full_resync: bool = False
    processed: int = 0
    # Per-outcome-kind counts (e.g. "external_upserted", "app_moved",
    # "app_noop", "app_deleted", "external_deleted", "app_adopted") --
    # mainly diagnostic/testable, not something the UI needs to render.
    counts: dict[str, int] = Field(default_factory=dict)
    watch_active: bool = False
    last_synced_at: datetime | None = None
