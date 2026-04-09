"""
Security helpers for API key generation, token verification, and secret encryption.

PBKDF2-HMAC-SHA256 is used for API keys and session tokens. Integration
secrets use symmetric encryption-at-rest via Fernet, stored with the
serialized prefix `enc$fernet$...`.
"""

import base64
import hashlib
import os
import secrets
from functools import lru_cache
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import TEXT, TypeDecorator


_DEV_SECRET_ENCRYPTION_KEY = "lastping-dev-only-encryption-key"
_ENCRYPTED_SECRET_PREFIX = "enc$fernet$"


def generate_api_key() -> str:
    # Generate a single-use plaintext API key (returned to clients once)
    return secrets.token_urlsafe(32)


def _pbkdf2_hash(key: str, salt: bytes, iterations: int = 100_000) -> bytes:
    # Return raw PBKDF2-derived key bytes for the given params.
    return hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), salt, iterations)


def fingerprint_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _derive_fernet_key(secret_material: str) -> bytes:
    digest = hashlib.sha256(secret_material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _secret_key_material() -> str:
    for env_name in ("LASTPING_ENCRYPTION_KEY", "ADMIN_TOKEN"):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    return _DEV_SECRET_ENCRYPTION_KEY


@lru_cache(maxsize=8)
def _fernet_for_material(secret_material: str) -> Fernet:
    return Fernet(_derive_fernet_key(secret_material))


def _get_fernet() -> Fernet:
    return _fernet_for_material(_secret_key_material())


def is_encrypted_secret(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.startswith(_ENCRYPTED_SECRET_PREFIX)


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    if is_encrypted_secret(text):
        return text
    token = _get_fernet().encrypt(text.encode("utf-8")).decode("utf-8")
    return f"{_ENCRYPTED_SECRET_PREFIX}{token}"


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    if not is_encrypted_secret(text):
        return text
    token = text[len(_ENCRYPTED_SECRET_PREFIX) :]
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Encrypted secret could not be decrypted with the configured key") from exc


class EncryptedString(TypeDecorator):
    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_secret(value)

    def process_result_value(self, value, dialect):
        return decrypt_secret(value)


def hash_api_key(key: str) -> str:
    salt = os.urandom(16)
    iterations = 100_000
    dk = _pbkdf2_hash(key, salt, iterations)
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
