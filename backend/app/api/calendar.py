"""Google Calendar connection + read-only calendar/availability routes.

Two different trust models coexist in this file, which is worth reading
before touching it:

  * Every route except `/callback` is a normal authenticated route (bearer
    token + tenant-checked), same as tasks.
  * `/callback` is hit directly by Google's browser redirect -- there is no
    Authorization header available on that request at all. It trusts only
    the signed `state` value minted by `/connect` (see app/core/oauth_state.py).

No route here creates, modifies, or deletes a Google Calendar *event* --
read-only for this phase (see /docs/architecture.md "Google Calendar
integration").
"""

from __future__ import annotations

import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse

from app.api.deps import get_current_user, get_tenant_context
from app.core.db import get_pool
from app.core.oauth_state import OAuthStateError, create_state, verify_state
from app.schemas.auth import AuthenticatedUser, TenantMembership
from app.schemas.calendar import (
    AvailabilityOut,
    CalendarConnectionOut,
    CalendarEventOut,
    CalendarSyncResultOut,
    ConnectUrlOut,
    ExternalCalendarEventOut,
    GoogleCalendarOut,
)
from app.services import calendar_connections as conn_service
from app.services import calendar_events as events_service
from app.services import calendar_sync as sync_service
from app.services import google_calendar

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar"])

_MAX_RANGE = timedelta(days=90)


@router.get("/connection", response_model=CalendarConnectionOut)
async def get_connection(
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> CalendarConnectionOut:
    record = await conn_service.get_connection(pool, uuid.UUID(tenant.tenant_id), user.id)
    return conn_service.to_connection_out(record)


@router.get("/connect", response_model=ConnectUrlOut)
async def connect(
    scope: str = Query(default="read", pattern="^(read|write)$"),
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
) -> ConnectUrlOut:
    """Returns the Google authorization URL. The client performs the actual
    top-level browser redirect itself (`window.location = authorization_url`
    on web; an external browser launch on mobile) -- this call is a normal
    authenticated fetch, not the redirect itself.

    `?scope=write` requests calendar.events *in addition to* the read
    scopes (Google's incremental authorization -- `include_granted_scopes`
    is always set, so an existing connection is upgraded in place, not
    replaced; see ADR-019). Nothing calls this with scope=write except the
    "Apply Schedule" flow, and only when the connection doesn't already
    have write access."""
    requested_scopes = google_calendar.WRITE_SCOPES if scope == "write" else google_calendar.READ_ONLY_SCOPES
    state = create_state(tenant_id=tenant.tenant_id, user_id=user.id)
    return ConnectUrlOut(
        authorization_url=google_calendar.build_authorization_url(state=state, scopes=requested_scopes)
    )


@router.get("/callback", response_class=HTMLResponse, include_in_schema=False)
async def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    pool: asyncpg.Pool = Depends(get_pool),
) -> HTMLResponse:
    if error:
        return _html_page(
            "Connection cancelled",
            f"Google Calendar was not connected ({error}). You can close this window and try again.",
            ok=False,
        )

    if not code or not state:
        return _html_page("Connection failed", "Missing authorization code or state.", ok=False)

    try:
        tenant_id, user_id = verify_state(state)
    except OAuthStateError:
        return _html_page(
            "Connection failed",
            "This authorization link is invalid or has expired. Please try connecting again.",
            ok=False,
        )

    try:
        token_data = await google_calendar.exchange_code_for_tokens(code=code)
    except google_calendar.GoogleApiError as exc:
        return _html_page("Connection failed", f"Google rejected the authorization request: {exc}", ok=False)

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    if not access_token or not refresh_token:
        return _html_page(
            "Connection failed",
            "Google didn't grant a long-lived authorization. If you've connected this app "
            "before, remove it from your Google Account's third-party access list and try again.",
            ok=False,
        )

    try:
        userinfo = await google_calendar.get_userinfo(access_token=access_token)
    except google_calendar.GoogleApiError:
        userinfo = {}
    google_account_email = userinfo.get("email") or "unknown"

    try:
        calendar_timezone = await google_calendar.get_calendar_timezone(
            access_token=access_token, calendar_id=conn_service.DEFAULT_CALENDAR_ID
        )
    except google_calendar.GoogleApiError:
        calendar_timezone = None  # scheduling falls back to UTC; not fatal to the connection itself

    expires_in = token_data.get("expires_in", 3600)
    token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    # Google's own record of what was actually granted -- not merely what
    # this request asked for (see build_authorization_url's `scopes` arg
    # and ADR-019). An empty string here (some responses omit `scope` when
    # nothing changed) leaves any previously-recorded scopes untouched
    # rather than erasing them -- see upsert_connection's `coalesce`-style
    # handling.
    granted_scopes = token_data.get("scope", "")

    record = await conn_service.upsert_connection(
        pool,
        tenant_id=uuid.UUID(tenant_id),
        user_id=user_id,
        google_account_email=google_account_email,
        calendar_id=conn_service.DEFAULT_CALENDAR_ID,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=token_expires_at,
        granted_scopes=granted_scopes,
        calendar_timezone=calendar_timezone,
    )

    # Best-effort -- a connection is fully usable without push
    # notifications (POST /calendar/sync is the fallback), so this never
    # blocks the connect flow itself. No-ops entirely without a configured
    # public webhook URL (e.g. local development).
    try:
        await sync_service.ensure_watch_channel(pool, record)
    except Exception:
        logger.exception("Watch channel registration failed during connect for connection %s", record.id)

    return _html_page(
        "Calendar connected",
        f"Connected as {google_account_email}. You can close this window and return to the app.",
    )


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def disconnect(
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> None:
    record = await conn_service.get_connection(pool, uuid.UUID(tenant.tenant_id), user.id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar not connected.")
    if record.watch_channel_id and record.watch_resource_id:
        try:
            access_token = await conn_service.get_valid_access_token(pool, record)
            await google_calendar.stop_channel(
                access_token=access_token, channel_id=record.watch_channel_id, resource_id=record.watch_resource_id
            )
        except Exception:
            pass  # best-effort -- the connection row is deleted either way, next line
    await conn_service.revoke_and_delete(pool, record)


@router.get("/calendars", response_model=list[GoogleCalendarOut])
async def list_calendars(
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[GoogleCalendarOut]:
    record = await _get_record_or_404(pool, tenant, user)
    access_token = await _access_token_or_reauth(pool, record)
    try:
        raw = await google_calendar.list_calendars(access_token=access_token)
    except google_calendar.GoogleApiError as exc:
        raise _map_google_error(exc) from exc
    return [
        GoogleCalendarOut(id=c["id"], summary=c.get("summary"), primary=c.get("primary", False))
        for c in raw
    ]


@router.get("/events", response_model=list[CalendarEventOut])
async def list_events(
    start: datetime = Query(...),
    end: datetime = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[CalendarEventOut]:
    _validate_range(start, end)
    record = await _get_record_or_404(pool, tenant, user)
    access_token = await _access_token_or_reauth(pool, record)
    try:
        raw_events = await google_calendar.list_events(
            access_token=access_token, calendar_id=record.calendar_id, time_min=start, time_max=end
        )
    except google_calendar.GoogleApiError as exc:
        raise _map_google_error(exc) from exc
    return events_service.normalize_events(raw_events)


@router.get("/availability", response_model=AvailabilityOut)
async def get_availability(
    start: datetime = Query(...),
    end: datetime = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> AvailabilityOut:
    _validate_range(start, end)
    record = await _get_record_or_404(pool, tenant, user)
    access_token = await _access_token_or_reauth(pool, record)
    try:
        raw_busy = await google_calendar.query_freebusy(
            access_token=access_token, calendar_id=record.calendar_id, time_min=start, time_max=end
        )
    except google_calendar.GoogleApiError as exc:
        raise _map_google_error(exc) from exc
    return AvailabilityOut(
        range_start=start,
        range_end=end,
        calendar_id=record.calendar_id,
        busy=events_service.normalize_busy_intervals(raw_busy),
    )


@router.get("/external-events", response_model=list[ExternalCalendarEventOut])
async def list_external_events(
    start: datetime = Query(...),
    end: datetime = Query(...),
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[ExternalCalendarEventOut]:
    """Busy blocks from Calendar events this app did NOT create, from the
    locally-synced cache (`google_calendar_external_events`) rather than a
    live Google call -- cheap to call often, and exactly what changes when
    a realtime update for that table arrives. See /docs/architecture.md
    "Event mapping: external events"."""
    _validate_range(start, end)
    record = await _get_record_or_404(pool, tenant, user)
    rows = await pool.fetch(
        """
        select google_event_id, title, starts_at, ends_at, all_day, status
        from public.google_calendar_external_events
        where tenant_id = $1 and connection_id = $2
          and status <> 'cancelled' and starts_at < $4 and ends_at > $3
        order by starts_at
        """,
        uuid.UUID(tenant.tenant_id),
        record.id,
        start,
        end,
    )
    return [
        ExternalCalendarEventOut(
            google_event_id=r["google_event_id"],
            title=r["title"],
            start=r["starts_at"],
            end=r["ends_at"],
            all_day=r["all_day"],
            status=r["status"],
        )
        for r in rows
    ]


@router.post("/sync", response_model=CalendarSyncResultOut)
async def sync_calendar(
    user: AuthenticatedUser = Depends(get_current_user),
    tenant: TenantMembership = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
) -> CalendarSyncResultOut:
    """Explicit reconciliation: renews the watch channel if it's missing
    or close to expiring, then runs an incremental (or full, if there's no
    sync token yet) sync inline. This is the lightweight fallback the
    brief allows in place of polling -- call it when a client opens/
    foregrounds the app or the user taps refresh, not on a timer. Webhook-
    triggered syncs (the primary path when push notifications are
    configured) go through the same `calendar_sync.sync_connection`, just
    from a background task instead of this request."""
    record = await _get_record_or_404(pool, tenant, user)
    await sync_service.ensure_watch_channel(pool, record)
    result = await sync_service.sync_connection(pool, record.id)
    refreshed = await conn_service.get_connection_by_id(pool, record.id)
    return CalendarSyncResultOut(
        synced=result.synced,
        reason=result.reason,
        full_resync=result.full_resync,
        processed=result.processed,
        counts=result.counts,
        watch_active=refreshed.watch_active if refreshed else False,
        last_synced_at=refreshed.last_synced_at if refreshed else None,
    )


@router.post("/webhook", include_in_schema=False)
async def calendar_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    pool: asyncpg.Pool = Depends(get_pool),
) -> Response:
    """Google's Calendar push-notification callback. There is no user JWT
    on this request -- it's called directly by Google's servers -- so
    trust is established entirely from the channel/resource/token headers
    against what was stored when the channel was registered, never from
    the request body (which never contains the changed event itself; see
    /docs/architecture.md "Webhook security"). Always returns 200 once the
    headers have been read, including for an unrecognized/mismatched
    channel -- that avoids both leaking which channel ids are valid and
    inviting a Google retry storm over something that will never succeed."""
    channel_id = request.headers.get("X-Goog-Channel-Id")
    resource_id = request.headers.get("X-Goog-Resource-Id")
    resource_state = request.headers.get("X-Goog-Resource-State")
    channel_token = request.headers.get("X-Goog-Channel-Token")

    if not channel_id:
        return Response(status_code=200)

    connection = await conn_service.get_connection_by_watch_channel(pool, channel_id)
    if connection is None:
        return Response(status_code=200)

    token_matches = hmac.compare_digest(channel_token or "", connection.watch_token or "")
    if not token_matches or resource_id != connection.watch_resource_id:
        logger.warning("Rejected calendar webhook call: channel/token/resource mismatch for channel %s", channel_id)
        return Response(status_code=200)

    if resource_state == "sync":
        # Google's initial handshake right after the channel is
        # registered -- not a change notification, nothing to sync yet.
        return Response(status_code=200)

    background_tasks.add_task(sync_service.sync_connection_safe, pool, connection.id)
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_range(start: datetime, end: datetime) -> None:
    if end <= start:
        raise HTTPException(status_code=422, detail="`end` must be after `start`.")
    if end - start > _MAX_RANGE:
        raise HTTPException(status_code=422, detail=f"Range too large; request at most {_MAX_RANGE.days} days at a time.")


async def _get_record_or_404(
    pool: asyncpg.Pool, tenant: TenantMembership, user: AuthenticatedUser
) -> conn_service.ConnectionRecord:
    record = await conn_service.get_connection(pool, uuid.UUID(tenant.tenant_id), user.id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar not connected.")
    return record


async def _access_token_or_reauth(pool: asyncpg.Pool, record: conn_service.ConnectionRecord) -> str:
    try:
        return await conn_service.get_valid_access_token(pool, record)
    except conn_service.ReauthRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "REAUTH_REQUIRED", "message": str(exc)},
        ) from exc


def _map_google_error(exc: google_calendar.GoogleApiError) -> HTTPException:
    if exc.status_code == 429:
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Google Calendar rate limit reached. Please try again shortly.",
        )
    if exc.status_code in (401, 403):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "REAUTH_REQUIRED", "message": "Google Calendar access was denied."},
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY, detail="Google Calendar is temporarily unavailable."
    )


def _html_page(title: str, message: str, *, ok: bool = True) -> HTMLResponse:
    color = "#2563eb" if ok else "#c0392b"
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>{title}</title></head>"
        "<body style=\"font-family: system-ui, sans-serif; max-width: 480px; "
        "margin: 80px auto; text-align: center;\">"
        f"<h2 style=\"color: {color};\">{title}</h2>"
        f"<p>{message}</p>"
        "</body></html>"
    )
    return HTMLResponse(content=html)
