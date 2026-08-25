"""A tiny asyncpg connection pool for reads the backend needs to do itself
(e.g. verifying tenant membership before trusting a client-supplied
tenant id). Business-logic tables are otherwise written to via this same
Postgres connection, using the backend's own DB role -- not the Supabase
client SDK -- so no extra dependency is needed for this phase."""

from __future__ import annotations

import asyncpg

from app.core.config import get_settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=5,
            statement_cache_size=0,  # required for Supabase's pgbouncer transaction pooler
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
