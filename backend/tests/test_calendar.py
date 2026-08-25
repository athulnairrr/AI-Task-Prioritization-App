"""Integration tests for the Google Calendar routes.

Like tests/test_tasks.py and tests/test_prioritize.py, these run against
the real Supabase project (real auth, real tenant rows, real
google_calendar_connections writes/reads/RLS-adjacent tenant checks) but
every actual call to Google is replaced via monkeypatching functions on
app.services.google_calendar -- no real Google API traffic here, so this
file is deterministic and doesn't require a completed (human-interactive)
OAuth consent. A separate, more limited live check against the real Google
API lives in tests/test_calendar_live_google.py.

Whole module skipped (not failed) when live Supabase config isn't present,
same as the other integration test files.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.oauth_state import create_state
from app.main import app
from app.services import google_calendar

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


@pytest.fixture(autouse=True)
def _cleanup_connections(client: TestClient, alice_token: str, bob_token: str):
    """Ensures no test leaks a connection into another test / a real run."""
    yield
    client.delete("/calendar/connection", headers=_auth(alice_token))
    client.delete("/calendar/connection", headers=_auth(bob_token))


def _fake_token_response(**overrides) -> dict:
    base = {
        "access_token": "fake-access-token",
        "refresh_token": "fake-refresh-token",
        "expires_in": 3600,
        "token_type": "Bearer",
    }
    base.update(overrides)
    return base


def _connect_alice(
    client: TestClient,
    alice_token: str,
    monkeypatch,
    email: str = "alice.demo@gmail.com",
    expires_in: int = 3600,
) -> None:
    """Drives the callback flow with mocked Google calls to get Alice into
    a 'connected' state, the same way every scenario that needs an
    existing connection sets one up. `expires_in` defaults to a fresh hour;
    pass a negative value to simulate an already-expired cached access
    token, forcing the next call to actually exercise the refresh path."""
    state = create_state(tenant_id=_tenant_id_for(client, alice_token), user_id=_decode_jwt_sub(alice_token))

    async def fake_exchange(*, code: str):
        assert code == "fake-auth-code"
        return _fake_token_response(expires_in=expires_in)

    async def fake_userinfo(*, access_token: str):
        return {"email": email}

    monkeypatch.setattr(google_calendar, "exchange_code_for_tokens", fake_exchange)
    monkeypatch.setattr(google_calendar, "get_userinfo", fake_userinfo)

    resp = client.get("/calendar/callback", params={"code": "fake-auth-code", "state": state})
    assert resp.status_code == 200
    assert "Calendar connected" in resp.text


def _tenant_id_for(client: TestClient, token: str) -> str:
    resp = client.get("/tenants/me", headers=_auth(token))
    assert resp.status_code == 200
    return resp.json()[0]["tenant_id"]


# ---------------------------------------------------------------------------
# Unauthenticated access
# ---------------------------------------------------------------------------


def test_connection_status_requires_auth(client: TestClient):
    resp = client.get("/calendar/connection")
    assert resp.status_code == 403


def test_connect_requires_auth(client: TestClient):
    resp = client.get("/calendar/connect")
    assert resp.status_code == 403


def test_events_requires_auth(client: TestClient):
    resp = client.get("/calendar/events", params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"})
    assert resp.status_code == 403


def test_disconnect_requires_auth(client: TestClient):
    resp = client.delete("/calendar/connection")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Connection status / not connected
# ---------------------------------------------------------------------------


def test_connection_status_when_not_connected(client: TestClient, alice_token: str):
    resp = client.get("/calendar/connection", headers=_auth(alice_token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_connected"


def test_events_404_when_not_connected(client: TestClient, alice_token: str):
    resp = client.get(
        "/calendar/events",
        headers=_auth(alice_token),
        params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
    )
    assert resp.status_code == 404


def test_availability_404_when_not_connected(client: TestClient, alice_token: str):
    resp = client.get(
        "/calendar/availability",
        headers=_auth(alice_token),
        params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
    )
    assert resp.status_code == 404


def test_disconnect_404_when_not_connected(client: TestClient, alice_token: str):
    resp = client.delete("/calendar/connection", headers=_auth(alice_token))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# OAuth connect / callback
# ---------------------------------------------------------------------------


def test_connect_returns_a_real_google_authorization_url(client: TestClient, alice_token: str):
    resp = client.get("/calendar/connect", headers=_auth(alice_token))
    assert resp.status_code == 200
    url = resp.json()["authorization_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "calendar.readonly" in url
    assert "state=" in url


def test_callback_denied_by_user_shows_friendly_page(client: TestClient):
    resp = client.get("/calendar/callback", params={"error": "access_denied"})
    assert resp.status_code == 200
    assert "cancelled" in resp.text.lower()


def test_callback_missing_code_shows_friendly_error(client: TestClient):
    resp = client.get("/calendar/callback", params={"state": "whatever"})
    assert resp.status_code == 200
    assert "failed" in resp.text.lower()


def test_callback_invalid_state_shows_friendly_error(client: TestClient):
    resp = client.get("/calendar/callback", params={"code": "abc", "state": "not-a-real-state"})
    assert resp.status_code == 200
    assert "invalid or has expired" in resp.text.lower()


def test_callback_missing_refresh_token_shows_friendly_error(client: TestClient, alice_token: str, monkeypatch):
    state = create_state(tenant_id=_tenant_id_for(client, alice_token), user_id=_decode_jwt_sub(alice_token))

    async def fake_exchange_no_refresh(*, code: str):
        return _fake_token_response(refresh_token=None)

    monkeypatch.setattr(google_calendar, "exchange_code_for_tokens", fake_exchange_no_refresh)

    resp = client.get("/calendar/callback", params={"code": "abc", "state": state})
    assert resp.status_code == 200
    assert "didn't grant a long-lived authorization" in resp.text


def test_callback_google_error_shows_friendly_error(client: TestClient, alice_token: str, monkeypatch):
    state = create_state(tenant_id=_tenant_id_for(client, alice_token), user_id=_decode_jwt_sub(alice_token))

    async def fake_exchange_fails(*, code: str):
        raise google_calendar.GoogleApiError("invalid_grant: bad code", status_code=400, error_code="invalid_grant")

    monkeypatch.setattr(google_calendar, "exchange_code_for_tokens", fake_exchange_fails)

    resp = client.get("/calendar/callback", params={"code": "abc", "state": state})
    assert resp.status_code == 200
    assert "rejected the authorization" in resp.text


# ---------------------------------------------------------------------------
# Successful connection
# ---------------------------------------------------------------------------


def test_successful_connection_end_to_end(client: TestClient, alice_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch, email="alice.demo@gmail.com")

    status_resp = client.get("/calendar/connection", headers=_auth(alice_token))
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == "connected"
    assert body["google_account_email"] == "alice.demo@gmail.com"
    assert body["calendar_id"] == "primary"
    assert body["connected_at"] is not None
    # Never returned, under any field name.
    assert "token" not in str(body).lower().replace("connected_at", "")


def test_reconnecting_updates_existing_row_not_duplicates(client: TestClient, alice_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch, email="alice.demo@gmail.com")
    _connect_alice(client, alice_token, monkeypatch, email="alice.new-email@gmail.com")

    status_resp = client.get("/calendar/connection", headers=_auth(alice_token))
    assert status_resp.json()["google_account_email"] == "alice.new-email@gmail.com"


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------


def test_bob_does_not_see_alices_connection(client: TestClient, alice_token: str, bob_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch)

    bob_status = client.get("/calendar/connection", headers=_auth(bob_token))
    assert bob_status.json()["status"] == "not_connected"


def test_bob_cannot_read_alices_events(client: TestClient, alice_token: str, bob_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch)

    resp = client.get(
        "/calendar/events",
        headers=_auth(bob_token),
        params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
    )
    assert resp.status_code == 404  # Bob has no connection of his own


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


def test_disconnect_removes_connection(client: TestClient, alice_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch)

    delete_resp = client.delete("/calendar/connection", headers=_auth(alice_token))
    assert delete_resp.status_code == 204

    status_resp = client.get("/calendar/connection", headers=_auth(alice_token))
    assert status_resp.json()["status"] == "not_connected"


# ---------------------------------------------------------------------------
# Revoked / invalid refresh token -> REAUTH_REQUIRED
# ---------------------------------------------------------------------------


def test_revoked_refresh_token_surfaces_as_reauth_required(
    client: TestClient, alice_token: str, monkeypatch
):
    # Connect with an already-expired cached token so the very next request
    # is forced through the refresh path (not served from the fresh cache).
    _connect_alice(client, alice_token, monkeypatch, expires_in=-10)

    async def fake_refresh_rejected(*, refresh_token: str):
        raise google_calendar.GoogleApiError(
            "invalid_grant: Token has been revoked", status_code=400, error_code="invalid_grant"
        )

    monkeypatch.setattr(google_calendar, "refresh_access_token", fake_refresh_rejected)

    resp = client.get(
        "/calendar/events",
        headers=_auth(alice_token),
        params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "REAUTH_REQUIRED"

    # The connection's stored status reflects this, so the UI can show it
    # without needing to fail a request first.
    status_resp = client.get("/calendar/connection", headers=_auth(alice_token))
    assert status_resp.json()["status"] == "reauth_required"


# ---------------------------------------------------------------------------
# Calendar events / availability / calendars -- normalized, Google mocked
# ---------------------------------------------------------------------------


def test_list_events_returns_normalized_events(client: TestClient, alice_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch)

    async def fake_list_events(*, access_token, calendar_id, time_min, time_max):
        return [
            {
                "id": "evt-1",
                "summary": "Design review",
                "start": {"dateTime": "2026-08-25T10:00:00Z"},
                "end": {"dateTime": "2026-08-25T11:00:00Z"},
                "status": "confirmed",
            }
        ]

    monkeypatch.setattr(google_calendar, "list_events", fake_list_events)

    resp = client.get(
        "/calendar/events",
        headers=_auth(alice_token),
        params={"start": "2026-08-25T00:00:00Z", "end": "2026-08-26T00:00:00Z"},
    )
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["event_id"] == "evt-1"
    assert events[0]["title"] == "Design review"
    assert events[0]["all_day"] is False


def test_availability_returns_normalized_busy_intervals(client: TestClient, alice_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch)

    async def fake_freebusy(*, access_token, calendar_id, time_min, time_max):
        return [{"start": "2026-08-25T09:00:00Z", "end": "2026-08-25T09:30:00Z"}]

    monkeypatch.setattr(google_calendar, "query_freebusy", fake_freebusy)

    resp = client.get(
        "/calendar/availability",
        headers=_auth(alice_token),
        params={"start": "2026-08-25T00:00:00Z", "end": "2026-08-26T00:00:00Z"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["busy"]) == 1
    assert body["calendar_id"] == "primary"


def test_availability_rejects_end_before_start(client: TestClient, alice_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch)
    resp = client.get(
        "/calendar/availability",
        headers=_auth(alice_token),
        params={"start": "2026-08-26T00:00:00Z", "end": "2026-08-25T00:00:00Z"},
    )
    assert resp.status_code == 422


def test_availability_rejects_overly_large_range(client: TestClient, alice_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch)
    resp = client.get(
        "/calendar/availability",
        headers=_auth(alice_token),
        params={"start": "2026-01-01T00:00:00Z", "end": "2027-01-01T00:00:00Z"},
    )
    assert resp.status_code == 422


def test_list_calendars_returns_normalized_list(client: TestClient, alice_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch)

    async def fake_list_calendars(*, access_token):
        return [
            {"id": "primary", "summary": "alice@gmail.com", "primary": True},
            {"id": "family#group.v.calendar.google.com", "summary": "Family", "primary": False},
        ]

    monkeypatch.setattr(google_calendar, "list_calendars", fake_list_calendars)

    resp = client.get("/calendar/calendars", headers=_auth(alice_token))
    assert resp.status_code == 200
    calendars = resp.json()
    assert len(calendars) == 2
    assert calendars[0]["primary"] is True


# ---------------------------------------------------------------------------
# Google API failure handling
# ---------------------------------------------------------------------------


def test_google_rate_limit_maps_to_429(client: TestClient, alice_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch)

    async def fake_list_events_rate_limited(*, access_token, calendar_id, time_min, time_max):
        raise google_calendar.GoogleApiError("rate limit", status_code=429)

    monkeypatch.setattr(google_calendar, "list_events", fake_list_events_rate_limited)

    resp = client.get(
        "/calendar/events",
        headers=_auth(alice_token),
        params={"start": "2026-08-25T00:00:00Z", "end": "2026-08-26T00:00:00Z"},
    )
    assert resp.status_code == 429


def test_google_server_error_maps_to_502(client: TestClient, alice_token: str, monkeypatch):
    _connect_alice(client, alice_token, monkeypatch)

    async def fake_list_events_down(*, access_token, calendar_id, time_min, time_max):
        raise google_calendar.GoogleApiError("server error", status_code=503)

    monkeypatch.setattr(google_calendar, "list_events", fake_list_events_down)

    resp = client.get(
        "/calendar/events",
        headers=_auth(alice_token),
        params={"start": "2026-08-25T00:00:00Z", "end": "2026-08-26T00:00:00Z"},
    )
    assert resp.status_code == 502
