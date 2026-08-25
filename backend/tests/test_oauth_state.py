"""Hermetic tests for the signed OAuth `state` parameter. No network."""

from __future__ import annotations

import time

import jwt
import pytest

from app.core import oauth_state
from app.core.oauth_state import OAuthStateError, create_state, verify_state


def test_create_and_verify_round_trip():
    state = create_state(tenant_id="tenant-123", user_id="user-456")
    tenant_id, user_id = verify_state(state)
    assert tenant_id == "tenant-123"
    assert user_id == "user-456"


def test_tampered_state_is_rejected():
    state = create_state(tenant_id="tenant-123", user_id="user-456")
    tampered = state[:-4] + ("A" * 4 if state[-4] != "A" else "B" * 4)
    with pytest.raises(OAuthStateError):
        verify_state(tampered)


def test_expired_state_is_rejected():
    settings = oauth_state.get_settings()
    now = int(time.time())
    payload = {
        "tenant_id": "tenant-123",
        "user_id": "user-456",
        "iat": now - 1000,
        "exp": now - 1,  # already expired
    }
    expired = jwt.encode(payload, settings.oauth_state_secret, algorithm="HS256")
    with pytest.raises(OAuthStateError):
        verify_state(expired)


def test_garbage_state_is_rejected():
    with pytest.raises(OAuthStateError):
        verify_state("not-a-real-token")


def test_state_signed_with_different_secret_is_rejected():
    forged = jwt.encode(
        {"tenant_id": "tenant-123", "user_id": "user-456", "exp": int(time.time()) + 600},
        "a-completely-different-secret",
        algorithm="HS256",
    )
    with pytest.raises(OAuthStateError):
        verify_state(forged)


def test_missing_secret_raises(monkeypatch):
    settings = oauth_state.get_settings()
    monkeypatch.setattr(settings, "oauth_state_secret", "")
    with pytest.raises(OAuthStateError):
        create_state(tenant_id="t", user_id="u")
