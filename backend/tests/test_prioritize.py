"""Integration tests for POST /tasks/{id}/prioritize and GET
/tasks/{id}/ai-result.

Like tests/test_tasks.py, these run against the real Supabase project (real
auth, real tenant/task rows, real RLS-adjacent tenant checks) but the AI
service itself is replaced with a fake via FastAPI's dependency_overrides --
no real Gemini call happens here, so this file is deterministic, fast, and
free to run as often as you like. A separate, optional test that calls the
real Gemini API lives in tests/test_prioritize_live_gemini.py.

Whole module skipped (not failed) when live Supabase config isn't present,
same as test_tasks.py.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.schemas.ai import GeminiTaskAnalysis
from app.services.ai import AiPrioritizationError, get_ai_service

settings = get_settings()

LIVE_CONFIGURED = bool(
    settings.supabase_url
    and settings.database_url
    and settings.supabase_jwks_url
    and settings.test_demo_user_a_email
    and settings.test_demo_user_a_password
    and settings.test_demo_user_b_email
    and settings.test_demo_user_b_password
)

pytestmark = pytest.mark.skipif(
    not LIVE_CONFIGURED,
    reason="Live Supabase project + demo user credentials not configured (see backend/.env.example)",
)


def _sign_in(email: str, password: str) -> str:
    resp = httpx.post(
        f"{settings.supabase_url}/auth/v1/token?grant_type=password",
        headers={"apikey": settings.supabase_service_role_key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def alice_token() -> str:
    return _sign_in(settings.test_demo_user_a_email, settings.test_demo_user_a_password)


@pytest.fixture(scope="module")
def bob_token() -> str:
    return _sign_in(settings.test_demo_user_b_email, settings.test_demo_user_b_password)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def alice_task(client: TestClient, alice_token: str):
    resp = client.post(
        "/tasks",
        headers=_auth(alice_token),
        json={
            "title": "pytest: finish the client proposal",
            "raw_input": "I need to finish the client proposal by Friday. "
            "It will take about 2 hours and is very important.",
        },
    )
    assert resp.status_code == 201, resp.text
    task = resp.json()
    yield task
    client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


class _FakeAiService:
    """Stands in for AiPrioritizationService in tests. Configure with
    either a canned `result` (success) or an `error` to raise."""

    def __init__(self, *, result: GeminiTaskAnalysis | None = None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.calls = 0

    async def analyze(self, *, title: str, description: str | None, raw_input: str | None):
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _override_ai_service(fake: _FakeAiService) -> None:
    app.dependency_overrides[get_ai_service] = lambda: fake


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_ai_service, None)


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    _clear_overrides()


def _valid_analysis(**overrides) -> GeminiTaskAnalysis:
    base = dict(
        category="work",
        urgency="high",
        importance="high",
        priority_score=92.0,
        confidence_score=0.88,
        estimated_minutes=120,
        reasoning="Client deliverable with a near-term deadline.",
    )
    base.update(overrides)
    return GeminiTaskAnalysis(**base)


# ---------------------------------------------------------------------------
# Successful AI response
# ---------------------------------------------------------------------------


def test_prioritize_success_stores_and_returns_result(
    client: TestClient, alice_token: str, alice_task: dict
):
    fake = _FakeAiService(result=_valid_analysis())
    _override_ai_service(fake)

    resp = client.post(f"/tasks/{alice_task['id']}/prioritize", headers=_auth(alice_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["task_id"] == alice_task["id"]
    assert body["category"] == "work"
    assert body["urgency"] == "high"
    assert body["importance"] == "high"
    assert body["priority_score"] == 92.0
    assert body["confidence_score"] == 0.88
    assert body["effort_estimate_minutes"] == 120
    assert "deadline" in body["reasoning"]
    assert body["model"] == settings.gemini_model
    assert fake.calls == 1


def test_prioritize_result_is_fetchable_without_recalling_ai(
    client: TestClient, alice_token: str, alice_task: dict
):
    fake = _FakeAiService(result=_valid_analysis())
    _override_ai_service(fake)
    create_resp = client.post(f"/tasks/{alice_task['id']}/prioritize", headers=_auth(alice_token))
    assert create_resp.status_code == 200

    # Swap in a service that would fail loudly if called -- GET must not call it.
    _override_ai_service(_FakeAiService(error=AssertionError("ai-result must not call Gemini")))
    get_resp = client.get(f"/tasks/{alice_task['id']}/ai-result", headers=_auth(alice_token))
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == create_resp.json()["id"]


def test_ai_result_404_before_any_prioritization(
    client: TestClient, alice_token: str, alice_task: dict
):
    resp = client.get(f"/tasks/{alice_task['id']}/ai-result", headers=_auth(alice_token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Structured-output validation (numeric bounds get clamped, not rejected)
# ---------------------------------------------------------------------------


def test_prioritize_clamps_out_of_range_values(
    client: TestClient, alice_token: str, alice_task: dict
):
    fake = _FakeAiService(result=_valid_analysis(priority_score=999.0, confidence_score=4.0))
    _override_ai_service(fake)

    resp = client.post(f"/tasks/{alice_task['id']}/prioritize", headers=_auth(alice_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["priority_score"] == 100.0
    assert body["confidence_score"] == 1.0


# ---------------------------------------------------------------------------
# Malformed AI response / Gemini failure -- must not corrupt the task
# ---------------------------------------------------------------------------


def test_prioritize_handles_malformed_ai_response(
    client: TestClient, alice_token: str, alice_task: dict
):
    fake = _FakeAiService(error=AiPrioritizationError("Gemini returned data that failed validation"))
    _override_ai_service(fake)

    resp = client.post(f"/tasks/{alice_task['id']}/prioritize", headers=_auth(alice_token))
    assert resp.status_code == 502
    assert "AI prioritization" in resp.json()["detail"]

    # No result was stored, and the task itself is untouched.
    ai_result_resp = client.get(f"/tasks/{alice_task['id']}/ai-result", headers=_auth(alice_token))
    assert ai_result_resp.status_code == 404
    task_resp = client.get(f"/tasks/{alice_task['id']}", headers=_auth(alice_token))
    assert task_resp.status_code == 200
    assert task_resp.json()["title"] == alice_task["title"]


def test_prioritize_handles_gemini_api_failure(
    client: TestClient, alice_token: str, alice_task: dict
):
    fake = _FakeAiService(error=AiPrioritizationError("Gemini request failed: connection reset"))
    _override_ai_service(fake)

    resp = client.post(f"/tasks/{alice_task['id']}/prioritize", headers=_auth(alice_token))
    assert resp.status_code == 502

    ai_result_resp = client.get(f"/tasks/{alice_task['id']}/ai-result", headers=_auth(alice_token))
    assert ai_result_resp.status_code == 404


# ---------------------------------------------------------------------------
# Unauthenticated / not found / cross-tenant
# ---------------------------------------------------------------------------


def test_prioritize_requires_auth(client: TestClient, alice_task: dict):
    resp = client.post(f"/tasks/{alice_task['id']}/prioritize")
    assert resp.status_code == 403


def test_prioritize_nonexistent_task_is_404(client: TestClient, alice_token: str):
    _override_ai_service(_FakeAiService(result=_valid_analysis()))
    resp = client.post(
        "/tasks/00000000-0000-0000-0000-000000000000/prioritize", headers=_auth(alice_token)
    )
    assert resp.status_code == 404


def test_bob_cannot_prioritize_alices_task(client: TestClient, bob_token: str, alice_task: dict):
    fake = _FakeAiService(result=_valid_analysis())
    _override_ai_service(fake)
    resp = client.post(f"/tasks/{alice_task['id']}/prioritize", headers=_auth(bob_token))
    assert resp.status_code == 404
    assert fake.calls == 0  # tenant check fails before Gemini would ever be called


def test_bob_cannot_read_alices_ai_result(client: TestClient, alice_token: str, bob_token: str, alice_task: dict):
    _override_ai_service(_FakeAiService(result=_valid_analysis()))
    client.post(f"/tasks/{alice_task['id']}/prioritize", headers=_auth(alice_token))

    resp = client.get(f"/tasks/{alice_task['id']}/ai-result", headers=_auth(bob_token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Re-prioritization
# ---------------------------------------------------------------------------


def test_reprioritizing_creates_a_new_result_and_updates_latest(
    client: TestClient, alice_token: str, alice_task: dict
):
    _override_ai_service(_FakeAiService(result=_valid_analysis(priority_score=50.0)))
    first = client.post(f"/tasks/{alice_task['id']}/prioritize", headers=_auth(alice_token))
    assert first.status_code == 200
    assert first.json()["priority_score"] == 50.0

    _override_ai_service(_FakeAiService(result=_valid_analysis(priority_score=77.0)))
    second = client.post(f"/tasks/{alice_task['id']}/prioritize", headers=_auth(alice_token))
    assert second.status_code == 200
    assert second.json()["priority_score"] == 77.0
    assert second.json()["id"] != first.json()["id"]  # a new row, not an overwrite

    latest = client.get(f"/tasks/{alice_task['id']}/ai-result", headers=_auth(alice_token))
    assert latest.json()["priority_score"] == 77.0
