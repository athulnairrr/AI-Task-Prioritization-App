"""Hermetic tests for normalizing raw Google Calendar API payloads into
the internal models. No network -- uses payload shapes captured from
Google's real API during live verification of this phase."""

from __future__ import annotations

from app.services.calendar_events import normalize_busy_intervals, normalize_event, normalize_events


def test_normalize_timed_event():
    raw = {
        "id": "abc123",
        "summary": "Team standup",
        "start": {"dateTime": "2026-08-25T09:00:00-07:00"},
        "end": {"dateTime": "2026-08-25T09:15:00-07:00"},
        "status": "confirmed",
    }
    event = normalize_event(raw)
    assert event.event_id == "abc123"
    assert event.title == "Team standup"
    assert event.all_day is False
    assert event.is_recurring is False
    assert event.status == "confirmed"
    assert event.start.hour == 9


def test_normalize_all_day_event():
    raw = {
        "id": "xyz789",
        "summary": "Company holiday",
        "start": {"date": "2026-12-25"},
        "end": {"date": "2026-12-26"},
        "status": "confirmed",
    }
    event = normalize_event(raw)
    assert event.all_day is True
    assert event.start.year == 2026
    assert event.start.month == 12
    assert event.start.day == 25


def test_normalize_recurring_event_instance():
    raw = {
        "id": "abc123_20260825T090000Z",
        "summary": "Weekly sync",
        "start": {"dateTime": "2026-08-25T09:00:00Z"},
        "end": {"dateTime": "2026-08-25T09:30:00Z"},
        "status": "confirmed",
        "recurringEventId": "abc123",
    }
    event = normalize_event(raw)
    assert event.is_recurring is True


def test_normalize_event_missing_title_gets_placeholder():
    raw = {
        "id": "no-title",
        "start": {"dateTime": "2026-08-25T09:00:00Z"},
        "end": {"dateTime": "2026-08-25T09:30:00Z"},
        "status": "confirmed",
    }
    event = normalize_event(raw)
    assert event.title == "(No title)"


def test_normalize_cancelled_event():
    raw = {
        "id": "cancelled-1",
        "summary": "Cancelled meeting",
        "start": {"dateTime": "2026-08-25T09:00:00Z"},
        "end": {"dateTime": "2026-08-25T09:30:00Z"},
        "status": "cancelled",
    }
    event = normalize_event(raw)
    assert event.status == "cancelled"


def test_normalize_events_skips_bare_cancelled_stubs():
    """A cancelled instance of a recurring event can appear as a bare
    {"id": ..., "status": "cancelled"} stub with no start/end -- can't be
    placed on a timeline, so it's dropped rather than crashing."""
    raw_events = [
        {"id": "stub-1", "status": "cancelled"},
        {
            "id": "real-1",
            "summary": "Real event",
            "start": {"dateTime": "2026-08-25T09:00:00Z"},
            "end": {"dateTime": "2026-08-25T09:30:00Z"},
            "status": "confirmed",
        },
    ]
    normalized = normalize_events(raw_events)
    assert len(normalized) == 1
    assert normalized[0].event_id == "real-1"


def test_normalize_busy_intervals():
    raw_busy = [
        {"start": "2026-08-25T09:00:00Z", "end": "2026-08-25T09:30:00Z"},
        {"start": "2026-08-25T14:00:00Z", "end": "2026-08-25T15:00:00Z"},
    ]
    intervals = normalize_busy_intervals(raw_busy)
    assert len(intervals) == 2
    assert intervals[0].start.hour == 9
    assert intervals[1].end.hour == 15


def test_normalize_empty_busy_intervals():
    assert normalize_busy_intervals([]) == []
