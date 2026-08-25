"""The scheduling engine: pure, deterministic, no I/O.

Every function here takes plain Python data (tasks, busy intervals,
constraints) and returns plain Python data -- no database, no Gemini, no
Google API calls happen in this module. That's deliberate: it's what makes
the whole engine hermetically testable (see tests/test_scheduling.py) and
keeps "what decided this timestamp" fully inspectable and reproducible.
Gemini decides task *intelligence* (priority/urgency/duration, Phase 3);
this module decides the *timestamp* -- never the other way around.

Algorithm (deterministic greedy / ranked-slot -- not an optimization
solver, per the MVP brief):

1. Compute free intervals: for each calendar day in [horizon_start,
   horizon_end], intersect the configured working-hours window with that
   day, then subtract every busy interval that overlaps it. Sub-intervals
   shorter than `min_block_minutes` are discarded.
2. Sort tasks by (priority DESC, deadline ASC-with-no-deadline-last,
   task_id) -- higher priority first; among equal priority, the earlier
   deadline goes first; a stable, fully deterministic final tiebreak.
3. Walk the sorted tasks one at a time (greedy: earlier/higher-priority
   tasks get first pick of the best remaining slots). For each task:
   a. Find every remaining free interval that (i) can fit the task's full
      duration and (ii) ends before the task's deadline, if it has one
      (accounting for the deadline possibly falling inside the interval).
   b. Score each candidate (see `_score_candidate`) and pick the
      highest-scoring one; ties break on earliest start.
   c. Place the task at the start of the chosen interval (front-loaded --
      "prefer earlier completion for high-priority/urgent tasks").
   d. Consume that slice of time from the free-interval pool so no later
      task in this same run can double-book it.
   A task with no valid candidate is reported unscheduled with a reason,
   never silently dropped.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Constraints -- configurable, not hardcoded through the algorithm
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchedulingConstraints:
    """MVP defaults. All of these are read from here, not scattered as
    magic numbers through the algorithm, specifically so a future phase
    (per-user working hours, timezones, working days) is a change in one
    place, not a rewrite."""

    working_hours_start_hour: int = 9
    working_hours_end_hour: int = 18
    # IANA timezone name (e.g. "America/New_York") that working hours are
    # interpreted in -- "9am" means 9am *there*, not 9am UTC. Defaults to
    # UTC only as a last resort (see app/api/scheduling.py, which always
    # tries to pass the connected Google Calendar's own timeZone first).
    # See /docs/architecture.md "Timezone strategy" (Phase 6) for why this
    # replaced the earlier UTC-only MVP simplification.
    working_hours_timezone: str = "UTC"
    min_block_minutes: int = 30
    # Priority assumed for a task that was explicitly requested by id but
    # has no Gemini result yet -- never used for the "all unscheduled
    # prioritized tasks" mode, which only considers tasks that already
    # have an AI result.
    default_priority_score: float = 50.0
    # Small, additive scoring bonuses (see _score_candidate) -- kept well
    # below the 0-100 priority range so priority always dominates ordering.
    earliest_slot_bonus: float = 3.0
    snug_fit_bonus: float = 2.0
    # A candidate interval counts as a "snug fit" when the task would use
    # at least this fraction of it, favoring not fragmenting a much larger
    # free block for a short task when a closer-fitting block exists.
    snug_fit_threshold: float = 0.6


DEFAULT_CONSTRAINTS = SchedulingConstraints()


# ---------------------------------------------------------------------------
# Internal data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def overlaps(self, other: "Interval") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class SchedulableTask:
    """What the engine needs to know about a task -- assembled by the API
    layer from `tasks` + the latest `task_ai_results` row, if any."""

    task_id: uuid.UUID
    title: str
    priority_score: float
    duration_minutes: int
    deadline: datetime | None = None
    has_ai_result: bool = False


@dataclass(frozen=True)
class ScheduledItem:
    task_id: uuid.UUID
    start: datetime
    end: datetime
    priority_score: float
    score: float
    reason: str


@dataclass(frozen=True)
class UnscheduledTask:
    task_id: uuid.UUID
    reason: str


@dataclass(frozen=True)
class ScheduleResult:
    scheduled: list[ScheduledItem] = field(default_factory=list)
    unscheduled: list[UnscheduledTask] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Free-interval computation
# ---------------------------------------------------------------------------


def compute_free_intervals(
    horizon_start: datetime,
    horizon_end: datetime,
    busy: list[Interval],
    constraints: SchedulingConstraints = DEFAULT_CONSTRAINTS,
) -> list[Interval]:
    """Working-hours windows for each *local* calendar day in the horizon
    (in `constraints.working_hours_timezone`), minus every busy interval
    that overlaps them. `horizon_start`/`horizon_end` and every `busy`
    interval must be timezone-aware -- they're typically UTC (as Google's
    API returns), but the day/working-hours boundaries are computed in the
    configured timezone, not UTC, so "9am" means 9am there. Internally
    everything is still compared as aware datetimes (Python resolves the
    correct UTC instant either way); only wall-clock 9am/6pm are
    timezone-sensitive.

    Each day's window is built via direct `datetime(..., tzinfo=tz)`
    construction rather than midnight-plus-timedelta arithmetic --
    `zoneinfo` correctly resolves the UTC offset for an explicit wall-clock
    time (including on a DST-transition day), whereas adding a fixed
    `timedelta` to an already-aware datetime does not re-resolve the
    offset and would silently misplace working hours by an hour around a
    DST change. See tests/test_scheduling.py's DST-boundary tests.
    """
    if horizon_end <= horizon_start:
        return []

    tz = ZoneInfo(constraints.working_hours_timezone)
    free: list[Interval] = []
    day = horizon_start.astimezone(tz).date()
    end_date = horizon_end.astimezone(tz).date()

    while day <= end_date:
        window_start = datetime(
            day.year, day.month, day.day, constraints.working_hours_start_hour, tzinfo=tz
        )
        window_end = datetime(
            day.year, day.month, day.day, constraints.working_hours_end_hour, tzinfo=tz
        )

        window_start = max(window_start, horizon_start)
        window_end = min(window_end, horizon_end)

        if window_end > window_start:
            free.extend(_subtract_busy(Interval(window_start, window_end), busy, constraints))

        day += timedelta(days=1)

    return free


def _subtract_busy(
    window: Interval, busy: list[Interval], constraints: SchedulingConstraints
) -> list[Interval]:
    overlapping = sorted(
        (b for b in busy if b.overlaps(window)), key=lambda b: b.start
    )
    pieces: list[Interval] = []
    cursor = window.start
    for b in overlapping:
        gap_end = min(b.start, window.end)
        if gap_end > cursor:
            pieces.append(Interval(cursor, gap_end))
        cursor = max(cursor, min(b.end, window.end))
    if window.end > cursor:
        pieces.append(Interval(cursor, window.end))
    return [p for p in pieces if p.minutes >= constraints.min_block_minutes]


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

_FAR_FUTURE = datetime(9999, 1, 1, tzinfo=timezone.utc)


def _task_sort_key(task: SchedulableTask):
    deadline = task.deadline if task.deadline is not None else _FAR_FUTURE
    return (-task.priority_score, deadline, str(task.task_id))


def build_schedule(
    tasks: list[SchedulableTask],
    busy: list[Interval],
    horizon_start: datetime,
    horizon_end: datetime,
    constraints: SchedulingConstraints = DEFAULT_CONSTRAINTS,
) -> ScheduleResult:
    free = compute_free_intervals(horizon_start, horizon_end, busy, constraints)
    scheduled: list[ScheduledItem] = []
    unscheduled: list[UnscheduledTask] = []

    for task in sorted(tasks, key=_task_sort_key):
        if task.duration_minutes <= 0:
            unscheduled.append(
                UnscheduledTask(task.task_id, "No usable duration estimate for this task.")
            )
            continue

        candidates = _find_candidates(task, free, constraints)
        if not candidates:
            reason = (
                f"No free {task.duration_minutes}-minute interval "
                + (f"before the deadline ({task.deadline.isoformat()})" if task.deadline else "in the requested window")
                + " was found."
            )
            unscheduled.append(UnscheduledTask(task.task_id, reason))
            continue

        best_free_idx, chosen_start, chosen_end, is_earliest, is_snug = _pick_best_candidate(
            task, candidates, constraints
        )
        score, reason = _score_candidate(task, is_earliest, is_snug, constraints)

        scheduled.append(
            ScheduledItem(
                task_id=task.task_id,
                start=chosen_start,
                end=chosen_end,
                priority_score=task.priority_score,
                score=score,
                reason=reason,
            )
        )

        # Consume the used slice from the free-interval pool so no later
        # task in this run can be placed on top of it.
        free_interval = free[best_free_idx]
        leftover_start = chosen_end
        if leftover_start < free_interval.end:
            free[best_free_idx] = Interval(leftover_start, free_interval.end)
        else:
            del free[best_free_idx]

    return ScheduleResult(scheduled=scheduled, unscheduled=unscheduled)


def _find_candidates(
    task: SchedulableTask, free: list[Interval], constraints: SchedulingConstraints
) -> list[tuple[int, Interval, datetime]]:
    """Returns (index into `free`, the free interval, effective usable end
    given the task's deadline) for every interval that can fit this task."""
    duration = timedelta(minutes=task.duration_minutes)
    candidates = []
    for idx, interval in enumerate(free):
        usable_end = interval.end if task.deadline is None else min(interval.end, task.deadline)
        if usable_end <= interval.start:
            continue
        if usable_end - interval.start >= duration:
            candidates.append((idx, interval, usable_end))
    return candidates


def _pick_best_candidate(
    task: SchedulableTask,
    candidates: list[tuple[int, Interval, datetime]],
    constraints: SchedulingConstraints,
) -> tuple[int, datetime, datetime, bool, bool]:
    duration = timedelta(minutes=task.duration_minutes)
    earliest_start = min(interval.start for _, interval, _ in candidates)

    scored = []
    for idx, interval, _usable_end in candidates:
        start = interval.start  # always front-load: earliest point in this interval
        end = start + duration
        is_earliest = start == earliest_start
        is_snug = (duration.total_seconds() / 60) / interval.minutes >= constraints.snug_fit_threshold
        bonus = 0.0
        if is_earliest:
            bonus += constraints.earliest_slot_bonus
        if is_snug:
            bonus += constraints.snug_fit_bonus
        scored.append((bonus, -start.timestamp(), idx, start, end, is_earliest, is_snug))

    # Highest bonus first; tie-break on earliest start (via -start.timestamp()).
    scored.sort(key=lambda row: (-row[0], -row[1]))
    _, _, idx, start, end, is_earliest, is_snug = scored[0]
    return idx, start, end, is_earliest, is_snug


def _score_candidate(
    task: SchedulableTask, is_earliest: bool, is_snug: bool, constraints: SchedulingConstraints
) -> tuple[float, str]:
    bonus = 0.0
    notes = []
    if is_earliest:
        bonus += constraints.earliest_slot_bonus
        notes.append("earliest available slot")
    if is_snug:
        bonus += constraints.snug_fit_bonus
        notes.append("tightly fits the window, preserving other free time")
    score = min(100.0, task.priority_score + bonus)

    tier = "High" if task.priority_score >= 70 else "Medium" if task.priority_score >= 40 else "Low"
    deadline_clause = f" with a deadline of {task.deadline.isoformat()}" if task.deadline else ""
    quality_clause = f" ({'; '.join(notes)})" if notes else ""
    reason = (
        f"{tier}-priority task (score {task.priority_score:.0f}){deadline_clause}; "
        f"scheduled into a {task.duration_minutes}-minute window{quality_clause}."
    )
    return score, reason
