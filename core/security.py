"""
core/security.py
────────────────
Cryptographic primitives shared across the auth layer — dependency-free
(Python stdlib only): PBKDF2-HMAC-SHA256 password hashing with per-user salt,
constant-time verification, and opaque token generation for sessions / resets.

Kept separate from DB/ORM concerns so it can be unit-tested in isolation and
reused by seeding scripts, the auth service, and password-reset flows.
"""

import hashlib
import hmac
import secrets

# PBKDF2 tuning — strong for SHA-256 in 2026, still fast enough to verify on
# every login without noticeable lag.
_PBKDF2_ITERATIONS = 200_000
_PBKDF2_ALGO = "sha256"


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """Return (salt_hex, hash_hex). Generates a fresh salt when none is given."""
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS
    )
    return salt, dk.hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    """Constant-time password check (avoids timing oracles)."""
    if not (salt and expected_hash):
        return False
    _, computed = hash_password(password, salt)
    return hmac.compare_digest(computed, expected_hash)


def new_token(nbytes: int = 32) -> str:
    """Opaque, URL-safe random token for sessions and reset links."""
    return secrets.token_urlsafe(nbytes)
