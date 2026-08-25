from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # CORS
    cors_allow_origins: str = "http://localhost:3000"

    # Supabase / Postgres
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_jwks_url: str = ""
    database_url: str = ""

    # Third-party integrations
    gemini_api_key: str = ""
    # Model name lives in config, not hardcoded, so it can be swapped (e.g. if
    # Google renames/deprecates it) without touching application code.
    gemini_model: str = "gemini-3.1-flash-lite"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    # Must exactly match a redirect URI registered on the Google Cloud OAuth
    # client (Web application type) -- see /docs/architecture.md "Google
    # Calendar integration" for exact Cloud Console setup steps.
    google_oauth_redirect_uri: str = "http://localhost:8000/calendar/callback"

    # Signs the OAuth `state` parameter (HS256) -- CSRF protection for the
    # connect -> Google -> callback round trip. Distinct from
    # token_encryption_key below (different purpose, different blast radius
    # if either leaks). Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    oauth_state_secret: str = ""

    # Fernet key used to encrypt Google refresh/access tokens before they're
    # stored in the database (see app/core/crypto.py). Generate with:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str = ""

    # Optional: credentials for two pre-existing demo/test Supabase Auth users,
    # used only by the live integration tests in tests/test_tasks.py (skipped
    # automatically if unset). Never real user accounts -- see database/seeds/seed.sql.
    test_demo_user_a_email: str = ""
    test_demo_user_a_password: str = ""
    test_demo_user_b_email: str = ""
    test_demo_user_b_password: str = ""

    # Optional: a real Google refresh token already granted calendar.events
    # (write) access, used only by tests/test_schedule_apply_live_google.py
    # to create and then delete exactly one real Calendar event. Obtaining
    # this requires a human completing Google's consent screen once (not
    # scriptable) -- see that test file and /docs/progress.md for how to
    # get one. Leave unset to skip that single test; everything else in
    # this phase is verified without it.
    test_live_calendar_refresh_token: str = ""
    test_live_calendar_id: str = "primary"

    # Phase 7: Google Calendar push notifications (watch channels). Must be
    # a real public HTTPS URL Google can reach (e.g.
    # "https://api.example.com/calendar/webhook") -- Google refuses
    # http:// and unreachable/localhost addresses outright. Left empty in
    # local development (no public URL available): watch-channel
    # registration is then skipped entirely and the app falls back to the
    # explicit POST /calendar/sync reconciliation endpoint. See
    # /docs/architecture.md "Two-way Calendar synchronization" and
    # /docs/progress.md known issues.
    google_calendar_webhook_url: str = ""
    # How long a registered watch channel is requested to live before it
    # must be renewed (Google channels cannot be renewed in place -- a new
    # one is registered and the old one stopped). Kept comfortably inside
    # Google's own maximum for the events.watch resource.
    calendar_watch_ttl_days: int = 7
    # Renew a channel once it is within this long of expiring, checked
    # opportunistically (no cron -- see ensure_watch_channel()).
    calendar_watch_renew_within_hours: int = 24
    # Bounds for a *full* sync (no syncToken yet, or one just invalidated
    # by a 410) -- deliberately narrow to stay well within Google's free
    # quota rather than paging through someone's entire calendar history.
    # An incremental sync (has a syncToken) is unbounded by these; Google
    # scopes it to whatever window established the token.
    calendar_sync_window_days_past: int = 7
    calendar_sync_window_days_future: int = 90

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
