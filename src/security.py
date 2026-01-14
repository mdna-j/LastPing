"""
Security helpers for API key generation and verification.

Uses PBKDF2-HMAC-SHA256 to derive a secure hash for API keys. The
serialized form is `pbkdf2_sha256$<iterations>$<salt_hex>$<dk_hex>`.
Keep this code small and stable — changes affect authentication.
"""

import os
import hashlib
import secrets
from typing import Tuple


def generate_api_key() -> str:
    # Generate a single-use plaintext API key (returned to clients once)
    return secrets.token_urlsafe(32)


def _pbkdf2_hash(key: str, salt: bytes, iterations: int = 100_000) -> bytes:
    # Return raw PBKDF2-derived key bytes for the given params.
    return hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), salt, iterations)


def hash_api_key(key: str) -> str:
    salt = os.urandom(16)
    iterations = 100_000
    dk = _pbkdf2_hash(key, salt, iterations)
    # Store algorithm, iterations, salt and derived key in hex form.
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_api_key(key: str, stored: str) -> bool:
    if not stored:
        return False
    try:
        parts = stored.split("$")
        if parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        dk = bytes.fromhex(parts[3])
        # Compute candidate and compare using constant-time comparison
        candidate = hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(candidate, dk)
    except Exception:
        return False


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 200_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
        if parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        dk = bytes.fromhex(parts[3])
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return secrets.compare_digest(candidate, dk)
    except Exception:
        return False
