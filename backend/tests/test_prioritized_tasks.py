"""Integration tests for GET /tasks/prioritized -- the mobile Today/
Prioritized-Tasks screens' single source of truth for tasks + latest AI
result, avoiding an N+1 client-side fetch pattern.

Same pattern as the other Phase-6+ integration test files: real Supabase
project, Gemini mocked via `get_ai_service` dependency override.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.schemas.ai import GeminiTaskAnalysis
from app.services.ai import get_ai_service

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
    reason="Live Supabase project + demo user credentials not configured",
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


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    app.dependency_overrides.pop(get_ai_service, None)


def _prioritize(client: TestClient, token: str, task_id: str, *, priority: float, minutes=60) -> None:
    analysis = GeminiTaskAnalysis(
        category="work",
        urgency="high",
        importance="high",
        priority_score=priority,
        confidence_score=0.85,
        estimated_minutes=minutes,
        reasoning="Test fixture reasoning.",
    )

    class _Fake:
        async def analyze(self, *, title, description, raw_input):
            return analysis

    app.dependency_overrides[get_ai_service] = lambda: _Fake()
    try:
        resp = client.post(f"/tasks/{task_id}/prioritize", headers=_auth(token))
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_ai_service, None)


def test_prioritized_requires_auth(client: TestClient):
    resp = client.get("/tasks/prioritized")
    assert resp.status_code == 403


def test_unprioritized_task_appears_with_null_ai_fields(client: TestClient, alice_token: str):
    create_resp = client.post("/tasks", headers=_auth(alice_token), json={"title": "pytest unprioritized"})
    task = create_resp.json()
    try:
        resp = client.get("/tasks/prioritized", headers=_auth(alice_token))
        assert resp.status_code == 200
        entry = next(e for e in resp.json() if e["id"] == task["id"])
        assert entry["priority_score"] is None
        assert entry["title"] == "pytest unprioritized"
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


def test_prioritized_tasks_sorted_by_priority_descending(client: TestClient, alice_token: str):
    low = client.post("/tasks", headers=_auth(alice_token), json={"title": "pytest low priority"}).json()
    high = client.post("/tasks", headers=_auth(alice_token), json={"title": "pytest high priority"}).json()
    try:
        _prioritize(client, alice_token, low["id"], priority=20.0)
        _prioritize(client, alice_token, high["id"], priority=95.0)

        resp = client.get("/tasks/prioritized", headers=_auth(alice_token))
        assert resp.status_code == 200
        ids_in_order = [e["id"] for e in resp.json()]
        assert ids_in_order.index(high["id"]) < ids_in_order.index(low["id"])

        high_entry = next(e for e in resp.json() if e["id"] == high["id"])
        assert high_entry["priority_score"] == 95.0
        assert high_entry["confidence_score"] == 0.85
        assert high_entry["importance"] == "high"
    finally:
        client.delete(f"/tasks/{low['id']}", headers=_auth(alice_token))
        client.delete(f"/tasks/{high['id']}", headers=_auth(alice_token))


def test_prioritized_tasks_status_filter(client: TestClient, alice_token: str):
    task = client.post("/tasks", headers=_auth(alice_token), json={"title": "pytest done task"}).json()
    try:
        client.post(f"/tasks/{task['id']}/complete", headers=_auth(alice_token))
        resp = client.get("/tasks/prioritized", headers=_auth(alice_token), params={"status": "pending"})
        assert resp.status_code == 200
        assert all(e["id"] != task["id"] for e in resp.json())
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


def test_bob_cannot_see_alices_prioritized_tasks(client: TestClient, alice_token: str, bob_token: str):
    task = client.post("/tasks", headers=_auth(alice_token), json={"title": "pytest cross-tenant"}).json()
    try:
        resp = client.get("/tasks/prioritized", headers=_auth(bob_token))
        assert resp.status_code == 200
        assert all(e["id"] != task["id"] for e in resp.json())
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))
