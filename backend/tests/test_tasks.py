"""Live integration tests for the task API.

Unlike test_auth.py (which mints a locally-signed token and never touches
the network), these tests sign in as two real, pre-existing demo Supabase
Auth users and exercise the full stack for real: JWT verification against
the project's actual JWKS, tenant-membership lookups and task CRUD against
the actual Supabase Postgres database configured via DATABASE_URL.

This whole module is skipped automatically (not failed) when the demo user
credentials aren't configured -- e.g. in CI, which has no project secrets.
See backend/.env.example and database/seeds/seed.sql for how to set up the
two demo users this module expects.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

settings = get_settings()

LIVE_CONFIGURED = bool(
    settings.supabase_url
    and settings.database_url
    and settings.supabase_jwks_url
    and settings.test_demo_user_a_email
    and settings.test_demo_user_a_password
    and settings.test_demo_user_b_email
    and settings.test_demo_user_b_password
)

pytestmark = pytest.mark.skipif(
    not LIVE_CONFIGURED,
    reason="Live Supabase project + demo user credentials not configured (see backend/.env.example)",
)


def _sign_in(email: str, password: str) -> str:
    resp = httpx.post(
        f"{settings.supabase_url}/auth/v1/token?grant_type=password",
        headers={
            "apikey": settings.supabase_service_role_key,
            "Content-Type": "application/json",
        },
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
    # `with` runs the app's lifespan (startup/shutdown), so the DB pool
    # opened by the first task request gets closed cleanly afterwards.
    with TestClient(app) as c:
        yield c


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def alice_task(client: TestClient, alice_token: str):
    """Creates a throwaway task as Alice and deletes it afterwards, even if
    the test itself already deleted it (204/404 on cleanup are both fine)."""
    resp = client.post(
        "/tasks",
        headers=_auth(alice_token),
        json={"title": "pytest: temp task"},
    )
    assert resp.status_code == 201, resp.text
    task = resp.json()
    yield task
    client.delete(f"/tasks/{task['id']}", headers=_auth(alice_token))


# ---------------------------------------------------------------------------
# Unauthenticated access
# ---------------------------------------------------------------------------


def test_list_tasks_requires_auth(client: TestClient):
    resp = client.get("/tasks")
    assert resp.status_code == 403  # no Authorization header at all


def test_create_task_requires_auth(client: TestClient):
    resp = client.post("/tasks", json={"title": "should be rejected"})
    assert resp.status_code == 403


def test_create_task_rejects_garbage_token(client: TestClient):
    resp = client.post(
        "/tasks",
        headers={"Authorization": "Bearer not-a-real-jwt"},
        json={"title": "should be rejected"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_create_task_rejects_blank_title(client: TestClient, alice_token: str):
    resp = client.post("/tasks", headers=_auth(alice_token), json={"title": "   "})
    assert resp.status_code == 422


def test_create_task_rejects_missing_title(client: TestClient, alice_token: str):
    resp = client.post("/tasks", headers=_auth(alice_token), json={})
    assert resp.status_code == 422


def test_create_task_rejects_negative_estimate(client: TestClient, alice_token: str):
    resp = client.post(
        "/tasks",
        headers=_auth(alice_token),
        json={"title": "Write report", "estimated_minutes": -10},
    )
    assert resp.status_code == 422


def test_get_task_rejects_malformed_id(client: TestClient, alice_token: str):
    resp = client.get("/tasks/not-a-uuid", headers=_auth(alice_token))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Create / read
# ---------------------------------------------------------------------------


def test_create_task_returns_full_task(client: TestClient, alice_token: str, alice_task: dict):
    assert alice_task["title"] == "pytest: temp task"
    assert alice_task["status"] == "pending"
    assert alice_task["description"] is None
    assert alice_task["created_by"]
    assert alice_task["tenant_id"]
    assert alice_task["created_at"]
    assert alice_task["updated_at"]


def test_get_task_by_id(client: TestClient, alice_token: str, alice_task: dict):
    resp = client.get(f"/tasks/{alice_task['id']}", headers=_auth(alice_token))
    assert resp.status_code == 200
    assert resp.json()["id"] == alice_task["id"]


def test_get_nonexistent_task_is_404(client: TestClient, alice_token: str):
    resp = client.get(
        "/tasks/00000000-0000-0000-0000-000000000000", headers=_auth(alice_token)
    )
    assert resp.status_code == 404


def test_list_tasks_includes_created_task(client: TestClient, alice_token: str, alice_task: dict):
    resp = client.get("/tasks", headers=_auth(alice_token))
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert alice_task["id"] in ids


def test_list_tasks_filters_by_status(client: TestClient, alice_token: str, alice_task: dict):
    resp = client.get("/tasks", headers=_auth(alice_token), params={"status": "done"})
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert alice_task["id"] not in ids  # freshly created task is 'pending', not 'done'

    resp_pending = client.get("/tasks", headers=_auth(alice_token), params={"status": "pending"})
    assert alice_task["id"] in [t["id"] for t in resp_pending.json()]


# ---------------------------------------------------------------------------
# Update / complete / delete
# ---------------------------------------------------------------------------


def test_update_task_title(client: TestClient, alice_token: str, alice_task: dict):
    resp = client.patch(
        f"/tasks/{alice_task['id']}", headers=_auth(alice_token), json={"title": "renamed"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "renamed"
    assert body["updated_at"] != alice_task["updated_at"]


def test_update_task_can_clear_due_at(client: TestClient, alice_token: str, alice_task: dict):
    set_resp = client.patch(
        f"/tasks/{alice_task['id']}",
        headers=_auth(alice_token),
        json={"due_at": "2030-01-01T00:00:00Z"},
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["due_at"] is not None

    clear_resp = client.patch(
        f"/tasks/{alice_task['id']}", headers=_auth(alice_token), json={"due_at": None}
    )
    assert clear_resp.status_code == 200
    assert clear_resp.json()["due_at"] is None


def test_update_nonexistent_task_is_404(client: TestClient, alice_token: str):
    resp = client.patch(
        "/tasks/00000000-0000-0000-0000-000000000000",
        headers=_auth(alice_token),
        json={"title": "x"},
    )
    assert resp.status_code == 404


def test_complete_task(client: TestClient, alice_token: str, alice_task: dict):
    resp = client.post(f"/tasks/{alice_task['id']}/complete", headers=_auth(alice_token))
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"


def test_delete_task(client: TestClient, alice_token: str):
    create_resp = client.post(
        "/tasks", headers=_auth(alice_token), json={"title": "pytest: to be deleted"}
    )
    task_id = create_resp.json()["id"]

    delete_resp = client.delete(f"/tasks/{task_id}", headers=_auth(alice_token))
    assert delete_resp.status_code == 204

    get_resp = client.get(f"/tasks/{task_id}", headers=_auth(alice_token))
    assert get_resp.status_code == 404


def test_delete_nonexistent_task_is_404(client: TestClient, alice_token: str):
    resp = client.delete(
        "/tasks/00000000-0000-0000-0000-000000000000", headers=_auth(alice_token)
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Cross-tenant access (Bob must never see/touch Alice's task)
# ---------------------------------------------------------------------------


def test_bob_cannot_read_alices_task(client: TestClient, bob_token: str, alice_task: dict):
    resp = client.get(f"/tasks/{alice_task['id']}", headers=_auth(bob_token))
    assert resp.status_code == 404  # not 403 -- existence isn't leaked either


def test_bob_cannot_update_alices_task(client: TestClient, bob_token: str, alice_task: dict):
    resp = client.patch(
        f"/tasks/{alice_task['id']}", headers=_auth(bob_token), json={"title": "hijacked"}
    )
    assert resp.status_code == 404


def test_bob_cannot_delete_alices_task(client: TestClient, bob_token: str, alice_task: dict):
    resp = client.delete(f"/tasks/{alice_task['id']}", headers=_auth(bob_token))
    assert resp.status_code == 404


def test_bob_cannot_complete_alices_task(client: TestClient, bob_token: str, alice_task: dict):
    resp = client.post(f"/tasks/{alice_task['id']}/complete", headers=_auth(bob_token))
    assert resp.status_code == 404


def test_bobs_task_list_excludes_alices_task(
    client: TestClient, bob_token: str, alice_task: dict
):
    resp = client.get("/tasks", headers=_auth(bob_token))
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert alice_task["id"] not in ids
