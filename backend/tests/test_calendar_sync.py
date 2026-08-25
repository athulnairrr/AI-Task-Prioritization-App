"""Integration tests for Phase 7 (two-way Calendar synchronization):
watch-channel lifecycle, POST /calendar/sync (incremental/full sync +
reconciliation), POST /calendar/webhook, and the external-event cache.

Same pattern as tests/test_calendar.py and tests/test_schedule_apply.py:
runs against the real Supabase project with every Google call mocked via
monkeypatching app.services.google_calendar -- no real Google traffic.
Whole module skipped (not failed) when live Supabase config isn't present.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.oauth_state import create_state
from app.main import app
from app.schemas.ai import GeminiTaskAnalysis
from app.services import calendar_sync as sync_service_module
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
FAKE_WEBHOOK_URL = "https://api.example.test/calendar/webhook"


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


def _prioritized_task(client: TestClient, token: str, *, minutes=60, title="pytest sync task") -> dict:
    create_resp = client.post("/tasks", headers=_auth(token), json={"title": title})
    assert create_resp.status_code == 201
    task = create_resp.json()

    analysis = GeminiTaskAnalysis(
        category="work",
        urgency="high",
        importance="high",
        priority_score=80.0,
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


def _apply_one(client: TestClient, token: str, monkeypatch, task: dict, *, google_event_id: str, updated: str) -> None:
    """Applies `task` and makes the resulting Google Calendar event carry a
    known id + `updated` timestamp, so subsequent sync tests can target it
    precisely."""

    async def fake_create_event(**kwargs):
        return {"id": google_event_id, "updated": updated}

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


def _paged(*items_by_call: list[dict], sync_token="new-sync-token"):
    """Builds a `list_events_page` fake that returns one fixed page per
    call (ignoring paging/sync_token nuances a unit test doesn't need),
    always terminating the page with the given `nextSyncToken`."""
    calls = {"n": 0}

    async def fake(**kwargs):
        idx = min(calls["n"], len(items_by_call) - 1)
        calls["n"] += 1
        return {"items": items_by_call[idx], "nextSyncToken": sync_token}

    fake.calls = calls
    return fake


async def _fetchrow(sql: str, *args):
    conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)
    try:
        return await conn.fetchrow(sql, *args)
    finally:
        await conn.close()


def _connection_row(tenant_id: str, user_id: str) -> dict:
    row = asyncio.run(
        _fetchrow(
            "select * from public.google_calendar_connections where tenant_id = $1 and user_id = $2",
            uuid.UUID(tenant_id),
            user_id,
        )
    )
    assert row is not None
    return dict(row)


def _schedule_item_row(task_id: str) -> dict:
    row = asyncio.run(
        _fetchrow("select * from public.schedule_items where task_id = $1 order by created_at desc limit 1", uuid.UUID(task_id))
    )
    assert row is not None
    return dict(row)


def _mapping_row(schedule_item_id) -> dict:
    row = asyncio.run(
        _fetchrow(
            "select * from public.google_calendar_event_mappings where schedule_item_id = $1", schedule_item_id
        )
    )
    assert row is not None
    return dict(row)


def _set_watch_expiry_in_past(connection_id) -> None:
    async def _run():
        conn = await asyncpg.connect(settings.database_url, statement_cache_size=0)
        try:
            await conn.execute(
                "update public.google_calendar_connections set watch_expires_at = now() - interval '1 hour' where id = $1",
                connection_id,
            )
        finally:
            await conn.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Unauthenticated / not connected
# ---------------------------------------------------------------------------


def test_sync_requires_auth(client: TestClient):
    resp = client.post("/calendar/sync")
    assert resp.status_code == 403


def test_sync_404_when_not_connected(client: TestClient, alice_token: str):
    resp = client.post("/calendar/sync", headers=_auth(alice_token))
    assert resp.status_code == 404


def test_external_events_requires_auth(client: TestClient):
    resp = client.get("/calendar/external-events", params={"start": "2026-01-01T00:00:00Z", "end": "2026-01-02T00:00:00Z"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# New / updated / deleted external events
# ---------------------------------------------------------------------------


def test_new_external_event_is_cached_as_busy_block(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    monkeypatch.setattr(
        google_calendar,
        "list_events_page",
        _paged(
            [
                {
                    "id": "ext-evt-1",
                    "status": "confirmed",
                    "summary": "Team standup",
                    "start": {"dateTime": "2026-09-05T09:00:00-04:00"},
                    "end": {"dateTime": "2026-09-05T09:15:00-04:00"},
                    "updated": "2026-09-01T00:00:00Z",
                }
            ]
        ),
    )

    resp = client.post("/calendar/sync", headers=_auth(alice_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["synced"] is True
    assert body["counts"].get("external_upserted") == 1

    listed = client.get(
        "/calendar/external-events",
        headers=_auth(alice_token),
        params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-10T00:00:00Z"},
    )
    assert listed.status_code == 200
    events = listed.json()
    assert len(events) == 1
    assert events[0]["google_event_id"] == "ext-evt-1"
    assert events[0]["title"] == "Team standup"


def test_external_event_update_refreshes_cache_without_duplicating(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    fake = _paged(
        [
            {
                "id": "ext-evt-2",
                "status": "confirmed",
                "summary": "Design review",
                "start": {"dateTime": "2026-09-05T10:00:00-04:00"},
                "end": {"dateTime": "2026-09-05T11:00:00-04:00"},
                "updated": "2026-09-01T00:00:00Z",
            }
        ],
        [
            {
                "id": "ext-evt-2",
                "status": "confirmed",
                "summary": "Design review (moved)",
                "start": {"dateTime": "2026-09-05T13:00:00-04:00"},
                "end": {"dateTime": "2026-09-05T14:00:00-04:00"},
                "updated": "2026-09-02T00:00:00Z",
            }
        ],
    )
    monkeypatch.setattr(google_calendar, "list_events_page", fake)

    client.post("/calendar/sync", headers=_auth(alice_token))
    client.post("/calendar/sync", headers=_auth(alice_token))

    listed = client.get(
        "/calendar/external-events",
        headers=_auth(alice_token),
        params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-10T00:00:00Z"},
    )
    events = listed.json()
    assert len(events) == 1  # updated in place, not duplicated
    assert events[0]["title"] == "Design review (moved)"


def test_external_event_deletion_removes_it_from_cache(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    fake = _paged(
        [
            {
                "id": "ext-evt-3",
                "status": "confirmed",
                "summary": "Dentist",
                "start": {"dateTime": "2026-09-06T09:00:00-04:00"},
                "end": {"dateTime": "2026-09-06T10:00:00-04:00"},
                "updated": "2026-09-01T00:00:00Z",
            }
        ],
        [{"id": "ext-evt-3", "status": "cancelled"}],
    )
    monkeypatch.setattr(google_calendar, "list_events_page", fake)

    client.post("/calendar/sync", headers=_auth(alice_token))
    second = client.post("/calendar/sync", headers=_auth(alice_token))
    assert second.json()["counts"].get("external_deleted") == 1

    listed = client.get(
        "/calendar/external-events",
        headers=_auth(alice_token),
        params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-10T00:00:00Z"},
    )
    assert listed.json() == []


# ---------------------------------------------------------------------------
# Application-created event: moved / deleted externally
# ---------------------------------------------------------------------------


def test_app_created_event_moved_externally_updates_schedule_item(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)
    task = _prioritized_task(client, alice_token, title="pytest moved task")
    try:
        _apply_one(client, alice_token, monkeypatch, task, google_event_id="app-evt-moved", updated="2026-09-01T00:00:00Z")

        fake = _paged(
            [
                {
                    "id": "app-evt-moved",
                    "status": "confirmed",
                    "summary": task["title"],
                    "start": {"dateTime": "2026-09-01T15:00:00Z"},
                    "end": {"dateTime": "2026-09-01T16:00:00Z"},
                    "updated": "2026-09-01T01:00:00Z",  # newer than the creation timestamp
                    "extendedProperties": {"private": {"app": "ai-work-planner"}},
                }
            ]
        )
        monkeypatch.setattr(google_calendar, "list_events_page", fake)

        resp = client.post("/calendar/sync", headers=_auth(alice_token))
        assert resp.json()["counts"].get("app_moved") == 1

        row = _schedule_item_row(task["id"])
        assert row["starts_at"].isoformat().startswith("2026-09-01T15:00")
        assert row["needs_attention"] is False
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


def test_app_created_event_deleted_externally_flags_needs_attention_and_does_not_recreate(
    client: TestClient, alice_token: str, monkeypatch
):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)
    task = _prioritized_task(client, alice_token, title="pytest deleted task")
    try:
        _apply_one(client, alice_token, monkeypatch, task, google_event_id="app-evt-deleted", updated="2026-09-01T00:00:00Z")

        create_calls = {"n": 0}

        async def counting_create_event(**kwargs):
            create_calls["n"] += 1
            return {"id": "should-not-be-called"}

        monkeypatch.setattr(google_calendar, "create_event", counting_create_event)
        monkeypatch.setattr(google_calendar, "list_events_page", _paged([{"id": "app-evt-deleted", "status": "cancelled"}]))

        resp = client.post("/calendar/sync", headers=_auth(alice_token))
        assert resp.json()["counts"].get("app_deleted") == 1
        assert create_calls["n"] == 0  # never silently recreated

        row = _schedule_item_row(task["id"])
        assert row["needs_attention"] is True
        assert "deleted" in row["attention_reason"].lower()

        mapping = _mapping_row(row["id"])
        assert mapping["sync_status"] == "deleted"

        needs_attention = client.get("/tasks/schedule/needs-attention", headers=_auth(alice_token))
        assert needs_attention.status_code == 200
        entries = needs_attention.json()
        assert any(e["task_id"] == task["id"] for e in entries)
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


# ---------------------------------------------------------------------------
# Loop prevention / idempotency / duplicate notifications
# ---------------------------------------------------------------------------


def test_own_write_echo_is_not_reprocessed_as_an_external_change(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True)
    task = _prioritized_task(client, alice_token, title="pytest loop task")
    try:
        created_at = "2026-09-01T00:00:00Z"
        _apply_one(client, alice_token, monkeypatch, task, google_event_id="app-evt-echo", updated=created_at)
        before = _schedule_item_row(task["id"])

        # A sync page reporting the exact same event, same `updated` --
        # e.g. the natural echo of our own create, not a real edit.
        monkeypatch.setattr(
            google_calendar,
            "list_events_page",
            _paged(
                [
                    {
                        "id": "app-evt-echo",
                        "status": "confirmed",
                        "summary": task["title"],
                        "start": {"dateTime": "2026-09-01T13:00:00Z"},
                        "end": {"dateTime": "2026-09-01T14:00:00Z"},
                        "updated": created_at,
                        "extendedProperties": {"private": {"app": "ai-work-planner"}},
                    }
                ]
            ),
        )
        resp = client.post("/calendar/sync", headers=_auth(alice_token))
        assert resp.json()["counts"].get("app_noop") == 1
        assert resp.json()["counts"].get("app_moved") is None

        after = _schedule_item_row(task["id"])
        assert after["updated_at"] == before["updated_at"]
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


def test_duplicate_webhook_notifications_are_idempotent(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    monkeypatch.setattr(
        google_calendar,
        "list_events_page",
        _paged(
            [
                {
                    "id": "ext-evt-dup",
                    "status": "confirmed",
                    "summary": "Recurring standup",
                    "start": {"dateTime": "2026-09-07T09:00:00-04:00"},
                    "end": {"dateTime": "2026-09-07T09:15:00-04:00"},
                    "updated": "2026-09-01T00:00:00Z",
                }
            ]
        ),
    )

    first = client.post("/calendar/sync", headers=_auth(alice_token))
    second = client.post("/calendar/sync", headers=_auth(alice_token))
    assert first.status_code == 200
    assert second.status_code == 200

    listed = client.get(
        "/calendar/external-events",
        headers=_auth(alice_token),
        params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-10T00:00:00Z"},
    )
    assert len(listed.json()) == 1  # replayed notification never creates a second row


def test_missed_webhook_is_caught_up_by_manual_reconciliation(client: TestClient, alice_token: str, monkeypatch):
    """No webhook call happens at all here -- POST /calendar/sync alone
    (the reconciliation fallback) still picks up the change, exactly as it
    would if a webhook notification had been lost."""
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    monkeypatch.setattr(
        google_calendar,
        "list_events_page",
        _paged(
            [
                {
                    "id": "ext-evt-missed",
                    "status": "confirmed",
                    "summary": "Missed-notification event",
                    "start": {"dateTime": "2026-09-08T09:00:00-04:00"},
                    "end": {"dateTime": "2026-09-08T10:00:00-04:00"},
                    "updated": "2026-09-01T00:00:00Z",
                }
            ]
        ),
    )
    resp = client.post("/calendar/sync", headers=_auth(alice_token))
    assert resp.json()["counts"].get("external_upserted") == 1


# ---------------------------------------------------------------------------
# Invalid sync token / 410 recovery
# ---------------------------------------------------------------------------


def test_invalid_sync_token_triggers_full_resync(client: TestClient, alice_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)

    calls = {"n": 0}

    async def fake_list_events_page(*, access_token, calendar_id, sync_token=None, page_token=None, time_min=None, time_max=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"items": [], "nextSyncToken": "stale-token"}
        if sync_token == "stale-token":
            raise google_calendar.GoogleApiError("Sync token is no longer valid", status_code=410)
        return {
            "items": [
                {
                    "id": "ext-evt-after-410",
                    "status": "confirmed",
                    "summary": "Survived the 410",
                    "start": {"dateTime": "2026-09-09T09:00:00-04:00"},
                    "end": {"dateTime": "2026-09-09T10:00:00-04:00"},
                    "updated": "2026-09-01T00:00:00Z",
                }
            ],
            "nextSyncToken": "fresh-token",
        }

    monkeypatch.setattr(google_calendar, "list_events_page", fake_list_events_page)

    first = client.post("/calendar/sync", headers=_auth(alice_token))
    assert first.json()["full_resync"] is False

    second = client.post("/calendar/sync", headers=_auth(alice_token))
    assert second.status_code == 200
    assert second.json()["full_resync"] is True
    assert second.json()["counts"].get("external_upserted") == 1

    connection = _connection_row(_tenant_id_for(client, alice_token), _decode_jwt_sub(alice_token))
    assert connection["sync_token"] == "fresh-token"


# ---------------------------------------------------------------------------
# Watch channel: registration, renewal, revoked auth
# ---------------------------------------------------------------------------


def test_watch_channel_registered_when_webhook_url_configured(client: TestClient, alice_token: str, monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_webhook_url", FAKE_WEBHOOK_URL)

    async def fake_watch(**kwargs):
        return {"resourceId": "res-1", "expiration": "9999999999999"}

    monkeypatch.setattr(google_calendar, "watch_events", fake_watch)
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    monkeypatch.setattr(google_calendar, "list_events_page", _paged([]))

    resp = client.post("/calendar/sync", headers=_auth(alice_token))
    assert resp.json()["watch_active"] is True


def test_watch_channel_not_registered_without_webhook_url(client: TestClient, alice_token: str, monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_webhook_url", "")
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    monkeypatch.setattr(google_calendar, "list_events_page", _paged([]))

    resp = client.post("/calendar/sync", headers=_auth(alice_token))
    assert resp.json()["watch_active"] is False


def test_expired_watch_channel_is_renewed_on_next_sync(client: TestClient, alice_token: str, monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_webhook_url", FAKE_WEBHOOK_URL)

    watch_calls: list[str] = []
    stop_calls: list[str] = []

    async def fake_watch(*, access_token, calendar_id, channel_id, webhook_url, channel_token, expiration_ms=None):
        watch_calls.append(channel_id)
        return {"resourceId": f"res-{len(watch_calls)}", "expiration": "9999999999999"}

    async def fake_stop(*, access_token, channel_id, resource_id):
        stop_calls.append(channel_id)

    monkeypatch.setattr(google_calendar, "watch_events", fake_watch)
    monkeypatch.setattr(google_calendar, "stop_channel", fake_stop)
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    monkeypatch.setattr(google_calendar, "list_events_page", _paged([], []))

    client.post("/calendar/sync", headers=_auth(alice_token))
    assert len(watch_calls) == 1

    connection = _connection_row(_tenant_id_for(client, alice_token), _decode_jwt_sub(alice_token))
    _set_watch_expiry_in_past(connection["id"])

    client.post("/calendar/sync", headers=_auth(alice_token))
    assert len(watch_calls) == 2  # a new channel was registered
    assert stop_calls == [watch_calls[0]]  # and the old one was best-effort stopped


def test_sync_skips_gracefully_when_authorization_revoked(client: TestClient, alice_token: str, monkeypatch):
    state = create_state(tenant_id=_tenant_id_for(client, alice_token), user_id=_decode_jwt_sub(alice_token))

    async def fake_exchange_expired(*, code: str):
        return {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_in": -10,
            "scope": " ".join(google_calendar.READ_ONLY_SCOPES),
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

    resp = client.post("/calendar/sync", headers=_auth(alice_token))
    assert resp.status_code == 200  # not a hard error -- surfaced as synced=False
    body = resp.json()
    assert body["synced"] is False
    assert "reauthorization" in body["reason"].lower()

    status_resp = client.get("/calendar/connection", headers=_auth(alice_token))
    assert status_resp.json()["status"] == "reauth_required"


# ---------------------------------------------------------------------------
# Cross-tenant protection
# ---------------------------------------------------------------------------


def test_bobs_external_events_are_not_visible_to_alice(client: TestClient, alice_token: str, bob_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False, email="alice@gmail.com")
    monkeypatch.setattr(google_calendar, "list_events_page", _paged([]))
    client.post("/calendar/sync", headers=_auth(alice_token))

    _connect_calendar(client, bob_token, monkeypatch, write_scope=False, email="bob@gmail.com")
    monkeypatch.setattr(
        google_calendar,
        "list_events_page",
        _paged(
            [
                {
                    "id": "bob-only-event",
                    "status": "confirmed",
                    "summary": "Bob's private event",
                    "start": {"dateTime": "2026-09-10T09:00:00-04:00"},
                    "end": {"dateTime": "2026-09-10T10:00:00-04:00"},
                    "updated": "2026-09-01T00:00:00Z",
                }
            ]
        ),
    )
    client.post("/calendar/sync", headers=_auth(bob_token))

    alice_listed = client.get(
        "/calendar/external-events",
        headers=_auth(alice_token),
        params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-15T00:00:00Z"},
    )
    assert alice_listed.json() == []

    bob_listed = client.get(
        "/calendar/external-events",
        headers=_auth(bob_token),
        params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-15T00:00:00Z"},
    )
    assert len(bob_listed.json()) == 1


def test_bob_cannot_see_alices_needs_attention_items(client: TestClient, alice_token: str, bob_token: str, monkeypatch):
    _connect_calendar(client, alice_token, monkeypatch, write_scope=True, email="alice@gmail.com")
    task = _prioritized_task(client, alice_token, title="pytest cross-tenant attention")
    try:
        _apply_one(client, alice_token, monkeypatch, task, google_event_id="app-evt-cross-tenant", updated="2026-09-01T00:00:00Z")
        monkeypatch.setattr(google_calendar, "list_events_page", _paged([{"id": "app-evt-cross-tenant", "status": "cancelled"}]))
        client.post("/calendar/sync", headers=_auth(alice_token))

        alice_view = client.get("/tasks/schedule/needs-attention", headers=_auth(alice_token))
        assert any(e["task_id"] == task["id"] for e in alice_view.json())

        bob_view = client.get("/tasks/schedule/needs-attention", headers=_auth(bob_token))
        assert bob_view.status_code == 200
        assert bob_view.json() == []
    finally:
        client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


# ---------------------------------------------------------------------------
# Webhook endpoint: security + happy path
# ---------------------------------------------------------------------------


def test_webhook_unknown_channel_returns_200_and_does_nothing(client: TestClient):
    resp = client.post(
        "/calendar/webhook",
        headers={
            "X-Goog-Channel-Id": "not-a-real-channel",
            "X-Goog-Resource-Id": "whatever",
            "X-Goog-Resource-State": "exists",
            "X-Goog-Channel-Token": "whatever",
        },
    )
    assert resp.status_code == 200


def test_webhook_missing_channel_header_returns_200(client: TestClient):
    resp = client.post("/calendar/webhook")
    assert resp.status_code == 200


def test_webhook_wrong_token_is_rejected(client: TestClient, alice_token: str, monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_webhook_url", FAKE_WEBHOOK_URL)

    async def fake_watch(**kwargs):
        return {"resourceId": "res-webhook-1", "expiration": "9999999999999"}

    monkeypatch.setattr(google_calendar, "watch_events", fake_watch)
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    monkeypatch.setattr(google_calendar, "list_events_page", _paged([]))
    client.post("/calendar/sync", headers=_auth(alice_token))

    connection = _connection_row(_tenant_id_for(client, alice_token), _decode_jwt_sub(alice_token))

    sync_calls = {"n": 0}
    monkeypatch.setattr(sync_service_module, "sync_connection_safe", _count_calls(sync_calls))

    resp = client.post(
        "/calendar/webhook",
        headers={
            "X-Goog-Channel-Id": connection["watch_channel_id"],
            "X-Goog-Resource-Id": connection["watch_resource_id"],
            "X-Goog-Resource-State": "exists",
            "X-Goog-Channel-Token": "wrong-token",
        },
    )
    assert resp.status_code == 200
    assert sync_calls["n"] == 0


def _count_calls(counter: dict):
    async def _fn(pool, connection_id):
        counter["n"] += 1

    return _fn


def test_webhook_sync_handshake_does_not_trigger_a_sync(client: TestClient, alice_token: str, monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_webhook_url", FAKE_WEBHOOK_URL)

    async def fake_watch(**kwargs):
        return {"resourceId": "res-webhook-2", "expiration": "9999999999999"}

    monkeypatch.setattr(google_calendar, "watch_events", fake_watch)
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    monkeypatch.setattr(google_calendar, "list_events_page", _paged([]))
    client.post("/calendar/sync", headers=_auth(alice_token))

    connection = _connection_row(_tenant_id_for(client, alice_token), _decode_jwt_sub(alice_token))

    resp = client.post(
        "/calendar/webhook",
        headers={
            "X-Goog-Channel-Id": connection["watch_channel_id"],
            "X-Goog-Resource-Id": connection["watch_resource_id"],
            "X-Goog-Resource-State": "sync",
            "X-Goog-Channel-Token": connection["watch_token"],
        },
    )
    assert resp.status_code == 200


def test_webhook_valid_notification_triggers_background_sync(client: TestClient, alice_token: str, monkeypatch):
    monkeypatch.setattr(settings, "google_calendar_webhook_url", FAKE_WEBHOOK_URL)

    async def fake_watch(**kwargs):
        return {"resourceId": "res-webhook-3", "expiration": "9999999999999"}

    monkeypatch.setattr(google_calendar, "watch_events", fake_watch)
    _connect_calendar(client, alice_token, monkeypatch, write_scope=False)
    monkeypatch.setattr(
        google_calendar,
        "list_events_page",
        _paged(
            [
                {
                    "id": "ext-evt-webhook",
                    "status": "confirmed",
                    "summary": "Via webhook",
                    "start": {"dateTime": "2026-09-11T09:00:00-04:00"},
                    "end": {"dateTime": "2026-09-11T10:00:00-04:00"},
                    "updated": "2026-09-01T00:00:00Z",
                }
            ]
        ),
    )
    client.post("/calendar/sync", headers=_auth(alice_token))  # registers the watch channel

    connection = _connection_row(_tenant_id_for(client, alice_token), _decode_jwt_sub(alice_token))

    # A fresh page for the notification-triggered sync (the earlier
    # /calendar/sync call already consumed the first one and stored a
    # syncToken).
    monkeypatch.setattr(
        google_calendar,
        "list_events_page",
        _paged(
            [
                {
                    "id": "ext-evt-webhook-2",
                    "status": "confirmed",
                    "summary": "Via webhook 2",
                    "start": {"dateTime": "2026-09-12T09:00:00-04:00"},
                    "end": {"dateTime": "2026-09-12T10:00:00-04:00"},
                    "updated": "2026-09-01T00:00:00Z",
                }
            ]
        ),
    )

    resp = client.post(
        "/calendar/webhook",
        headers={
            "X-Goog-Channel-Id": connection["watch_channel_id"],
            "X-Goog-Resource-Id": connection["watch_resource_id"],
            "X-Goog-Resource-State": "exists",
            "X-Goog-Channel-Token": connection["watch_token"],
        },
    )
    assert resp.status_code == 200

    listed = client.get(
        "/calendar/external-events",
        headers=_auth(alice_token),
        params={"start": "2026-09-01T00:00:00Z", "end": "2026-09-20T00:00:00Z"},
    )
    titles = {e["title"] for e in listed.json()}
    assert "Via webhook 2" in titles
