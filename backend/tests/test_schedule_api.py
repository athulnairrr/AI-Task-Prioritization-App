"""Integration tests for POST /tasks/schedule -- auth, tenant scoping, and
endpoint wiring around the (separately, hermetically tested in
tests/test_scheduling.py) scheduling engine.

Like tests/test_calendar.py, these run against the real Supabase project
but every Google call is mocked via monkeypatching app.services.google_calendar
functions, and the AI service is mocked via app.dependency_overrides --
no real Gemini or Google traffic. Whole module skipped (not failed) when
live Supabase config isn't present.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.oauth_state import create_state
from app.main import app
from app.schemas.ai import GeminiTaskAnalysis
from app.services import google_calendar
from app.services.ai import get_ai_service

settings = get_settings()

LIVE_CONFIGURED = bool(
    settings.supabase_url
    and settings.database_url
    and settings.supabase_jwks_url
    and settings.oauth_state_secret
    and settings.token_encryption_key
    and settings.test_demo_user_a_email
    and settings.test_demo_user_a_password
    and settings.test_demo_user_b_email
    and settings.test_demo_user_b_password
)

pytestmark = pytest.mark.skipif(
    not LIVE_CONFIGURED,
    reason="Live Supabase project + demo user credentials + OAUTH_STATE_SECRET/TOKEN_ENCRYPTION_KEY not configured",
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


def _decode_jwt_sub(token: str) -> str:
    import jwt as _jwt

    return _jwt.decode(token, options={"verify_signature": False})["sub"]


def _tenant_id_for(client: TestClient, token: str) -> str:
    resp = client.get("/tenants/me", headers=_auth(token))
    assert resp.status_code == 200
    return resp.json()[0]["tenant_id"]


def _connect_calendar(client: TestClient, token: str, monkeypatch) -> None:
    state = create_state(tenant_id=_tenant_id_for(client, token), user_id=_decode_jwt_sub(token))

    async def fake_exchange(*, code: str):
        return {"access_token": "fake-access", "refresh_token": "fake-refresh", "expires_in": 3600}

    async def fake_userinfo(*, access_token: str):
        return {"email": "demo@gmail.com"}

    monkeypatch.setattr(google_calendar, "exchange_code_for_tokens", fake_exchange)
    monkeypatch.setattr(google_calendar, "get_userinfo", fake_userinfo)
    resp = client.get("/calendar/callback", params={"code": "fake-code", "state": state})
    assert resp.status_code == 200
    assert "Calendar connected" in resp.text


class _FakeAiService:
    def __init__(self, result: GeminiTaskAnalysis):
        self._result = result

    async def analyze(self, *, title, description, raw_input):
        return self._result


def _prioritize(client: TestClient, token: str, task_id: str, *, priority=80.0, minutes=60) -> None:
    analysis = GeminiTaskAnalysis(
        category="work",
        urgency="high",
        importance="high",
        priority_score=priority,
        confidence_score=0.9,
        estimated_minutes=minutes,
        reasoning="Test fixture reasoning.",
    )
    app.dependency_overrides[get_ai_service] = lambda: _FakeAiService(analysis)
    try:
        resp = client.post(f"/tasks/{task_id}/prioritize", headers=_auth(token))
        assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.pop(get_ai_service, None)


@pytest.fixture(autouse=True)
def _cleanup(client: TestClient, alice_token: str, bob_token: str):
    yield
    app.dependency_overrides.pop(get_ai_service, None)
    client.delete("/calendar/connection", headers=_auth(alice_token))
    client.delete("/calendar/connection", headers=_auth(bob_token))


@pytest.fixture()
def alice_prioritized_task(client: TestClient, alice_token: str):
    create_resp = client.post(
        "/tasks", headers=_auth(alice_token), json={"title": "pytest: schedule me"}
    )
    assert create_resp.status_code == 201
    task = create_resp.json()
    _prioritize(client, alice_token, task["id"], priority=85.0, minutes=60)
    yield task
    client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


# ---------------------------------------------------------------------------
# Unauthenticated / validation
# ---------------------------------------------------------------------------


def test_schedule_requires_auth(client: TestClient):
    resp = client.post("/tasks/schedule", json={})
    assert resp.status_code == 403


def test_schedule_rejects_end_before_start(client: TestClient, alice_token: str):
    resp = client.post(
        "/tasks/schedule",
        headers=_auth(alice_token),
        json={"horizon_start": "2026-09-02T00:00:00Z", "horizon_end": "2026-09-01T00:00:00Z"},
    )
    assert resp.status_code == 422


def test_schedule_404_when_calendar_not_connected(
    client: TestClient, alice_token: str, alice_prioritized_task: dict
):
    resp = client.post(
        "/tasks/schedule", headers=_auth(alice_token), json={"task_ids": [alice_prioritized_task["id"]]}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# End-to-end proposal (Google mocked)
# ---------------------------------------------------------------------------


def test_schedule_proposes_a_slot_for_a_prioritized_task(
    client: TestClient, alice_token: str, alice_prioritized_task: dict, monkeypatch
):
    _connect_calendar(client, alice_token, monkeypatch)

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []  # fully free

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    resp = client.post(
        "/tasks/schedule",
        headers=_auth(alice_token),
        json={
            "task_ids": [alice_prioritized_task["id"]],
            "horizon_start": "2026-09-01T00:00:00Z",
            "horizon_end": "2026-09-03T00:00:00Z",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["scheduled"]) == 1
    item = body["scheduled"][0]
    assert item["task_id"] == alice_prioritized_task["id"]
    assert item["priority_score"] == 85.0
    assert item["score"] >= 85.0
    assert item["reason"]
    assert body["unscheduled"] == []


def test_schedule_unscheduled_when_no_free_slot(
    client: TestClient, alice_token: str, alice_prioritized_task: dict, monkeypatch
):
    _connect_calendar(client, alice_token, monkeypatch)

    async def fake_freebusy_fully_booked(*, access_token, calendar_id, time_min, time_max):
        return [{"start": "2026-09-01T09:00:00Z", "end": "2026-09-03T00:00:00Z"}]

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy_fully_booked)

    resp = client.post(
        "/tasks/schedule",
        headers=_auth(alice_token),
        json={
            "task_ids": [alice_prioritized_task["id"]],
            "horizon_start": "2026-09-01T00:00:00Z",
            "horizon_end": "2026-09-03T00:00:00Z",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scheduled"] == []
    assert len(body["unscheduled"]) == 1
    assert body["unscheduled"][0]["task_id"] == alice_prioritized_task["id"]


def test_schedule_reports_task_without_duration_as_unscheduled(
    client: TestClient, alice_token: str, monkeypatch
):
    _connect_calendar(client, alice_token, monkeypatch)

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    create_resp = client.post(
        "/tasks", headers=_auth(alice_token), json={"title": "pytest: no estimate, no AI result"}
    )
    task = create_resp.json()
    try:
        resp = client.post(
            "/tasks/schedule",
            headers=_auth(alice_token),
            json={
                "task_ids": [task["id"]],
                "horizon_start": "2026-09-01T00:00:00Z",
                "horizon_end": "2026-09-03T00:00:00Z",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["scheduled"] == []
        assert len(body["unscheduled"]) == 1
        assert "duration" in body["unscheduled"][0]["reason"].lower()
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


def test_schedule_reports_unknown_task_id_as_not_found(
    client: TestClient, alice_token: str, monkeypatch
):
    _connect_calendar(client, alice_token, monkeypatch)

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    resp = client.post(
        "/tasks/schedule",
        headers=_auth(alice_token),
        json={"task_ids": ["00000000-0000-0000-0000-000000000000"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scheduled"] == []
    assert len(body["unscheduled"]) == 1
    assert "not found" in body["unscheduled"][0]["reason"].lower()


# ---------------------------------------------------------------------------
# Cross-tenant
# ---------------------------------------------------------------------------


def test_bob_cannot_schedule_alices_task(
    client: TestClient, bob_token: str, alice_prioritized_task: dict, monkeypatch
):
    _connect_calendar(client, bob_token, monkeypatch)

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    resp = client.post(
        "/tasks/schedule", headers=_auth(bob_token), json={"task_ids": [alice_prioritized_task["id"]]}
    )
    assert resp.status_code == 200
    body = resp.json()
    # Alice's task is invisible to Bob's tenant -- reported as not found,
    # not scheduled, and nothing about it (e.g. its title) is exposed.
    assert body["scheduled"] == []
    assert len(body["unscheduled"]) == 1
    assert body["unscheduled"][0]["task_id"] == alice_prioritized_task["id"]
    assert "not found" in body["unscheduled"][0]["reason"].lower()


def test_auto_select_mode_only_considers_own_tenants_pending_prioritized_tasks(
    client: TestClient, alice_token: str, alice_prioritized_task: dict, monkeypatch
):
    _connect_calendar(client, alice_token, monkeypatch)

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    resp = client.post(
        "/tasks/schedule",
        headers=_auth(alice_token),
        json={"horizon_start": "2026-09-01T00:00:00Z", "horizon_end": "2026-09-03T00:00:00Z"},
    )
    assert resp.status_code == 200
    body = resp.json()
    scheduled_ids = {item["task_id"] for item in body["scheduled"]}
    assert alice_prioritized_task["id"] in scheduled_ids
