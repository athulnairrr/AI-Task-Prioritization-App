"""Hermetic tests for app/services/priority.py -- pure Python, no network,
no database. See that module's docstring for the reasoning."""

from datetime import datetime, timedelta, timezone

from app.services.priority import category_group, effective_priority_score, proximity_boost

_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def test_proximity_boost_no_due_date():
    assert proximity_boost(None, now=_NOW) == 0.0


def test_proximity_boost_far_out_due_date():
    assert proximity_boost(_NOW + timedelta(days=30), now=_NOW) == 0.0


def test_proximity_boost_tiers():
    assert proximity_boost(_NOW + timedelta(hours=12), now=_NOW) == 30.0  # within 24h
    assert proximity_boost(_NOW + timedelta(days=2), now=_NOW) == 20.0  # within 3 days
    assert proximity_boost(_NOW + timedelta(days=6), now=_NOW) == 10.0  # within a week
    assert proximity_boost(_NOW + timedelta(days=10), now=_NOW) == 3.0  # within two weeks


def test_proximity_boost_overdue_is_max():
    assert proximity_boost(_NOW - timedelta(days=3), now=_NOW) == 30.0


def test_proximity_boost_handles_naive_datetime():
    # A naive datetime (no tzinfo) shouldn't raise -- treated as UTC.
    naive_due = datetime(2026, 1, 16, 0, 0)
    assert proximity_boost(naive_due, now=_NOW) == 30.0


def test_effective_priority_score_none_when_unprioritized():
    assert effective_priority_score(None, _NOW + timedelta(hours=1), now=_NOW) is None


def test_effective_priority_score_adds_boost():
    assert effective_priority_score(50.0, _NOW + timedelta(hours=1), now=_NOW) == 80.0


def test_effective_priority_score_clamps_to_max():
    assert effective_priority_score(95.0, _NOW + timedelta(hours=1), now=_NOW) == 100.0


def test_effective_priority_score_no_due_date_unchanged():
    assert effective_priority_score(42.0, None, now=_NOW) == 42.0


def test_category_group_mapping():
    assert category_group("work") == "professional"
    assert category_group("finance") == "professional"
    assert category_group("learning") == "educational"
    assert category_group("personal") == "personal"
    assert category_group("health") == "personal"
    assert category_group("household") == "personal"
    assert category_group("other") == "other"


def test_category_group_none_when_unprioritized():
    assert category_group(None) is None


def test_category_group_unknown_falls_back_to_other():
    assert category_group("something-new-gemini-invents") == "other"
