"""Gemini-backed task analysis.

`AiPrioritizationService` is the only thing that talks to Gemini. It is
wired in as a FastAPI dependency (`get_ai_service`) specifically so tests
can override it with a fake that returns canned results -- see
tests/test_prioritize.py -- without making a real network call, keeping CI
fast, deterministic, and free.

Cost/free-tier discipline (see /docs/decisions.md ADR-011 and
/docs/architecture.md "Gemini integration"): this service is only ever
invoked from the explicit `POST /tasks/{id}/prioritize` route -- never from
task creation, task listing, or any read path -- so a page refresh or a
realtime event can never trigger a Gemini call.
"""

from __future__ import annotations

from functools import lru_cache

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.ai import GeminiTaskAnalysis

# Small, fixed prompt + low max_output_tokens: keeps each call cheap and
# within the Gemini free tier (see ADR-011).
_MAX_OUTPUT_TOKENS = 400
_TEMPERATURE = 0.2

_SYSTEM_INSTRUCTION = (
    "You are a task-prioritization assistant for a personal task manager. "
    "Given a task's title and any extra details, assess it and return "
    "structured JSON only. Be concise and decisive. priority_score must be "
    "0-100, confidence_score must be 0.0-1.0, estimated_minutes should be a "
    "sensible whole number of minutes (infer one from the task details if "
    "no duration is stated)."
)


class AiPrioritizationError(Exception):
    """Raised for any Gemini failure (network, API, invalid/unusable output).
    Callers must not write anything to task_ai_results when this is raised."""


class AiPrioritizationService:
    """Thin wrapper around the Gemini API. Construct with no arguments to
    use configured settings; pass explicit values for testing."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.gemini_api_key
        self._model = model if model is not None else settings.gemini_model
        self._client = genai.Client(api_key=self._api_key) if self._api_key else None

    async def analyze(
        self, *, title: str, description: str | None, raw_input: str | None
    ) -> GeminiTaskAnalysis:
        if self._client is None:
            raise AiPrioritizationError("Gemini API key is not configured.")

        prompt = _build_prompt(title=title, description=description, raw_input=raw_input)

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=GeminiTaskAnalysis,
                    max_output_tokens=_MAX_OUTPUT_TOKENS,
                    temperature=_TEMPERATURE,
                ),
            )
        except Exception as exc:  # Gemini/network/API errors of any kind
            raise AiPrioritizationError(f"Gemini request failed: {exc}") from exc

        return _extract_analysis(response)


def _build_prompt(*, title: str, description: str | None, raw_input: str | None) -> str:
    # Prefer the user's original free-text (raw_input) if present -- it
    # usually carries the most signal (deadlines, effort, importance cues).
    details = raw_input or description
    lines = [f"Title: {title}"]
    if details:
        lines.append(f"Details: {details}")
    return "\n".join(lines)


def _extract_analysis(response: types.GenerateContentResponse) -> GeminiTaskAnalysis:
    """Structured output only -- never hand-parsed free text. `response.parsed`
    is the SDK's own validated instantiation of GeminiTaskAnalysis; as a
    fallback (e.g. an SDK version that didn't auto-parse) we validate
    `response.text` as JSON against the same schema. Either way, a value
    that doesn't fit the schema raises AiPrioritizationError -- it is never
    stored and never corrupts the task."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, GeminiTaskAnalysis):
        return parsed

    text = getattr(response, "text", None)
    if not text:
        raise AiPrioritizationError("Gemini did not return usable structured output.")
    try:
        return GeminiTaskAnalysis.model_validate_json(text)
    except ValidationError as exc:
        raise AiPrioritizationError(f"Gemini returned data that failed validation: {exc}") from exc


@lru_cache
def _default_ai_service() -> AiPrioritizationService:
    return AiPrioritizationService()


async def get_ai_service() -> AiPrioritizationService:
    """FastAPI dependency. Override in tests via app.dependency_overrides."""
    return _default_ai_service()
