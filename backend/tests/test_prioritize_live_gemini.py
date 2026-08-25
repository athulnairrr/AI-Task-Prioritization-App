"""One optional, real end-to-end smoke test: real Supabase auth, a real
task, and a real call to the Gemini API (no dependency override). Skipped
unless GEMINI_API_KEY (in addition to the usual live Supabase config) is
set -- this is the only test file in the suite that spends real (free-tier)
Gemini quota, and it does so exactly once.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

settings = get_settings()

LIVE_GEMINI_CONFIGURED = bool(
    settings.gemini_api_key
    and settings.supabase_url
    and settings.database_url
    and settings.supabase_jwks_url
    and settings.test_demo_user_a_email
    and settings.test_demo_user_a_password
)

pytestmark = pytest.mark.skipif(
    not LIVE_GEMINI_CONFIGURED,
    reason="GEMINI_API_KEY + live Supabase config not set -- skipping the real Gemini smoke test",
)


def test_prioritize_against_the_real_gemini_api():
    token_resp = httpx.post(
        f"{settings.supabase_url}/auth/v1/token?grant_type=password",
        headers={"apikey": settings.supabase_service_role_key, "Content-Type": "application/json"},
        json={
            "email": settings.test_demo_user_a_email,
            "password": settings.test_demo_user_a_password,
        },
        timeout=20,
    )
    token_resp.raise_for_status()
    token = token_resp.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        create_resp = client.post(
            "/tasks",
            headers=auth,
            json={
                "title": "Finish the client proposal",
                "raw_input": "I need to finish the client proposal by Friday. "
                "It will take about 2 hours and is very important.",
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        task = create_resp.json()

        try:
            resp = client.post(f"/tasks/{task['id']}/prioritize", headers=auth)
            assert resp.status_code == 200, resp.text
            body = resp.json()

            assert body["category"] in {
                "work",
                "personal",
                "health",
                "finance",
                "learning",
                "household",
                "other",
            }
            assert body["urgency"] in {"low", "medium", "high"}
            assert body["importance"] in {"low", "medium", "high"}
            assert 0.0 <= body["priority_score"] <= 100.0
            assert 0.0 <= body["confidence_score"] <= 1.0
            assert 5 <= body["effort_estimate_minutes"] <= 8 * 60
            assert body["reasoning"]
            assert body["model"] == settings.gemini_model

            # The example from the product brief -- a stated "2 hours" should
            # be picked up reasonably close to 120 minutes, not ignored.
            assert 90 <= body["effort_estimate_minutes"] <= 180
        finally:
            client.delete(f"/tasks/{task['id']}", headers=auth)
