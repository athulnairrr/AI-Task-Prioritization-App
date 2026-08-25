"""Integration tests for GET /tasks/schedule/items -- the mobile Today/
Calendar screens' single source of truth for "what's on my plan".

Same pattern as tests/test_schedule_apply.py: real Supabase project,
Google calls mocked.
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


@pytest.fixture(autouse=True)
def _cleanup(client: TestClient, alice_token: str, bob_token: str):
    yield
    app.dependency_overrides.pop(get_ai_service, None)
    client.delete("/calendar/connection", headers=_auth(alice_token))
    client.delete("/calendar/connection", headers=_auth(bob_token))


def _connect_calendar(client: TestClient, token: str, monkeypatch) -> None:
    state = create_state(tenant_id=_tenant_id_for(client, token), user_id=_decode_jwt_sub(token))
    granted = " ".join(google_calendar.WRITE_SCOPES)

    async def fake_exchange(*, code: str):
        return {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_in": 3600,
            "scope": granted,
        }

    async def fake_userinfo(*, access_token: str):
        return {"email": "demo@gmail.com"}

    async def fake_get_timezone(*, access_token: str, calendar_id: str):
        return "America/New_York"

    monkeypatch.setattr(google_calendar, "exchange_code_for_tokens", fake_exchange)
    monkeypatch.setattr(google_calendar, "get_userinfo", fake_userinfo)
    monkeypatch.setattr(google_calendar, "get_calendar_timezone", fake_get_timezone)

    resp = client.get("/calendar/callback", params={"code": "fake-auth-code", "state": state})
    assert resp.status_code == 200


def _prioritized_task(client: TestClient, token: str, *, title="pytest schedule-items task", priority=88.0) -> dict:
    create_resp = client.post("/tasks", headers=_auth(token), json={"title": title})
    assert create_resp.status_code == 201
    task = create_resp.json()

    analysis = GeminiTaskAnalysis(
        category="work",
        urgency="high",
        importance="high",
        priority_score=priority,
        confidence_score=0.9,
        estimated_minutes=60,
        reasoning="Test fixture reasoning.",
    )

    class _Fake:
        async def analyze(self, *, title, description, raw_input):
            return analysis

    app.dependency_overrides[get_ai_service] = lambda: _Fake()
    try:
        resp = client.post(f"/tasks/{task['id']}/prioritize", headers=_auth(token))
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.pop(get_ai_service, None)

    return task


def _apply_one(client: TestClient, token: str, monkeypatch, task: dict) -> None:
    async def fake_create_event(**kwargs):
        return {"id": "evt-schedule-items", "updated": "2026-09-01T00:00:00Z"}

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "create_event", fake_create_event)
    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    resp = client.post(
        "/tasks/schedule/apply",
        headers=_auth(token),
        json={"items": [{"task_id": task["id"], "start": "2026-09-01T13:00:00Z", "end": "2026-09-01T14:00:00Z"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1


def test_schedule_items_requires_auth(client: TestClient):
    resp = client.get(
        "/tasks/schedule/items", params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}
    )
    assert resp.status_code == 403


def test_schedule_items_rejects_end_before_start(client: TestClient, alice_token: str):
    resp = client.get(
        "/tasks/schedule/items",
        headers=_auth(alice_token),
        params={"start": "2026-09-02T00:00:00Z", "end": "2026-09-01T00:00:00Z"},
    )
    assert resp.status_code == 422


def test_schedule_items_empty_when_nothing_applied(client: TestClient, alice_token: str):
    resp = client.get(
        "/tasks/schedule/items",
        headers=_auth(alice_token),
        params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert resp.json() == []


def test_schedule_items_reflects_applied_schedule_with_priority_and_sync_status(
    client: TestClient, alice_token: str, monkeypatch
):
    _connect_calendar(client, alice_token, monkeypatch)
    task = _prioritized_task(client, alice_token, priority=91.0)
    try:
        _apply_one(client, alice_token, monkeypatch, task)

        resp = client.get(
            "/tasks/schedule/items",
            headers=_auth(alice_token),
            params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"},
        )
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        item = items[0]
        assert item["task_id"] == task["id"]
        assert item["title"] == task["title"]
        assert item["priority_score"] == 91.0
        assert item["google_event_id"] == "evt-schedule-items"
        assert item["sync_status"] == "synced"
        assert item["needs_attention"] is False
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


def test_schedule_items_excludes_items_outside_the_range(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch)
    task = _prioritized_task(client, alice_token)
    try:
        _apply_one(client, alice_token, monkeypatch, task)

        resp = client.get(
            "/tasks/schedule/items",
            headers=_auth(alice_token),
            params={"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"},
        )
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


def test_bob_cannot_see_alices_schedule_items(client: TestClient, alice_token: str, bob_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch)
    task = _prioritized_task(client, alice_token)
    try:
        _apply_one(client, alice_token, monkeypatch, task)

        resp = client.get(
            "/tasks/schedule/items",
            headers=_auth(bob_token),
            params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"},
        )
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))
