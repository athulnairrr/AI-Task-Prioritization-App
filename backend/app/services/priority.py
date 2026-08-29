"""Deterministic, calendar/deadline-aware priority presentation.

Gemini's `priority_score` (see app/services/ai.py) is a one-shot snapshot --
set only when the user taps "Prioritize with AI", never recalculated on its
own. Two problems that leaves for a task with a real deadline: a study task
prioritized two weeks before an exam looks exactly as urgent the day before
the exam as it did on day one, and there's no cheap way to re-run Gemini
constantly just to keep that current (free-tier cost discipline, see
ADR-011).

This module is the deterministic layer that fixes that without any extra
Gemini calls: `effective_priority_score()` adds a plain-Python "proximity
boost" on top of the stored `priority_score`, based only on how close
`due_at` is to now. Same pattern this codebase already uses elsewhere (e.g.
`GeminiTaskAnalysis.clamp()`, the scheduling engine's slot-quality bonuses in
app/services/scheduling.py) -- AI judgment for the subjective assessment,
deterministic Python for anything that must be predictable and free.

Also home to `category_group()`, a display-only mapping of Gemini's
finer-grained `category` values onto the product brief's three buckets
(professional / personal / educational) -- no schema change, no migration,
just how the Tasks tab groups/filters what's already stored.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.ai import PRIORITY_SCORE_MAX, PRIORITY_SCORE_MIN

# Tiered rather than a smooth curve -- easier to reason about and to explain
# to a user ("inside 24h" is a clearer promise than a decay constant). Each
# tier is a (max_days_until_due, boost) pair, checked in order; the first
# tier whose threshold isn't exceeded wins. A task already overdue falls
# into the first tier same as one due in the next few hours -- both are
# "as urgent as this gets".
_PROXIMITY_TIERS: list[tuple[float, float]] = [
    (1, 30.0),   # due within 24h (or overdue)
    (3, 20.0),   # due within 3 days
    (7, 10.0),   # due within a week
    (14, 3.0),   # due within two weeks
]


def proximity_boost(due_at: datetime | None, now: datetime | None = None) -> float:
    """How much to add to a task's priority purely because its deadline is
    close. 0 for no deadline or a deadline more than two weeks out."""
    if due_at is None:
        return 0.0
    now = now or datetime.now(timezone.utc)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    days_until_due = (due_at - now).total_seconds() / 86400
    for max_days, boost in _PROXIMITY_TIERS:
        if days_until_due <= max_days:
            return boost
    return 0.0


def effective_priority_score(
    priority_score: float | None, due_at: datetime | None, now: datetime | None = None
) -> float | None:
    """`priority_score` (Gemini's snapshot) plus a deadline-proximity boost,
    clamped back into the same [0, 100] range Gemini's own score lives in.
    None if the task has never been prioritized -- there's nothing to boost."""
    if priority_score is None:
        return None
    boosted = priority_score + proximity_boost(due_at, now)
    return min(max(boosted, PRIORITY_SCORE_MIN), PRIORITY_SCORE_MAX)


# Gemini's actual category enum (app/schemas/ai.py TaskCategory) is finer
# grained than the product brief's three buckets -- this is purely a
# display/filter grouping, not a schema change.
_CATEGORY_GROUPS: dict[str, str] = {
    "work": "professional",
    "finance": "professional",
    "learning": "educational",
    "personal": "personal",
    "health": "personal",
    "household": "personal",
    "other": "other",
}


def category_group(category: str | None) -> str | None:
    """Maps a stored Gemini `category` onto professional/personal/
    educational/other. None if the task has never been prioritized."""
    if category is None:
        return None
    return _CATEGORY_GROUPS.get(category, "other")
