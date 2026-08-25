"""Hermetic tests for the deterministic scheduling engine
(app/services/scheduling.py). No network, no database, no Gemini, no
Google API -- every input (tasks, busy intervals, horizon) is a fixed
in-memory fixture, per the phase brief."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.services.scheduling import (
    DEFAULT_CONSTRAINTS,
    Interval,
    SchedulableTask,
    SchedulingConstraints,
    build_schedule,
    compute_free_intervals,
)


def dt(day: int, hour: int, minute: int = 0, month: int = 8, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def make_task(
    priority: float = 50.0,
    duration: int = 60,
    deadline: datetime | None = None,
    task_id: uuid.UUID | None = None,
) -> SchedulableTask:
    return SchedulableTask(
        task_id=task_id or uuid.uuid4(),
        title="Test task",
        priority_score=priority,
        duration_minutes=duration,
        deadline=deadline,
        has_ai_result=True,
    )


# A Monday-Tuesday-Wednesday horizon, 09:00-18:00 working hours (defaults).
HORIZON_START = dt(24, 0)  # Monday 2026-08-24, midnight
HORIZON_END = dt(27, 0)  # Thursday 2026-08-27, midnight


# ---------------------------------------------------------------------------
# compute_free_intervals -- working-hour boundaries, busy subtraction
# ---------------------------------------------------------------------------


def test_free_intervals_respect_working_hours():
    free = compute_free_intervals(dt(24, 0), dt(25, 0), busy=[])
    assert len(free) == 1
    assert free[0].start == dt(24, 9)
    assert free[0].end == dt(24, 18)


def test_free_intervals_clip_to_horizon_boundaries():
    """Horizon starting mid-day should not include time before it."""
    free = compute_free_intervals(dt(24, 11), dt(24, 18), busy=[])
    assert len(free) == 1
    assert free[0].start == dt(24, 11)
    assert free[0].end == dt(24, 18)


def test_free_intervals_subtract_busy_interval_in_the_middle():
    busy = [Interval(dt(24, 12), dt(24, 13))]
    free = compute_free_intervals(dt(24, 0), dt(25, 0), busy)
    assert len(free) == 2
    assert (free[0].start, free[0].end) == (dt(24, 9), dt(24, 12))
    assert (free[1].start, free[1].end) == (dt(24, 13), dt(24, 18))


def test_free_intervals_drop_slivers_below_min_block():
    # A 10-minute gap is below the default 30-minute minimum block.
    busy = [Interval(dt(24, 9), dt(24, 12)), Interval(dt(24, 12, 10), dt(24, 18))]
    free = compute_free_intervals(dt(24, 0), dt(25, 0), busy)
    assert free == []


def test_free_intervals_span_multiple_days():
    free = compute_free_intervals(dt(24, 0), dt(26, 0), busy=[])
    assert len(free) == 2
    assert free[0].start.day == 24
    assert free[1].start.day == 25


def test_free_intervals_empty_when_fully_booked():
    busy = [Interval(dt(24, 9), dt(24, 18))]
    free = compute_free_intervals(dt(24, 0), dt(25, 0), busy)
    assert free == []


# ---------------------------------------------------------------------------
# One task / one slot
# ---------------------------------------------------------------------------


def test_single_task_gets_scheduled_into_the_only_free_slot():
    task = make_task(priority=80, duration=60)
    result = build_schedule([task], busy=[], horizon_start=dt(24, 0), horizon_end=dt(24, 23, 59))
    assert len(result.scheduled) == 1
    item = result.scheduled[0]
    assert item.task_id == task.task_id
    assert item.start == dt(24, 9)  # front-loaded to the start of the working day
    assert item.end == dt(24, 10)
    assert item.score >= 80


# ---------------------------------------------------------------------------
# Multiple tasks / priority ordering
# ---------------------------------------------------------------------------


def test_higher_priority_task_gets_the_earlier_slot():
    low = make_task(priority=30, duration=60)
    high = make_task(priority=90, duration=60)
    result = build_schedule(
        [low, high], busy=[], horizon_start=dt(24, 0), horizon_end=dt(24, 23, 59)
    )
    by_id = {item.task_id: item for item in result.scheduled}
    assert by_id[high.task_id].start == dt(24, 9)
    assert by_id[low.task_id].start == dt(24, 10)  # placed after the high-priority task


def test_equal_priority_ties_break_on_earlier_deadline():
    later_deadline = make_task(priority=50, duration=60, deadline=dt(26, 18))
    earlier_deadline = make_task(priority=50, duration=60, deadline=dt(24, 18))
    result = build_schedule(
        [later_deadline, earlier_deadline],
        busy=[],
        horizon_start=dt(24, 0),
        horizon_end=dt(26, 23, 59),
    )
    by_id = {item.task_id: item for item in result.scheduled}
    # The earlier-deadline task should be processed (and thus placed) first.
    assert by_id[earlier_deadline.task_id].start == dt(24, 9)
    assert by_id[later_deadline.task_id].start == dt(24, 10)


def test_task_without_deadline_sorts_after_one_with_a_deadline_at_equal_priority():
    no_deadline = make_task(priority=50, duration=60, deadline=None)
    with_deadline = make_task(priority=50, duration=60, deadline=dt(26, 18))
    result = build_schedule(
        [no_deadline, with_deadline],
        busy=[],
        horizon_start=dt(24, 0),
        horizon_end=dt(26, 23, 59),
    )
    by_id = {item.task_id: item for item in result.scheduled}
    assert by_id[with_deadline.task_id].start == dt(24, 9)
    assert by_id[no_deadline.task_id].start == dt(24, 10)


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


def test_task_not_scheduled_past_its_deadline():
    # Only free time is Tuesday 09-18; deadline is Monday evening -- no
    # candidate should exist before the deadline.
    task = make_task(priority=80, duration=60, deadline=dt(24, 18))
    busy = [Interval(dt(24, 9), dt(24, 18))]  # Monday fully booked
    result = build_schedule([task], busy=busy, horizon_start=dt(24, 0), horizon_end=dt(25, 23, 59))
    assert result.scheduled == []
    assert len(result.unscheduled) == 1
    assert "deadline" in result.unscheduled[0].reason.lower()


def test_task_scheduled_before_deadline_when_a_slot_exists():
    task = make_task(priority=80, duration=60, deadline=dt(25, 12))
    result = build_schedule([task], busy=[], horizon_start=dt(24, 0), horizon_end=dt(26, 0))
    assert len(result.scheduled) == 1
    assert result.scheduled[0].end <= task.deadline


def test_deadline_falling_inside_a_free_interval_truncates_usable_time():
    # Free 09-18, but deadline is 09:30 -- only a 30-minute slot is usable,
    # not enough for a 60-minute task.
    task = make_task(priority=80, duration=60, deadline=dt(24, 9, 30))
    result = build_schedule([task], busy=[], horizon_start=dt(24, 0), horizon_end=dt(24, 23, 59))
    assert result.scheduled == []
    assert len(result.unscheduled) == 1


# ---------------------------------------------------------------------------
# Fit / overlap / insufficient availability
# ---------------------------------------------------------------------------


def test_exact_fit_slot_is_used():
    busy = [Interval(dt(24, 10), dt(24, 18))]  # only 09-10 free
    task = make_task(priority=80, duration=60)
    result = build_schedule([task], busy=busy, horizon_start=dt(24, 0), horizon_end=dt(24, 23, 59))
    assert len(result.scheduled) == 1
    assert (result.scheduled[0].start, result.scheduled[0].end) == (dt(24, 9), dt(24, 10))


def test_duration_longer_than_any_available_slot_is_unscheduled():
    busy = [Interval(dt(24, 9, 30), dt(24, 18))]  # only a 30-minute gap
    task = make_task(priority=80, duration=60)
    result = build_schedule([task], busy=busy, horizon_start=dt(24, 0), horizon_end=dt(24, 23, 59))
    assert result.scheduled == []
    assert len(result.unscheduled) == 1
    assert result.unscheduled[0].task_id == task.task_id


def test_completely_insufficient_availability_across_the_horizon():
    busy = [Interval(dt(24, 9), dt(24, 18)), Interval(dt(25, 9), dt(25, 18))]
    task = make_task(priority=80, duration=60)
    result = build_schedule([task], busy=busy, horizon_start=dt(24, 0), horizon_end=dt(26, 0))
    # Wednesday (day 26) is outside [horizon_start, horizon_end) at midnight boundary
    # so only Monday/Tuesday are considered, both fully booked.
    assert result.scheduled == []


def test_overlapping_busy_intervals_are_handled_correctly():
    # Two overlapping busy blocks should still just remove 09-14 total.
    busy = [Interval(dt(24, 9), dt(24, 12)), Interval(dt(24, 11), dt(24, 14))]
    task = make_task(priority=80, duration=60)
    result = build_schedule([task], busy=busy, horizon_start=dt(24, 0), horizon_end=dt(24, 23, 59))
    assert len(result.scheduled) == 1
    assert result.scheduled[0].start == dt(24, 14)


def test_never_overlaps_an_existing_busy_interval():
    busy = [Interval(dt(24, 10), dt(24, 11))]
    task = make_task(priority=80, duration=120)  # would overlap 09-11 if naively placed at 09:00... but doesn't fit before 10 anyway
    result = build_schedule([task], busy=busy, horizon_start=dt(24, 0), horizon_end=dt(24, 23, 59))
    assert len(result.scheduled) == 1
    item = result.scheduled[0]
    for b in busy:
        assert not (item.start < b.end and b.start < item.end)


def test_two_tasks_never_overlap_each_other():
    a = make_task(priority=90, duration=240)
    b = make_task(priority=80, duration=240)
    result = build_schedule([a, b], busy=[], horizon_start=dt(24, 0), horizon_end=dt(24, 23, 59))
    assert len(result.scheduled) == 2
    items = sorted(result.scheduled, key=lambda i: i.start)
    assert items[0].end <= items[1].start


# ---------------------------------------------------------------------------
# Multiple candidate slots -- picks the best, not just the first
# ---------------------------------------------------------------------------


def test_prefers_snug_fit_over_a_much_larger_earlier_block_when_tied_on_earliest():
    # Monday has a 2-hour block (09-11); Tuesday has a much larger 9-hour
    # block. A 2-hour task should take the snug Monday block since it's
    # both earliest and a tight fit -- not fragment Tuesday's big block.
    busy = [Interval(dt(24, 11), dt(24, 18))]
    task = make_task(priority=70, duration=120)
    result = build_schedule([task], busy=busy, horizon_start=dt(24, 0), horizon_end=dt(25, 23, 59))
    assert len(result.scheduled) == 1
    assert result.scheduled[0].start == dt(24, 9)
    assert result.scheduled[0].end == dt(24, 11)


def test_score_reflects_priority_and_slot_quality_bonuses():
    task = make_task(priority=94, duration=60)
    result = build_schedule([task], busy=[], horizon_start=dt(24, 0), horizon_end=dt(24, 23, 59))
    item = result.scheduled[0]
    # priority 94 + earliest(3) + snug(2, since 60/540 < 0.6 -- not snug here)
    # is capped at 100; assert it's at least the base priority and sensible.
    assert item.priority_score == 94
    assert item.score >= 94
    assert "94" in item.reason


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_schedule_is_deterministic_across_runs():
    tasks = [make_task(priority=p, duration=60) for p in [80, 30, 95, 60]]
    result1 = build_schedule(tasks, busy=[], horizon_start=dt(24, 0), horizon_end=dt(26, 0))
    result2 = build_schedule(tasks, busy=[], horizon_start=dt(24, 0), horizon_end=dt(26, 0))
    assert [(i.task_id, i.start, i.end) for i in result1.scheduled] == [
        (i.task_id, i.start, i.end) for i in result2.scheduled
    ]


def test_empty_task_list_returns_empty_result():
    result = build_schedule([], busy=[], horizon_start=dt(24, 0), horizon_end=dt(25, 0))
    assert result.scheduled == []
    assert result.unscheduled == []


def test_zero_duration_task_is_unscheduled_not_crashed():
    task = make_task(priority=50, duration=0)
    result = build_schedule([task], busy=[], horizon_start=dt(24, 0), horizon_end=dt(25, 0))
    assert result.scheduled == []
    assert len(result.unscheduled) == 1


def test_custom_constraints_change_working_hours():
    constraints = SchedulingConstraints(working_hours_start_hour=7, working_hours_end_hour=9)
    free = compute_free_intervals(dt(24, 0), dt(25, 0), busy=[], constraints=constraints)
    assert free[0].start == dt(24, 7)
    assert free[0].end == dt(24, 9)
    assert constraints != DEFAULT_CONSTRAINTS


# ---------------------------------------------------------------------------
# Timezone-aware working hours (Phase 6 -- replaces the earlier UTC-only
# MVP simplification; see /docs/decisions.md ADR-018)
# ---------------------------------------------------------------------------


def test_working_hours_are_interpreted_in_the_configured_timezone():
    # 09:00 America/New_York in August (EDT, UTC-4) is 13:00 UTC.
    constraints = SchedulingConstraints(working_hours_timezone="America/New_York")
    free = compute_free_intervals(dt(24, 0), dt(25, 0), busy=[], constraints=constraints)
    assert len(free) == 1
    assert free[0].start == dt(24, 13)  # 09:00 EDT == 13:00 UTC
    assert free[0].end == dt(24, 22)  # 18:00 EDT == 22:00 UTC


def test_utc_timezone_default_is_unchanged_from_before():
    free = compute_free_intervals(dt(24, 0), dt(25, 0), busy=[])
    assert free[0].start == dt(24, 9)
    assert free[0].end == dt(24, 18)


def test_working_hours_correct_across_a_dst_spring_forward_transition():
    """US DST started 2026-03-08. 09:00 local on 03-07 (before, EST,
    UTC-5) and 09:00 local on 03-09 (after, EDT, UTC-4) must differ by an
    hour in UTC -- this only holds if working-hour boundaries are resolved
    per-day via direct (year, month, day, hour, tzinfo) construction, not
    midnight + a fixed timedelta (which would silently misplace the
    boundary by an hour on the transition day)."""
    constraints = SchedulingConstraints(working_hours_timezone="America/New_York")

    before = datetime(2026, 3, 7, 0, tzinfo=timezone.utc)
    after_start = datetime(2026, 3, 9, 0, tzinfo=timezone.utc)

    free_before = compute_free_intervals(before, before.replace(hour=23), busy=[], constraints=constraints)
    free_after = compute_free_intervals(after_start, after_start.replace(hour=23), busy=[], constraints=constraints)

    # .hour reads the local wall-clock field (always 9 either side of the
    # transition, correctly) -- what must differ is the UTC offset used to
    # resolve that wall-clock time to an actual instant.
    assert free_before[0].start.hour == 9
    assert free_after[0].start.hour == 9
    assert free_before[0].start.utcoffset() == timedelta(hours=-5)  # EST
    assert free_after[0].start.utcoffset() == timedelta(hours=-4)  # EDT
    # Converted to UTC, the two "9am local" instants are genuinely 1 hour
    # apart in wall-clock-relative-to-UTC terms.
    assert free_before[0].start.astimezone(timezone.utc).hour == 14
    assert free_after[0].start.astimezone(timezone.utc).hour == 13


def test_build_schedule_respects_a_non_utc_timezone():
    constraints = SchedulingConstraints(working_hours_timezone="America/New_York")
    task = make_task(priority=80, duration=60)
    result = build_schedule(
        [task], busy=[], horizon_start=dt(24, 0), horizon_end=dt(24, 23, 59), constraints=constraints
    )
    assert len(result.scheduled) == 1
    assert result.scheduled[0].start == dt(24, 13)  # front-loaded to 09:00 EDT
