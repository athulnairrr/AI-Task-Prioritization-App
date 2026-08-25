"""Direct REST calls to Google's OAuth and Calendar APIs.

Deliberately implemented with plain `httpx` calls rather than
`google-api-python-client` -- that package is synchronous (would need
thread-pool wrapping in an async FastAPI app) and pulls in a large
dependency tree for what is, here, a handful of narrow, read-only REST
calls. See /docs/decisions.md ADR-012.

Every function raises `GoogleApiError` on any non-2xx response; nothing
here talks to the database or knows about tenants -- that's
app/services/calendar_connections.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from app.core.config import get_settings

AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

# Minimum scopes for the initial connection: read events/free-busy, and
# identify which Google account was connected (for display -- "Connected
# as you@gmail.com"). No write scope is requested here.
READ_ONLY_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# Added only when the user explicitly chooses to apply a schedule (a later
# phase -- not requested during initial connect, and no route in this
# phase uses it). Kept alongside READ_ONLY_SCOPES, not in place of it, so
# an incremental-auth request (see build_authorization_url's `scopes`
# param + include_granted_scopes below) asks for everything the connection
# should end up with, matching Google's documented incremental-authorization
# pattern. See /docs/architecture.md "Incremental OAuth for write access"
# and ADR-016.
CALENDAR_EVENTS_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
WRITE_SCOPES = [*READ_ONLY_SCOPES, CALENDAR_EVENTS_WRITE_SCOPE]

_REQUEST_TIMEOUT = 15.0


class GoogleApiError(Exception):
    """Raised for any failed call to Google (network, 4xx, 5xx)."""

    def __init__(self, message: str, *, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code  # e.g. "invalid_grant" from a token error body


def build_authorization_url(*, state: str, scopes: list[str] | None = None) -> str:
    """`scopes` defaults to READ_ONLY_SCOPES (the only thing any route in
    this phase requests). A future "apply schedule" flow passes
    WRITE_SCOPES instead -- with `include_granted_scopes=true` (always
    set, below), Google treats that as incremental authorization: the
    user is asked to additionally grant calendar.events, and the resulting
    refresh token covers the union of what they'd already granted plus
    the new scope, without them having to re-consent to calendar.readonly
    from scratch. See ADR-016."""
    settings = get_settings()
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes if scopes is not None else READ_ONLY_SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        # Forces Google to return a refresh_token even if this user
        # previously granted consent (Google only returns one on the very
        # first consent otherwise) -- without this, a reconnect after a
        # revoke could silently fail to get a usable refresh_token.
        "prompt": "consent",
        "state": state,
    }
    query = httpx.QueryParams(params)
    return f"{AUTH_BASE_URL}?{query}"


async def exchange_code_for_tokens(*, code: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    return _parse_token_response(resp)


async def refresh_access_token(*, refresh_token: str) -> dict[str, Any]:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "refresh_token": refresh_token,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "grant_type": "refresh_token",
            },
        )
    return _parse_token_response(resp)


async def revoke_token(*, token: str) -> None:
    """Best-effort revoke at Google. Callers should not fail the whole
    disconnect operation if this fails -- the local connection is deleted
    either way (see app/services/calendar_connections.py)."""
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(REVOKE_URL, data={"token": token})
    if resp.status_code >= 400:
        raise GoogleApiError(f"Revoke failed: {resp.text}", status_code=resp.status_code)


async def get_userinfo(*, access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.get(USERINFO_URL, headers=_bearer(access_token))
    return _parse_json_response(resp)


async def list_calendars(*, access_token: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.get(
            f"{CALENDAR_API_BASE}/users/me/calendarList",
            headers=_bearer(access_token),
            params={"minAccessRole": "reader"},
        )
    return _parse_json_response(resp).get("items", [])


async def list_events(
    *, access_token: str, calendar_id: str, time_min: datetime, time_max: datetime
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.get(
            f"{CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events",
            headers=_bearer(access_token),
            params={
                "timeMin": _iso(time_min),
                "timeMax": _iso(time_max),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "250",
            },
        )
    return _parse_json_response(resp).get("items", [])


async def get_calendar_timezone(*, access_token: str, calendar_id: str) -> str | None:
    """The connected calendar's own IANA timezone (e.g. "America/New_York"),
    per Google's calendars.get -- the source of truth this app uses for
    working hours and event timeZone fields (see /docs/architecture.md
    "Timezone strategy"). Returns None if Google doesn't report one
    (unusual, but the caller falls back to UTC rather than crashing)."""
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.get(
            f"{CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}",
            headers=_bearer(access_token),
        )
    return _parse_json_response(resp).get("timeZone")


async def create_event(
    *,
    access_token: str,
    calendar_id: str,
    summary: str,
    description: str,
    start: datetime,
    end: datetime,
    time_zone: str,
    private_properties: dict[str, str],
) -> dict[str, Any]:
    """Creates a new event. Never touches any other event on the
    calendar -- this is purely an insert. `private_properties` is stored
    as the event's `extendedProperties.private` (visible only to this
    app's API calls, not shown in Google Calendar's UI), used to tag the
    event with our own task/tenant identifiers -- see
    /docs/architecture.md "Google event metadata"."""
    # Localize to the target zone before serializing -- the instant is
    # unchanged, but the wall-clock representation Google stores/displays
    # then genuinely matches `time_zone`, not just a same-instant UTC
    # timestamp with a same-named-but-unapplied timeZone field.
    tz = ZoneInfo(time_zone)
    local_start = start.astimezone(tz)
    local_end = end.astimezone(tz)

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(
            f"{CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events",
            headers=_bearer(access_token),
            json={
                "summary": summary,
                "description": description,
                "start": {"dateTime": local_start.isoformat(), "timeZone": time_zone},
                "end": {"dateTime": local_end.isoformat(), "timeZone": time_zone},
                "extendedProperties": {"private": private_properties},
            },
        )
    return _parse_json_response(resp)


async def watch_events(
    *,
    access_token: str,
    calendar_id: str,
    channel_id: str,
    webhook_url: str,
    channel_token: str,
    expiration_ms: int | None = None,
) -> dict[str, Any]:
    """Registers a push-notification (watch) channel on this calendar's
    events collection. `webhook_url` must be a real public HTTPS
    endpoint -- Google refuses http:// and unreachable/localhost
    addresses. `channel_token` is echoed back on every notification as
    `X-Goog-Channel-Token`; the webhook handler uses it (plus the
    returned `resourceId`) to verify a notification actually belongs to
    the channel it claims to. See /docs/architecture.md "Watch channels"."""
    body: dict[str, Any] = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
        "token": channel_token,
    }
    if expiration_ms is not None:
        body["expiration"] = str(expiration_ms)
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(
            f"{CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events/watch",
            headers=_bearer(access_token),
            json=body,
        )
    return _parse_json_response(resp)


async def stop_channel(*, access_token: str, channel_id: str, resource_id: str) -> None:
    """Best-effort -- callers (channel renewal) must not fail just because
    Google has already expired/forgotten the old channel."""
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(
            f"{CALENDAR_API_BASE}/channels/stop",
            headers=_bearer(access_token),
            json={"id": channel_id, "resourceId": resource_id},
        )
    if resp.status_code >= 400 and resp.status_code not in (404, 410):
        raise GoogleApiError(f"Stop channel failed: {resp.text}", status_code=resp.status_code)


async def list_events_page(
    *,
    access_token: str,
    calendar_id: str,
    sync_token: str | None = None,
    page_token: str | None = None,
    time_min: datetime | None = None,
    time_max: datetime | None = None,
) -> dict[str, Any]:
    """One page of events for incremental (`sync_token` set) or full
    (bounded by `time_min`/`time_max`) synchronization. `showDeleted` is
    always on so cancellations surface as tombstones the caller can act
    on. `orderBy` is deliberately never set here -- Google's API rejects
    it whenever `syncToken` is used, and the same request shape must be
    reused across every page of a given sync for the resulting
    `nextSyncToken` to be valid on the next call. Raises `GoogleApiError`
    with `status_code == 410` if `sync_token` is no longer valid (Google's
    documented signal to discard it and do a full resync)."""
    params: dict[str, str] = {"singleEvents": "true", "showDeleted": "true", "maxResults": "250"}
    if sync_token:
        params["syncToken"] = sync_token
    else:
        if time_min is not None:
            params["timeMin"] = _iso(time_min)
        if time_max is not None:
            params["timeMax"] = _iso(time_max)
    if page_token:
        params["pageToken"] = page_token
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.get(
            f"{CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events",
            headers=_bearer(access_token),
            params=params,
        )
    return _parse_json_response(resp)


async def delete_event(*, access_token: str, calendar_id: str, event_id: str) -> None:
    """Test-cleanup helper only -- no application route in this phase
    exposes deleting a Calendar event; this exists so the live test that
    creates one real event can remove it afterward."""
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.delete(
            f"{CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}",
            headers=_bearer(access_token),
        )
    if resp.status_code >= 400 and resp.status_code != 410:  # 410 Gone == already deleted
        raise GoogleApiError(f"Delete failed: {resp.text}", status_code=resp.status_code)


async def query_freebusy(
    *, access_token: str, calendar_id: str, time_min: datetime, time_max: datetime
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(
            f"{CALENDAR_API_BASE}/freeBusy",
            headers=_bearer(access_token),
            json={
                "timeMin": _iso(time_min),
                "timeMax": _iso(time_max),
                "items": [{"id": calendar_id}],
            },
        )
    body = _parse_json_response(resp)
    calendars = body.get("calendars", {})
    entry = calendars.get(calendar_id, {})
    if entry.get("errors"):
        raise GoogleApiError(f"Free/busy query failed: {entry['errors']}")
    return entry.get("busy", [])


def _bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.isoformat()


def _parse_json_response(resp: httpx.Response) -> dict[str, Any]:
    if resp.status_code >= 400:
        error_code = None
        try:
            body = resp.json()
            error_field = body.get("error")
            # Calendar API errors nest as {"error": {"status": "..."}};
            # some auth-adjacent endpoints (e.g. userinfo) instead return a
            # plain string like {"error": "invalid_token"} -- handle both.
            if isinstance(error_field, dict):
                error_code = error_field.get("status")
            elif isinstance(error_field, str):
                error_code = error_field
        except ValueError:
            pass
        raise GoogleApiError(
            f"Google API request failed: {resp.text}",
            status_code=resp.status_code,
            error_code=str(error_code) if error_code else None,
        )
    return resp.json()


def _parse_token_response(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except ValueError as exc:
        raise GoogleApiError(f"Malformed response from Google token endpoint: {resp.text}") from exc

    if resp.status_code >= 400:
        error_code = body.get("error")
        raise GoogleApiError(
            f"Google token request failed: {body.get('error_description', body)}",
            status_code=resp.status_code,
            error_code=error_code,
        )
    return body
