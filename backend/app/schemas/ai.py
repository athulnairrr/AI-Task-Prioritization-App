"""AI prioritization schemas.

`GeminiTaskAnalysis` does double duty: it's the exact `response_schema`
handed to Gemini for structured JSON output (see app/services/ai.py), and
the Pydantic model that output is parsed into -- Gemini's free-form text is
never hand-parsed.

Two independent layers of validation, deliberately not merged into one:

1. **Structural** (Pydantic, at parse time): `category`/`urgency`/`importance`
   must be one of the declared enum values, all fields must be present and
   the right type. A violation here means the response is genuinely
   unusable -- Gemini didn't follow the requested shape at all -- and
   raises `AiPrioritizationError`; nothing is stored (see app/services/ai.py).
2. **Numeric bounds** (`clamp()`, deterministic Python, not Pydantic
   constraints): `priority_score`/`confidence_score`/`estimated_minutes`
   have no `ge`/`le` on the field itself, specifically so a Gemini value
   that drifts outside range doesn't blow up parsing -- it gets clamped
   into range instead. This is "use deterministic application logic for
   final validation/bounds," not "reject anything Gemini gets slightly
   wrong." See ADR-011.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TaskCategory = Literal["work", "personal", "health", "finance", "learning", "household", "other"]
UrgencyLevel = Literal["low", "medium", "high"]
ImportanceLevel = Literal["low", "medium", "high"]

# Deterministic bounds enforced regardless of what Gemini returns.
PRIORITY_SCORE_MIN, PRIORITY_SCORE_MAX = 0.0, 100.0
CONFIDENCE_SCORE_MIN, CONFIDENCE_SCORE_MAX = 0.0, 1.0
ESTIMATED_MINUTES_MIN, ESTIMATED_MINUTES_MAX = 5, 8 * 60  # 5 minutes .. 8 hours
REASONING_MAX_LENGTH = 500


class GeminiTaskAnalysis(BaseModel):
    """The structured output requested from Gemini for a single task.

    Numeric fields intentionally have no `ge`/`le` here -- see module
    docstring. Descriptions below are still sent to Gemini as part of the
    JSON schema and steer it toward in-range values; `clamp()` is what
    actually guarantees them.
    """

    category: TaskCategory
    urgency: UrgencyLevel
    importance: ImportanceLevel
    priority_score: float = Field(description="Overall priority, 0 (lowest) to 100 (highest).")
    confidence_score: float = Field(
        description="Your confidence in this assessment, 0.0 (low) to 1.0 (high)."
    )
    estimated_minutes: int = Field(
        description="Estimated minutes to complete the task. Infer from the task details if a duration isn't stated."
    )
    reasoning: str = Field(description="One concise sentence explaining the assessment.")

    def clamp(self) -> "GeminiTaskAnalysis":
        """The deterministic bounds-enforcement layer: always succeeds,
        never raises. Guarantees every numeric field is within its defined
        range and reasoning isn't unreasonably long, regardless of what
        Gemini actually returned."""
        return self.model_copy(
            update={
                "priority_score": min(max(self.priority_score, PRIORITY_SCORE_MIN), PRIORITY_SCORE_MAX),
                "confidence_score": min(
                    max(self.confidence_score, CONFIDENCE_SCORE_MIN), CONFIDENCE_SCORE_MAX
                ),
                "estimated_minutes": min(
                    max(self.estimated_minutes, ESTIMATED_MINUTES_MIN), ESTIMATED_MINUTES_MAX
                ),
                "reasoning": self.reasoning[:REASONING_MAX_LENGTH],
            }
        )


class TaskAiResultOut(BaseModel):
    """Mirrors a row in `public.task_ai_results`, with `importance` and
    `confidence_score` (not dedicated columns -- see ADR-011) surfaced from
    `raw_response` for API convenience."""

    id: uuid.UUID
    task_id: uuid.UUID
    tenant_id: uuid.UUID
    model: str
    category: str | None
    urgency: str | None
    importance: str | None
    priority_score: float | None
    confidence_score: float | None
    effort_estimate_minutes: int | None
    reasoning: str | None
    created_at: datetime
