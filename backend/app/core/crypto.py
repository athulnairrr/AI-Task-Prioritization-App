"""Application-level encryption for Google OAuth tokens at rest.

Google refresh tokens are long-lived, high-value credentials -- anyone who
reads one out of the database can pull the connected user's calendar
indefinitely until it's revoked. `google_calendar_connections.refresh_token`
(and `.access_token`, for defense in depth even though it's short-lived) are
therefore never stored as plaintext: everything written to those columns
goes through `encrypt_token()` first, and everything read back goes through
`decrypt_token()` -- nowhere else in the codebase should touch that column
directly.

Algorithm: Fernet (AES-128-CBC + HMAC-SHA256, from the `cryptography`
package) -- authenticated symmetric encryption, i.e. tampering with
ciphertext is detected (raises `TokenEncryptionError`), not silently
decrypted into garbage.

Key management:
  * `TOKEN_ENCRYPTION_KEY` is a Fernet key (32 url-safe base64-encoded
    bytes), generated with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  * It must never be committed, logged, or sent to a client. It lives only
    in server-side environment config (`.env`, locally; a secrets manager /
    platform env var store in any real deployment).
  * Rotation: Fernet has no built-in multi-key rotation, so rotating this
    key invalidates every already-stored token. To rotate without forcing
    every user to reconnect: (1) decrypt all existing tokens with the old
    key, (2) re-encrypt with the new key, (3) deploy the new key. Because
    this MVP stores no other secrets this way, the simpler fallback is
    acceptable at this scale: rotate the key and treat every existing
    connection as needing reconnection (they'll surface as
    `reauth_required` the next time a token operation fails to decrypt).
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class TokenEncryptionError(Exception):
    """Raised for a missing/invalid encryption key, or ciphertext that
    fails to decrypt (wrong key, corruption, or tampering)."""


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.token_encryption_key:
        raise TokenEncryptionError("TOKEN_ENCRYPTION_KEY is not configured.")
    try:
        return Fernet(settings.token_encryption_key.encode())
    except Exception as exc:
        raise TokenEncryptionError(f"TOKEN_ENCRYPTION_KEY is not a valid Fernet key: {exc}") from exc


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise TokenEncryptionError(
            "Stored token could not be decrypted (wrong key or corrupted data)."
        ) from exc
