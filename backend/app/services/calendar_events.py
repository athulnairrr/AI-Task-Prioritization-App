"""Normalizes raw Google Calendar API responses into the internal models
the rest of the app (and, later, the scheduling engine) actually needs --
see /docs/architecture.md "Calendar data model" for the field mapping and
why the rest of Google's event payload (attendees, conference data,
description, location, ...) is deliberately dropped here rather than
passed through to clients.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.schemas.calendar import BusyIntervalOut, CalendarEventOut


def normalize_event(raw: dict[str, Any]) -> CalendarEventOut:
    start_raw = raw.get("start", {})
    end_raw = raw.get("end", {})
    all_day = "date" in start_raw and "dateTime" not in start_raw

    if all_day:
        start = datetime.fromisoformat(start_raw["date"]).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(end_raw["date"]).replace(tzinfo=timezone.utc)
    else:
        start = datetime.fromisoformat(start_raw["dateTime"])
        end = datetime.fromisoformat(end_raw["dateTime"])

    return CalendarEventOut(
        event_id=raw["id"],
        title=raw.get("summary") or "(No title)",
        start=start,
        end=end,
        all_day=all_day,
        status=raw.get("status", "confirmed"),
        is_recurring=bool(raw.get("recurringEventId") or raw.get("recurrence")),
    )


def normalize_events(raw_events: list[dict[str, Any]]) -> list[CalendarEventOut]:
    # Cancelled instances of recurring events can appear as bare
    # `{"id": ..., "status": "cancelled"}` stubs with no start/end -- skip
    # anything we can't actually place on a timeline.
    normalized = []
    for raw in raw_events:
        if not raw.get("start") or not raw.get("end"):
            continue
        normalized.append(normalize_event(raw))
    return normalized


def normalize_busy_intervals(raw_busy: list[dict[str, Any]]) -> list[BusyIntervalOut]:
    return [
        BusyIntervalOut(start=datetime.fromisoformat(b["start"]), end=datetime.fromisoformat(b["end"]))
        for b in raw_busy
    ]
