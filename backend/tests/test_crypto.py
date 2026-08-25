"""Hermetic tests for token encryption/decryption. No network."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core import crypto


def test_encrypt_decrypt_round_trip():
    plaintext = "1//0gExampleRefreshTokenValue"
    ciphertext = crypto.encrypt_token(plaintext)
    assert ciphertext != plaintext
    assert crypto.decrypt_token(ciphertext) == plaintext


def test_ciphertext_is_not_plaintext_substring():
    """A basic sanity check that we're not accidentally no-op'ing."""
    plaintext = "super-secret-refresh-token"
    ciphertext = crypto.encrypt_token(plaintext)
    assert plaintext not in ciphertext


def test_decrypt_with_wrong_key_fails(monkeypatch):
    ciphertext = crypto.encrypt_token("some-token")

    # Swap in a different key and clear the cached Fernet instance.
    crypto._fernet.cache_clear()
    other_key = Fernet.generate_key().decode()
    settings = crypto.get_settings()
    monkeypatch.setattr(settings, "token_encryption_key", other_key)

    with pytest.raises(crypto.TokenEncryptionError):
        crypto.decrypt_token(ciphertext)

    crypto._fernet.cache_clear()  # don't leak the swapped key into other tests


def test_decrypt_garbage_raises():
    with pytest.raises(crypto.TokenEncryptionError):
        crypto.decrypt_token("this-is-not-a-fernet-token")


def test_missing_key_raises(monkeypatch):
    crypto._fernet.cache_clear()
    settings = crypto.get_settings()
    monkeypatch.setattr(settings, "token_encryption_key", "")

    with pytest.raises(crypto.TokenEncryptionError):
        crypto.encrypt_token("anything")

    crypto._fernet.cache_clear()
