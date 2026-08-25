"""Hermetic, no-network unit tests for the AI analysis schema: structural
validation (Pydantic) and the deterministic clamp() bounds layer. See
app/schemas/ai.py for why these are two separate mechanisms."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.ai import (
    CONFIDENCE_SCORE_MAX,
    CONFIDENCE_SCORE_MIN,
    ESTIMATED_MINUTES_MAX,
    ESTIMATED_MINUTES_MIN,
    PRIORITY_SCORE_MAX,
    PRIORITY_SCORE_MIN,
    REASONING_MAX_LENGTH,
    GeminiTaskAnalysis,
)


def _valid_kwargs(**overrides) -> dict:
    base = dict(
        category="work",
        urgency="high",
        importance="high",
        priority_score=80.0,
        confidence_score=0.9,
        estimated_minutes=60,
        reasoning="Important deadline-driven task.",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Structural validation -- a genuinely malformed response is rejected
# ---------------------------------------------------------------------------


def test_valid_analysis_parses():
    analysis = GeminiTaskAnalysis(**_valid_kwargs())
    assert analysis.category == "work"
    assert analysis.priority_score == 80.0


def test_rejects_unknown_category():
    with pytest.raises(ValidationError):
        GeminiTaskAnalysis(**_valid_kwargs(category="banana"))


def test_rejects_unknown_urgency():
    with pytest.raises(ValidationError):
        GeminiTaskAnalysis(**_valid_kwargs(urgency="extremely-high"))


def test_rejects_missing_required_field():
    kwargs = _valid_kwargs()
    del kwargs["reasoning"]
    with pytest.raises(ValidationError):
        GeminiTaskAnalysis(**kwargs)


def test_rejects_non_numeric_priority_score():
    with pytest.raises(ValidationError):
        GeminiTaskAnalysis(**_valid_kwargs(priority_score="very high"))


# ---------------------------------------------------------------------------
# clamp() -- deterministic bounds enforcement, independent of Gemini
# ---------------------------------------------------------------------------


def test_clamp_caps_priority_score_above_max():
    analysis = GeminiTaskAnalysis(**_valid_kwargs(priority_score=150.0))
    clamped = analysis.clamp()
    assert clamped.priority_score == PRIORITY_SCORE_MAX


def test_clamp_floors_priority_score_below_min():
    analysis = GeminiTaskAnalysis(**_valid_kwargs(priority_score=-40.0))
    clamped = analysis.clamp()
    assert clamped.priority_score == PRIORITY_SCORE_MIN


def test_clamp_caps_confidence_score_above_max():
    analysis = GeminiTaskAnalysis(**_valid_kwargs(confidence_score=5.0))
    clamped = analysis.clamp()
    assert clamped.confidence_score == CONFIDENCE_SCORE_MAX


def test_clamp_floors_confidence_score_below_min():
    analysis = GeminiTaskAnalysis(**_valid_kwargs(confidence_score=-1.0))
    clamped = analysis.clamp()
    assert clamped.confidence_score == CONFIDENCE_SCORE_MIN


def test_clamp_bounds_estimated_minutes():
    too_short = GeminiTaskAnalysis(**_valid_kwargs(estimated_minutes=0)).clamp()
    assert too_short.estimated_minutes == ESTIMATED_MINUTES_MIN

    too_long = GeminiTaskAnalysis(**_valid_kwargs(estimated_minutes=100_000)).clamp()
    assert too_long.estimated_minutes == ESTIMATED_MINUTES_MAX


def test_clamp_truncates_long_reasoning():
    analysis = GeminiTaskAnalysis(**_valid_kwargs(reasoning="x" * 5000))
    clamped = analysis.clamp()
    assert len(clamped.reasoning) == REASONING_MAX_LENGTH


def test_clamp_leaves_in_range_values_untouched():
    analysis = GeminiTaskAnalysis(**_valid_kwargs())
    clamped = analysis.clamp()
    assert clamped == analysis
