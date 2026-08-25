"""Integration tests for POST /tasks/schedule/apply and the incremental
OAuth write-scope flow (GET /calendar/connect?scope=write).

Like the other Phase 4/5 integration test files, these run against the
real Supabase project with every Google call mocked via monkeypatching
app.services.google_calendar -- no real Google traffic, no real Gemini
traffic. A separate, optional live test that creates and deletes one real
Calendar event lives in tests/test_schedule_apply_live_google.py.

Whole module skipped (not failed) when live Supabase config isn't present.
"""

from __future__ import annotations

import uuid

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

FAKE_TIMEZONE = "America/New_York"


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


def _connect_calendar(
    client: TestClient, token: str, monkeypatch, *, write_scope: bool = True, email: str = "demo@gmail.com"
) -> None:
    """Drives the callback with mocked Google calls -- captures a
    write-capable connection with a fixed, non-UTC timezone by default,
    since that's what most of this phase's tests care about exercising."""
    state = create_state(tenant_id=_tenant_id_for(client, token), user_id=_decode_jwt_sub(token))
    granted = " ".join(google_calendar.WRITE_SCOPES if write_scope else google_calendar.READ_ONLY_SCOPES)

    async def fake_exchange(*, code: str):
        return {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_in": 3600,
            "scope": granted,
        }

    async def fake_userinfo(*, access_token: str):
        return {"email": email}

    async def fake_get_timezone(*, access_token: str, calendar_id: str):
        return FAKE_TIMEZONE

    monkeypatch.setattr(google_calendar, "exchange_code_for_tokens", fake_exchange)
    monkeypatch.setattr(google_calendar, "get_userinfo", fake_userinfo)
    monkeypatch.setattr(google_calendar, "get_calendar_timezone", fake_get_timezone)

    resp = client.get("/calendar/callback", params={"code": "fake-auth-code", "state": state})
    assert resp.status_code == 200
    assert "Calendar connected" in resp.text


def _prioritized_task(client: TestClient, token: str, monkeypatch, *, priority=80.0, minutes=60, title="pytest apply task") -> dict:
    create_resp = client.post("/tasks", headers=_auth(token), json={"title": title})
    assert create_resp.status_code == 201
    task = create_resp.json()

    analysis = GeminiTaskAnalysis(
        category="work",
        urgency="high",
        importance="high",
        priority_score=priority,
        confidence_score=0.9,
        estimated_minutes=minutes,
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


@pytest.fixture()
def alice_task(client: TestClient, alice_token: str, monkeypatch) -> dict:
    task = _prioritized_task(client, alice_token, monkeypatch)
    yield task
    client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


def _fake_created_event(event_id: str | None = None):
    async def fake_create_event(**kwargs):
        return {"id": event_id or f"evt-{uuid.uuid4().hex[:8]}"}

    return fake_create_event


# ---------------------------------------------------------------------------
# Unauthenticated / validation
# ---------------------------------------------------------------------------


def test_apply_requires_auth(client: TestClient):
    resp = client.post("/tasks/schedule/apply", json={"items": []})
    assert resp.status_code == 403


def test_apply_empty_items_returns_zero_counts(client: TestClient, alice_token: str):
    resp = client.post("/tasks/schedule/apply", headers=_auth(alice_token), json={"items": []})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"created": 0, "already_applied": 0, "failed": 0, "results": []}


def test_apply_404_when_calendar_not_connected(client: TestClient, alice_token: str, alice_task: dict):
    resp = client.post(
        "/tasks/schedule/apply",
        headers=_auth(alice_token),
        json={"items": [{"task_id": alice_task["id"], "start": "2026-09-01T13:00:00Z", "end": "2026-09-01T14:00:00Z"}]},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Incremental OAuth / write scope
# ---------------------------------------------------------------------------


def test_connect_read_scope_by_default(client: TestClient, alice_token: str):
    resp = client.get("/calendar/connect", headers=_auth(alice_token))
    assert resp.status_code == 200
    url = resp.json()["authorization_url"]
    assert "calendar.readonly" in url
    assert "calendar.events" not in url


def test_connect_write_scope_requests_events_scope_too(client: TestClient, alice_token: str):
    resp = client.get("/calendar/connect", headers=_auth(alice_token), params={"scope": "write"})
    assert resp.status_code == 200
    url = resp.json()["authorization_url"]
    assert "calendar.readonly" in url  # preserved, not replaced
    assert "calendar.events" in url
    assert "include_granted_scopes=true" in url


def test_apply_403_when_connection_lacks_write_scope(client: TestClient, alice_token: str, alice_task: dict, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)

    resp = client.post(
        "/tasks/schedule/apply",
        headers=_auth(alice_token),
        json={"items": [{"task_id": alice_task["id"], "start": "2026-09-01T13:00:00Z", "end": "2026-09-01T14:00:00Z"}]},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "CALENDAR_WRITE_SCOPE_REQUIRED"


def test_connection_status_reports_write_access(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)
    resp = client.get("/calendar/connection", headers=_auth(alice_token))
    body = resp.json()
    assert body["has_write_access"] is True
    assert body["calendar_timezone"] == FAKE_TIMEZONE


def test_connection_status_reports_no_write_access_for_read_only(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    resp = client.get("/calendar/connection", headers=_auth(alice_token))
    assert resp.json()["has_write_access"] is False


def test_incremental_upgrade_preserves_and_adds_scope(client: TestClient, alice_token: str, monkeypatch):
    """Connect read-only first, then upgrade to write -- exactly the
    incremental-auth flow, on the same connection row."""
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    status_resp = client.get("/calendar/connection", headers=_auth(alice_token))
    assert status_resp.json()["has_write_access"] is False

    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)
    status_resp2 = client.get("/calendar/connection", headers=_auth(alice_token))
    assert status_resp2.json()["has_write_access"] is True
    # Same connection (same account), not a duplicate row.
    assert status_resp2.json()["google_account_email"] == "demo@gmail.com"


# ---------------------------------------------------------------------------
# Successful event creation / timezone / metadata
# ---------------------------------------------------------------------------


def test_successful_apply_creates_event_with_correct_metadata_and_timezone(
    client: TestClient, alice_token: str, alice_task: dict, monkeypatch
):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)

    captured = {}

    async def fake_create_event(**kwargs):
        captured.update(kwargs)
        return {"id": "evt-created-1"}

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "create_event", fake_create_event)
    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    resp = client.post(
        "/tasks/schedule/apply",
        headers=_auth(alice_token),
        json={
            "items": [
                {"task_id": alice_task["id"], "start": "2026-09-01T13:00:00Z", "end": "2026-09-01T14:00:00Z"}
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert body["failed"] == 0
    assert body["results"][0]["status"] == "created"
    assert body["results"][0]["google_event_id"] == "evt-created-1"

    # Correct timezone passed through to the Google call.
    assert captured["time_zone"] == FAKE_TIMEZONE
    # Correct metadata: title as summary, task/tenant id in private properties.
    assert captured["summary"] == alice_task["title"]
    assert captured["private_properties"]["task_id"] == alice_task["id"]
    assert captured["private_properties"]["app"] == "ai-work-planner"


# ---------------------------------------------------------------------------
# Idempotency / duplicate apply / reapply
# ---------------------------------------------------------------------------


def test_duplicate_apply_does_not_create_a_second_event(
    client: TestClient, alice_token: str, alice_task: dict, monkeypatch
):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)
    call_count = {"n": 0}

    async def counting_create_event(**kwargs):
        call_count["n"] += 1
        return {"id": "evt-dup-1"}

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "create_event", counting_create_event)
    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    item = {"task_id": alice_task["id"], "start": "2026-09-01T13:00:00Z", "end": "2026-09-01T14:00:00Z"}

    first = client.post("/tasks/schedule/apply", headers=_auth(alice_token), json={"items": [item]})
    assert first.json()["created"] == 1
    assert call_count["n"] == 1

    second = client.post("/tasks/schedule/apply", headers=_auth(alice_token), json={"items": [item]})
    assert second.status_code == 200
    assert second.json()["created"] == 0
    assert second.json()["already_applied"] == 1
    assert second.json()["results"][0]["status"] == "already_applied"
    assert second.json()["results"][0]["google_event_id"] == "evt-dup-1"
    assert call_count["n"] == 1  # Google was NOT called again


def test_retrying_a_previously_failed_item_does_not_duplicate(
    client: TestClient, alice_token: str, alice_task: dict, monkeypatch
):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    async def failing_create_event(**kwargs):
        raise google_calendar.GoogleApiError("boom", status_code=500)

    monkeypatch.setattr(google_calendar, "create_event", failing_create_event)
    item = {"task_id": alice_task["id"], "start": "2026-09-01T13:00:00Z", "end": "2026-09-01T14:00:00Z"}
    first = client.post("/tasks/schedule/apply", headers=_auth(alice_token), json={"items": [item]})
    assert first.json()["failed"] == 1
    assert first.json()["created"] == 0

    # Now Google succeeds -- retry should create exactly one event, not
    # treat the earlier failure as "already applied".
    monkeypatch.setattr(google_calendar, "create_event", _fake_created_event("evt-retry-1"))
    second = client.post("/tasks/schedule/apply", headers=_auth(alice_token), json={"items": [item]})
    assert second.json()["created"] == 1
    assert second.json()["results"][0]["google_event_id"] == "evt-retry-1"


# ---------------------------------------------------------------------------
# Partial failure
# ---------------------------------------------------------------------------


def test_partial_failure_one_of_three(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    tasks = [_prioritized_task(client, alice_token, monkeypatch, title=f"pytest partial {i}") for i in range(3)]
    try:
        call_n = {"n": 0}

        async def flaky_create_event(**kwargs):
            call_n["n"] += 1
            if call_n["n"] == 2:
                raise google_calendar.GoogleApiError("mid-batch failure", status_code=500)
            return {"id": f"evt-partial-{call_n['n']}"}

        monkeypatch.setattr(google_calendar, "create_event", flaky_create_event)

        items = [
            {
                "task_id": t["id"],
                "start": f"2026-09-0{i + 1}T13:00:00Z",
                "end": f"2026-09-0{i + 1}T14:00:00Z",
            }
            for i, t in enumerate(tasks)
        ]
        resp = client.post("/tasks/schedule/apply", headers=_auth(alice_token), json={"items": items})
        assert resp.status_code == 200
        body = resp.json()
        assert body["created"] == 2
        assert body["failed"] == 1
        statuses = [r["status"] for r in body["results"]]
        assert statuses == ["created", "failed", "created"]
    finally:
        for t in tasks:
            client.delete(f"/tasks/{t['id']}", headers=_auth(alice_token))


# ---------------------------------------------------------------------------
# Revalidation -- never trust client-supplied timestamps
# ---------------------------------------------------------------------------


def test_apply_rejects_slot_that_is_no_longer_free(client: TestClient, alice_token: str, alice_task: dict, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)

    async def fake_freebusy_now_booked(*, access_token, calendar_id, time_min, time_max):
        return [{"start": "2026-09-01T12:30:00Z", "end": "2026-09-01T14:30:00Z"}]

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy_now_booked)
    monkeypatch.setattr(google_calendar, "create_event", _fake_created_event())

    resp = client.post(
        "/tasks/schedule/apply",
        headers=_auth(alice_token),
        json={"items": [{"task_id": alice_task["id"], "start": "2026-09-01T13:00:00Z", "end": "2026-09-01T14:00:00Z"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["failed"] == 1
    assert "no longer available" in body["results"][0]["reason"].lower()


def test_apply_rejects_slot_after_deadline(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)
    monkeypatch.setattr(google_calendar, "create_event", _fake_created_event())

    create_resp = client.post(
        "/tasks",
        headers=_auth(alice_token),
        json={"title": "pytest deadline task", "due_at": "2026-09-01T00:00:00Z"},
    )
    task = create_resp.json()
    try:
        resp = client.post(
            "/tasks/schedule/apply",
            headers=_auth(alice_token),
            json={"items": [{"task_id": task["id"], "start": "2026-09-02T13:00:00Z", "end": "2026-09-02T14:00:00Z"}]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["failed"] == 1
        assert "deadline" in body["results"][0]["reason"].lower()
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


def test_apply_reports_unknown_task_as_failed(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    resp = client.post(
        "/tasks/schedule/apply",
        headers=_auth(alice_token),
        json={
            "items": [
                {
                    "task_id": "00000000-0000-0000-0000-000000000000",
                    "start": "2026-09-01T13:00:00Z",
                    "end": "2026-09-01T14:00:00Z",
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["failed"] == 1
    assert "not found" in body["results"][0]["reason"].lower()


# ---------------------------------------------------------------------------
# Revoked token
# ---------------------------------------------------------------------------


def test_apply_409_when_refresh_token_revoked(client: TestClient, alice_token: str, alice_task: dict, monkeypatch):
    state = create_state(tenant_id=_tenant_id_for(client, alice_token), user_id=_decode_jwt_sub(alice_token))

    async def fake_exchange_expired(*, code: str):
        return {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_in": -10,  # already expired -- forces a refresh attempt on next use
            "scope": " ".join(google_calendar.WRITE_SCOPES),
        }

    async def fake_userinfo(*, access_token: str):
        return {"email": "demo@gmail.com"}

    async def fake_get_timezone(*, access_token: str, calendar_id: str):
        return FAKE_TIMEZONE

    monkeypatch.setattr(google_calendar, "exchange_code_for_tokens", fake_exchange_expired)
    monkeypatch.setattr(google_calendar, "get_userinfo", fake_userinfo)
    monkeypatch.setattr(google_calendar, "get_calendar_timezone", fake_get_timezone)
    connect_resp = client.get("/calendar/callback", params={"code": "fake-auth-code", "state": state})
    assert connect_resp.status_code == 200

    async def fake_refresh_rejected(*, refresh_token: str):
        raise google_calendar.GoogleApiError(
            "invalid_grant: Token has been revoked", status_code=400, error_code="invalid_grant"
        )

    monkeypatch.setattr(google_calendar, "refresh_access_token", fake_refresh_rejected)

    resp = client.post(
        "/tasks/schedule/apply",
        headers=_auth(alice_token),
        json={"items": [{"task_id": alice_task["id"], "start": "2026-09-01T13:00:00Z", "end": "2026-09-01T14:00:00Z"}]},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "REAUTH_REQUIRED"


# ---------------------------------------------------------------------------
# Cross-tenant protection
# ---------------------------------------------------------------------------


def test_bob_cannot_apply_schedule_for_alices_task(
    client: TestClient, alice_token: str, bob_token: str, alice_task: dict, monkeypatch
):
    _connect_calendar(client, bob_token, monkeypatch, write_scope=True)
    monkeypatch.setattr(google_calendar, "create_event", _fake_created_event())

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return []

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    resp = client.post(
        "/tasks/schedule/apply",
        headers=_auth(bob_token),
        json={"items": [{"task_id": alice_task["id"], "start": "2026-09-01T13:00:00Z", "end": "2026-09-01T14:00:00Z"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["failed"] == 1
    assert "not found" in body["results"][0]["reason"].lower()
