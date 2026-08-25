"""Offline, hermetic tests for JWT verification and the auth-derived routes.

These do not hit the network or a real Supabase project -- they mint a
locally-signed ES256 token (matching Supabase's real token shape) and patch
the JWKS client so `decode_supabase_jwt` verifies against our test key
instead. This keeps CI fast and independent of any live project, while
exercising exactly the same verification code path used in production.

(This repository's schema/RLS/trigger behavior and the full signup -> login
-> authenticated-request -> logout flow were additionally verified live
against a real Supabase project during development -- see
/docs/progress.md.)
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.core import security
from app.main import app

_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_KID = "test-key"
_USER_ID = str(uuid.uuid4())


def _make_token(**overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": _USER_ID,
        "email": "test@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
        **overrides,
    }
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="ES256", headers={"kid": _KID})


@pytest.fixture(autouse=True)
def _patch_jwks(monkeypatch):
    """Point signature verification at our in-memory test key instead of a
    real network-fetched JWKS document."""
    fake_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=_PUBLIC_KEY)
    )
    monkeypatch.setattr(security, "_jwk_client", lambda: fake_client)
    yield


client = TestClient(app)


def test_valid_token_resolves_identity():
    token = _make_token()
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"id": _USER_ID, "email": "test@example.com"}


def test_missing_token_is_rejected():
    resp = client.get("/me")
    assert resp.status_code == 403  # no Authorization header at all


def test_expired_token_is_rejected():
    token = _make_token(exp=int(time.time()) - 120)  # well outside the verifier's clock-skew leeway
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_wrong_audience_is_rejected():
    token = _make_token(aud="some-other-app")
    resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_tampered_token_is_rejected():
    token = _make_token()
    tampered = token[:-4] + ("A" * 4 if token[-4] != "A" else "B" * 4)
    resp = client.get("/me", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401


def test_garbage_token_is_rejected():
    resp = client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
