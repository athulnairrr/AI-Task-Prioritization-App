"""Two-way Google Calendar synchronization: watch-channel lifecycle and
incremental (syncToken-based) sync.

This module is the only thing that decides "is this event ours, was it
moved, was it deleted, is this a duplicate/echo notification". Nothing
here ever writes back to Google -- a detected external change only ever
updates our own database (`schedule_items`, `google_calendar_event_mappings`,
`google_calendar_external_events`). That one-directional rule is itself
most of this phase's loop prevention: the cycle described in the brief
("our app changes event -> webhook -> backend changes event -> webhook ->
...") requires a backend write-back step that simply doesn't exist here.
What remains is making sure a *duplicate* or *replayed* Google
notification, or two syncs racing each other, is still processed
idempotently -- see `_apply_event_change` and the advisory lock in
`sync_connection`.

See /docs/architecture.md "Two-way Calendar synchronization" for the full
design writeup.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import asyncpg

from app.core.config import get_settings
from app.services import calendar_connections as conn_service
from app.services import google_calendar
from app.services import schedule_apply

logger = logging.getLogger(__name__)


@dataclass
class SyncSummary:
    synced: bool
    reason: str | None = None
    full_resync: bool = False
    processed: int = 0
    counts: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Watch channel lifecycle
# ---------------------------------------------------------------------------


async def ensure_watch_channel(pool: asyncpg.Pool, connection: conn_service.ConnectionRecord) -> None:
    """Registers a fresh watch channel if none is active or the current
    one is close to expiring; otherwise a no-op. Always best-effort --
    push notifications are a nice-to-have on top of the POST /calendar/sync
    reconciliation fallback, never something worth failing a request over.
    Requires `settings.google_calendar_webhook_url` (a real public HTTPS
    endpoint); silently does nothing without one, which is the expected
    state in local development."""
    settings = get_settings()
    if not settings.google_calendar_webhook_url:
        return

    now = datetime.now(timezone.utc)
    renew_within = timedelta(hours=settings.calendar_watch_renew_within_hours)
    if connection.watch_expires_at and connection.watch_expires_at - renew_within > now:
        return  # still comfortably valid

    try:
        access_token = await conn_service.get_valid_access_token(pool, connection)
    except conn_service.ReauthRequiredError:
        return  # no usable token; the next opportunity (or /calendar/sync) will retry

    old_channel_id, old_resource_id = connection.watch_channel_id, connection.watch_resource_id
    channel_id = uuid.uuid4().hex
    channel_token = secrets.token_urlsafe(24)
    expiration_ms = int((now + timedelta(days=settings.calendar_watch_ttl_days)).timestamp() * 1000)

    try:
        resp = await google_calendar.watch_events(
            access_token=access_token,
            calendar_id=connection.calendar_id,
            channel_id=channel_id,
            webhook_url=settings.google_calendar_webhook_url,
            channel_token=channel_token,
            expiration_ms=expiration_ms,
        )
    except google_calendar.GoogleApiError as exc:
        logger.warning("Watch channel registration failed for connection %s: %s", connection.id, exc)
        return

    resource_id = resp.get("resourceId")
    if not resource_id:
        return

    expiration_raw = resp.get("expiration")
    expires_at = (
        datetime.fromtimestamp(int(expiration_raw) / 1000, tz=timezone.utc)
        if expiration_raw
        else now + timedelta(days=settings.calendar_watch_ttl_days)
    )

    await conn_service.save_watch_channel(
        pool, connection.id, channel_id=channel_id, resource_id=resource_id, token=channel_token, expires_at=expires_at
    )

    if old_channel_id and old_resource_id:
        try:
            await google_calendar.stop_channel(
                access_token=access_token, channel_id=old_channel_id, resource_id=old_resource_id
            )
        except google_calendar.GoogleApiError:
            pass  # best-effort; an unstoppable old channel just expires on its own


# ---------------------------------------------------------------------------
# Incremental / full sync
# ---------------------------------------------------------------------------


async def sync_connection(pool: asyncpg.Pool, connection_id: uuid.UUID) -> SyncSummary:
    """Entry point for both the webhook-triggered background sync and the
    explicit POST /calendar/sync reconciliation call. Serializes concurrent
    syncs of the *same* connection via a Postgres session-level advisory
    lock held on one dedicated connection for the lock's lifetime -- a
    second caller that loses the race returns immediately with
    `synced=False` rather than racing the syncToken (Google's sync tokens
    are not safe to use concurrently from two callers)."""
    async with pool.acquire() as lock_conn:
        got_lock = await lock_conn.fetchval(
            "select pg_try_advisory_lock(hashtext('calendar_sync'), hashtext($1))", str(connection_id)
        )
        if not got_lock:
            return SyncSummary(synced=False, reason="A sync for this connection is already in progress.")
        try:
            return await _sync_connection_locked(pool, connection_id)
        finally:
            await lock_conn.execute(
                "select pg_advisory_unlock(hashtext('calendar_sync'), hashtext($1))", str(connection_id)
            )


async def sync_connection_safe(pool: asyncpg.Pool, connection_id: uuid.UUID) -> None:
    """Wraps `sync_connection` for use as a FastAPI BackgroundTask (the
    webhook handler's response has already been sent by the time this
    runs -- there's no request to report an error back on, so this only
    logs)."""
    try:
        result = await sync_connection(pool, connection_id)
        if not result.synced and result.reason:
            logger.info("Webhook-triggered sync for connection %s: %s", connection_id, result.reason)
    except Exception:
        logger.exception("Webhook-triggered sync failed for connection %s", connection_id)


async def _sync_connection_locked(pool: asyncpg.Pool, connection_id: uuid.UUID) -> SyncSummary:
    settings = get_settings()
    connection = await conn_service.get_connection_by_id(pool, connection_id)
    if connection is None:
        return SyncSummary(synced=False, reason="Connection no longer exists.")

    try:
        access_token = await conn_service.get_valid_access_token(pool, connection)
    except conn_service.ReauthRequiredError:
        return SyncSummary(synced=False, reason="Reauthorization required.")

    sync_token = connection.sync_token
    full_resync = False
    items: list[dict] = []
    next_sync_token: str | None = None

    for attempt in range(2):  # one retry, only for a 410-triggered full resync
        items = []
        next_sync_token = None
        page_token: str | None = None
        try:
            while True:
                if sync_token:
                    page = await google_calendar.list_events_page(
                        access_token=access_token,
                        calendar_id=connection.calendar_id,
                        sync_token=sync_token,
                        page_token=page_token,
                    )
                else:
                    now = datetime.now(timezone.utc)
                    page = await google_calendar.list_events_page(
                        access_token=access_token,
                        calendar_id=connection.calendar_id,
                        page_token=page_token,
                        time_min=now - timedelta(days=settings.calendar_sync_window_days_past),
                        time_max=now + timedelta(days=settings.calendar_sync_window_days_future),
                    )
                items.extend(page.get("items", []))
                if "nextSyncToken" in page:
                    next_sync_token = page["nextSyncToken"]
                page_token = page.get("nextPageToken")
                if not page_token:
                    break
        except google_calendar.GoogleApiError as exc:
            if exc.status_code == 410 and sync_token:
                # Google's documented recovery: discard the token and
                # perform a full (bounded-window) resync from scratch.
                sync_token = None
                full_resync = True
                await conn_service.update_sync_state(pool, connection.id, None)
                continue
            raise
        break
    else:
        return SyncSummary(synced=False, reason="Sync token repeatedly invalid.")

    counts: Counter[str] = Counter()
    for item in items:
        kind = await _apply_event_change(pool, connection, item)
        counts[kind] += 1

    await conn_service.update_sync_state(pool, connection.id, next_sync_token)

    return SyncSummary(synced=True, full_resync=full_resync, processed=len(items), counts=dict(counts))


# ---------------------------------------------------------------------------
# Per-event change application
# ---------------------------------------------------------------------------


async def _apply_event_change(
    pool: asyncpg.Pool, connection: conn_service.ConnectionRecord, item: dict
) -> str:
    """Applies one raw Google event/tombstone from a sync page. Returns a
    short `kind` string used only for the summary counts returned to
    callers/tests. Every branch is idempotent: replaying the exact same
    item (duplicate webhook, overlapping sync pages, a re-run reconciliation)
    must always be safe to call again."""
    google_event_id = item["id"]
    status = item.get("status", "confirmed")
    tenant_id = connection.tenant_id

    mapping = await pool.fetchrow(
        """
        select m.id as mapping_id, m.schedule_item_id, m.sync_status, m.google_updated_at
        from public.google_calendar_event_mappings m
        where m.connection_id = $1 and m.google_event_id = $2
        """,
        connection.id,
        google_event_id,
    )

    if status == "cancelled":
        return await _apply_cancellation(pool, connection, google_event_id, mapping)

    updated_at = _parse_google_timestamp(item.get("updated"))

    if mapping is not None:
        return await _apply_app_event_update(pool, mapping, item, updated_at)

    adopted = await _try_adopt_untracked_event(pool, tenant_id, connection, google_event_id, item, updated_at)
    if adopted:
        return "app_adopted"

    return await _upsert_external_event(pool, tenant_id, connection, google_event_id, item)


async def _apply_cancellation(
    pool: asyncpg.Pool,
    connection: conn_service.ConnectionRecord,
    google_event_id: str,
    mapping: asyncpg.Record | None,
) -> str:
    if mapping is None:
        await pool.execute(
            "delete from public.google_calendar_external_events where connection_id = $1 and google_event_id = $2",
            connection.id,
            google_event_id,
        )
        return "external_deleted"

    if mapping["sync_status"] == "deleted":
        return "app_delete_noop"  # already processed -- duplicate/replayed notification

    await pool.execute(
        """
        update public.google_calendar_event_mappings
        set sync_status = 'deleted', last_error = $2, last_synced_at = now()
        where id = $1
        """,
        mapping["mapping_id"],
        "The Google Calendar event for this schedule item was deleted.",
    )
    await pool.execute(
        """
        update public.schedule_items
        set needs_attention = true,
            attention_reason = 'The Google Calendar event for this task was deleted externally. Re-apply the schedule to recreate it.'
        where id = $1
        """,
        mapping["schedule_item_id"],
    )
    return "app_deleted"


async def _apply_app_event_update(
    pool: asyncpg.Pool, mapping: asyncpg.Record, item: dict, updated_at: datetime | None
) -> str:
    stored_updated_at = mapping["google_updated_at"]
    if stored_updated_at is not None and updated_at is not None and updated_at <= stored_updated_at:
        # Not a newer version than what we already recorded -- either an
        # echo of our own last write or a duplicate/replayed notification.
        # See module docstring "Loop prevention".
        return "app_noop"

    start, _ = _parse_event_time(item["start"])
    end, _ = _parse_event_time(item["end"])
    await pool.execute(
        "update public.schedule_items set starts_at = $1, ends_at = $2, needs_attention = false, attention_reason = null where id = $3",
        start,
        end,
        mapping["schedule_item_id"],
    )
    await pool.execute(
        """
        update public.google_calendar_event_mappings
        set sync_status = 'synced', google_updated_at = $2, last_synced_at = now(), last_error = null
        where id = $1
        """,
        mapping["mapping_id"],
        updated_at,
    )
    return "app_moved"


async def _try_adopt_untracked_event(
    pool: asyncpg.Pool,
    tenant_id: uuid.UUID,
    connection: conn_service.ConnectionRecord,
    google_event_id: str,
    item: dict,
    updated_at: datetime | None,
) -> bool:
    """Self-heals the rare case where this app's own `create_event` call
    succeeded at Google but the follow-up mapping insert never happened
    (e.g. a crash between the two) -- recognized by our own
    extendedProperties tag pointing at a real schedule_item that still has
    no mapping, rather than treated as an unrelated external event."""
    private = (item.get("extendedProperties") or {}).get("private") or {}
    if private.get("app") != schedule_apply.APP_IDENTIFIER:
        return False
    raw_schedule_item_id = private.get("schedule_item_id")
    if not raw_schedule_item_id:
        return False
    try:
        schedule_item_id = uuid.UUID(raw_schedule_item_id)
    except ValueError:
        return False

    owns_it = await pool.fetchval(
        "select 1 from public.schedule_items where id = $1 and tenant_id = $2", schedule_item_id, tenant_id
    )
    if not owns_it:
        return False

    await pool.execute(
        """
        insert into public.google_calendar_event_mappings
            (tenant_id, schedule_item_id, connection_id, google_event_id, sync_status, google_updated_at, last_synced_at)
        values ($1, $2, $3, $4, 'synced', $5, now())
        on conflict (schedule_item_id) do update set
            connection_id = excluded.connection_id,
            google_event_id = excluded.google_event_id,
            sync_status = 'synced',
            google_updated_at = excluded.google_updated_at,
            last_synced_at = now()
        """,
        tenant_id,
        schedule_item_id,
        connection.id,
        google_event_id,
        updated_at,
    )
    return True


async def _upsert_external_event(
    pool: asyncpg.Pool,
    tenant_id: uuid.UUID,
    connection: conn_service.ConnectionRecord,
    google_event_id: str,
    item: dict,
) -> str:
    if not item.get("start") or not item.get("end"):
        return "external_skipped"  # a bare cancelled-instance stub or similar; nothing to place
    start, all_day = _parse_event_time(item["start"])
    end, _ = _parse_event_time(item["end"])
    await pool.execute(
        """
        insert into public.google_calendar_external_events
            (tenant_id, connection_id, google_event_id, title, starts_at, ends_at, all_day, status, updated_at)
        values ($1, $2, $3, $4, $5, $6, $7, $8, now())
        on conflict (connection_id, google_event_id) do update set
            title = excluded.title,
            starts_at = excluded.starts_at,
            ends_at = excluded.ends_at,
            all_day = excluded.all_day,
            status = excluded.status,
            updated_at = now()
        """,
        tenant_id,
        connection.id,
        google_event_id,
        item.get("summary"),
        start,
        end,
        all_day,
        item.get("status", "confirmed"),
    )
    return "external_upserted"


def _parse_event_time(obj: dict) -> tuple[datetime, bool]:
    if "dateTime" in obj:
        return datetime.fromisoformat(obj["dateTime"]), False
    date_value = datetime.fromisoformat(obj["date"]).replace(tzinfo=timezone.utc)
    return date_value, True


def _parse_google_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
