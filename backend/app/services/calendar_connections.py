"""Persistence + token lifecycle for `public.google_calendar_connections`.

This is the only module that decrypts a Google token or decides whether to
refresh one. Route handlers never touch `access_token`/`refresh_token`
directly -- they call `get_valid_access_token()` and get back a plaintext
access token to use for exactly one outgoing Google API call, never stored
or logged by the caller.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg

from app.core.crypto import decrypt_token, encrypt_token
from app.schemas.calendar import CalendarConnectionOut, CalendarConnectionStatus
from app.services import google_calendar

DEFAULT_CALENDAR_ID = "primary"

# Refresh a bit before actual expiry so a request never races an
# almost-expired token.
_EXPIRY_SAFETY_MARGIN = timedelta(seconds=60)

_COLUMNS = (
    "id, tenant_id, user_id, google_account_email, calendar_id, "
    "access_token, refresh_token, token_expires_at, connected_at, "
    "updated_at, status, last_error, calendar_timezone, granted_scopes, "
    "sync_token, last_synced_at, watch_channel_id, watch_resource_id, "
    "watch_token, watch_expires_at"
)


class ReauthRequiredError(Exception):
    """Raised when Google rejects the refresh token (revoked/expired) --
    callers should surface this as a clear REAUTH_REQUIRED state, not a
    generic 500."""


class NotConnectedError(Exception):
    """Raised when there is no connection row for this tenant/user at all."""


@dataclass
class ConnectionRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    google_account_email: str
    calendar_id: str
    token_expires_at: datetime | None
    connected_at: datetime
    status: str
    last_error: str | None
    calendar_timezone: str | None
    granted_scopes: str
    sync_token: str | None
    last_synced_at: datetime | None
    watch_channel_id: str | None
    watch_resource_id: str | None
    watch_token: str | None
    watch_expires_at: datetime | None
    _access_token_enc: str | None
    _refresh_token_enc: str

    @property
    def has_write_scope(self) -> bool:
        return google_calendar.CALENDAR_EVENTS_WRITE_SCOPE in self.granted_scopes.split()

    @property
    def timezone_or_utc(self) -> str:
        return self.calendar_timezone or "UTC"

    @property
    def watch_active(self) -> bool:
        return bool(
            self.watch_channel_id and self.watch_expires_at and self.watch_expires_at > datetime.now(timezone.utc)
        )


def _row_to_record(row: asyncpg.Record) -> ConnectionRecord:
    return ConnectionRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        google_account_email=row["google_account_email"],
        calendar_id=row["calendar_id"],
        token_expires_at=row["token_expires_at"],
        connected_at=row["connected_at"],
        status=row["status"],
        last_error=row["last_error"],
        calendar_timezone=row["calendar_timezone"],
        granted_scopes=row["granted_scopes"],
        sync_token=row["sync_token"],
        last_synced_at=row["last_synced_at"],
        watch_channel_id=row["watch_channel_id"],
        watch_resource_id=row["watch_resource_id"],
        watch_token=row["watch_token"],
        watch_expires_at=row["watch_expires_at"],
        _access_token_enc=row["access_token"],
        _refresh_token_enc=row["refresh_token"],
    )


def to_connection_out(record: ConnectionRecord | None) -> CalendarConnectionOut:
    if record is None:
        return CalendarConnectionOut(status=CalendarConnectionStatus.not_connected)
    return CalendarConnectionOut(
        status=CalendarConnectionStatus(record.status),
        google_account_email=record.google_account_email,
        calendar_id=record.calendar_id,
        connected_at=record.connected_at,
        last_error=record.last_error,
        calendar_timezone=record.calendar_timezone,
        has_write_access=record.has_write_scope,
        last_synced_at=record.last_synced_at,
        watch_active=record.watch_active,
    )


async def get_connection(
    pool: asyncpg.Pool, tenant_id: uuid.UUID, user_id: str, calendar_id: str = DEFAULT_CALENDAR_ID
) -> ConnectionRecord | None:
    row = await pool.fetchrow(
        f"""
        select {_COLUMNS} from public.google_calendar_connections
        where tenant_id = $1 and user_id = $2 and calendar_id = $3
        """,
        tenant_id,
        user_id,
        calendar_id,
    )
    return _row_to_record(row) if row else None


async def get_connection_by_id(pool: asyncpg.Pool, connection_id: uuid.UUID) -> ConnectionRecord | None:
    row = await pool.fetchrow(
        f"select {_COLUMNS} from public.google_calendar_connections where id = $1", connection_id
    )
    return _row_to_record(row) if row else None


async def get_connection_by_watch_channel(pool: asyncpg.Pool, channel_id: str) -> ConnectionRecord | None:
    """Looks up a connection by its active watch channel id -- the only
    identifier a Google webhook notification carries (see
    app/api/calendar.py's webhook route). Returns None for an unknown or
    stale channel id (e.g. one that has since been renewed away from);
    callers should treat that as "nothing to do", not an error."""
    row = await pool.fetchrow(
        f"select {_COLUMNS} from public.google_calendar_connections where watch_channel_id = $1", channel_id
    )
    return _row_to_record(row) if row else None


async def save_watch_channel(
    pool: asyncpg.Pool,
    connection_id: uuid.UUID,
    *,
    channel_id: str,
    resource_id: str,
    token: str,
    expires_at: datetime,
) -> None:
    await pool.execute(
        """
        update public.google_calendar_connections
        set watch_channel_id = $2, watch_resource_id = $3, watch_token = $4, watch_expires_at = $5
        where id = $1
        """,
        connection_id,
        channel_id,
        resource_id,
        token,
        expires_at,
    )


async def clear_watch_channel(pool: asyncpg.Pool, connection_id: uuid.UUID) -> None:
    await pool.execute(
        """
        update public.google_calendar_connections
        set watch_channel_id = null, watch_resource_id = null, watch_token = null, watch_expires_at = null
        where id = $1
        """,
        connection_id,
    )


async def update_sync_state(pool: asyncpg.Pool, connection_id: uuid.UUID, sync_token: str | None) -> None:
    """`sync_token=None` is a valid, meaningful value here (not "leave
    unchanged") -- it's how a 410-triggered full resync clears the old
    token before establishing a new one, and how a full resync's own
    result gets stored afterward. `last_synced_at` always advances to
    "now" on a completed pass, even one that ends up with no token yet."""
    await pool.execute(
        """
        update public.google_calendar_connections
        set sync_token = $2, last_synced_at = now()
        where id = $1
        """,
        connection_id,
        sync_token,
    )


async def upsert_connection(
    pool: asyncpg.Pool,
    *,
    tenant_id: uuid.UUID,
    user_id: str,
    google_account_email: str,
    calendar_id: str,
    access_token: str | None,
    refresh_token: str,
    token_expires_at: datetime | None,
    granted_scopes: str = "",
    calendar_timezone: str | None = None,
) -> ConnectionRecord:
    """Encrypts tokens and inserts/updates the connection row (a reconnect
    after disconnect, or re-consent -- including an incremental-auth
    upgrade to add the write scope -- upserts rather than duplicating).

    `granted_scopes` should be exactly what Google's token response
    reported (its `scope` field), not merely what was requested -- Google
    is the source of truth for what was actually granted. Passing an empty
    string here (the default) leaves an existing value alone via
    `coalesce`, since some callers only refresh tokens, not scope/timezone.
    """
    access_token_enc = encrypt_token(access_token) if access_token else None
    refresh_token_enc = encrypt_token(refresh_token)

    row = await pool.fetchrow(
        f"""
        insert into public.google_calendar_connections
            (tenant_id, user_id, google_account_email, calendar_id, access_token,
             refresh_token, token_expires_at, status, last_error, granted_scopes, calendar_timezone)
        values ($1, $2, $3, $4, $5, $6, $7, 'connected', null, $8, $9)
        on conflict (tenant_id, user_id, calendar_id) do update set
            google_account_email = excluded.google_account_email,
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            token_expires_at = excluded.token_expires_at,
            status = 'connected',
            last_error = null,
            granted_scopes = case when excluded.granted_scopes = '' then public.google_calendar_connections.granted_scopes else excluded.granted_scopes end,
            calendar_timezone = coalesce(excluded.calendar_timezone, public.google_calendar_connections.calendar_timezone)
        returning {_COLUMNS}
        """,
        tenant_id,
        user_id,
        google_account_email,
        calendar_id,
        access_token_enc,
        refresh_token_enc,
        token_expires_at,
        granted_scopes,
        calendar_timezone,
    )
    return _row_to_record(row)


async def delete_connection(
    pool: asyncpg.Pool, tenant_id: uuid.UUID, user_id: str, calendar_id: str = DEFAULT_CALENDAR_ID
) -> ConnectionRecord | None:
    """Deletes the local row after a best-effort revoke at Google (the
    caller is responsible for calling google_calendar.revoke_token first --
    this function just needs the still-present row to know what to revoke,
    so routes fetch the record, revoke, then call this)."""
    row = await pool.fetchrow(
        f"""
        delete from public.google_calendar_connections
        where tenant_id = $1 and user_id = $2 and calendar_id = $3
        returning {_COLUMNS}
        """,
        tenant_id,
        user_id,
        calendar_id,
    )
    return _row_to_record(row) if row else None


async def revoke_and_delete(pool: asyncpg.Pool, record: ConnectionRecord) -> None:
    """Best-effort revoke at Google, then always delete the local row --
    even if Google's revoke call fails (network issue, already-revoked
    token, Google API outage), the user must still be able to disconnect
    locally and reconnect cleanly afterward."""
    try:
        refresh_token = decrypt_token(record._refresh_token_enc)
        await google_calendar.revoke_token(token=refresh_token)
    except Exception:
        pass  # local deletion still proceeds regardless
    await delete_connection(pool, record.tenant_id, record.user_id, record.calendar_id)


async def _mark_reauth_required(pool: asyncpg.Pool, record: ConnectionRecord, message: str) -> None:
    await pool.execute(
        """
        update public.google_calendar_connections
        set status = 'reauth_required', last_error = $3, access_token = null, token_expires_at = null
        where tenant_id = $1 and user_id = $2 and calendar_id = $4
        """,
        record.tenant_id,
        record.user_id,
        message,
        record.calendar_id,
    )


async def _mark_error(pool: asyncpg.Pool, record: ConnectionRecord, message: str) -> None:
    await pool.execute(
        """
        update public.google_calendar_connections
        set status = 'error', last_error = $3
        where tenant_id = $1 and user_id = $2 and calendar_id = $4
        """,
        record.tenant_id,
        record.user_id,
        message,
        record.calendar_id,
    )


async def get_valid_access_token(pool: asyncpg.Pool, record: ConnectionRecord) -> str:
    """Returns a usable plaintext access token, refreshing via Google if the
    cached one is missing/expired. Raises ReauthRequiredError if Google
    rejects the refresh token (revoked/expired) -- callers must not treat
    that as a generic 500."""
    now = datetime.now(timezone.utc)
    if record._access_token_enc and record.token_expires_at and record.token_expires_at - _EXPIRY_SAFETY_MARGIN > now:
        return decrypt_token(record._access_token_enc)

    refresh_token = decrypt_token(record._refresh_token_enc)
    try:
        token_data = await google_calendar.refresh_access_token(refresh_token=refresh_token)
    except google_calendar.GoogleApiError as exc:
        if exc.error_code == "invalid_grant":
            await _mark_reauth_required(pool, record, "Google rejected the refresh token (revoked or expired).")
            raise ReauthRequiredError(str(exc)) from exc
        await _mark_error(pool, record, f"Token refresh failed: {exc}")
        raise

    new_access_token = token_data["access_token"]
    expires_in = token_data.get("expires_in", 3600)
    new_expires_at = now + timedelta(seconds=expires_in)

    await pool.execute(
        """
        update public.google_calendar_connections
        set access_token = $3, token_expires_at = $4, status = 'connected', last_error = null
        where tenant_id = $1 and user_id = $2 and calendar_id = $5
        """,
        record.tenant_id,
        record.user_id,
        encrypt_token(new_access_token),
        new_expires_at,
        record.calendar_id,
    )
    return new_access_token
