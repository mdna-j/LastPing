import os
import hashlib
import secrets


def generate_api_key() -> str:
    return secrets.token_urlsafe(32)


def _pbkdf2_hash(key: str, salt: bytes, iterations: int = 100_000) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), salt, iterations)


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
