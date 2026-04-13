from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx


def _urlsafe_b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _urlsafe_b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(f"{data}{padding}".encode("ascii"))


def _signing_material() -> str:
    for env_name in ("LASTPING_ENCRYPTION_KEY", "ADMIN_TOKEN"):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    return "lastping-dev-only-enterprise-auth"


def sign_auth_payload(payload: dict[str, Any], *, purpose: str, ttl_seconds: int) -> str:
    body = dict(payload)
    body["purpose"] = purpose
    body["exp"] = int((datetime.utcnow() + timedelta(seconds=ttl_seconds)).timestamp())
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(_signing_material().encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_urlsafe_b64encode(raw)}.{_urlsafe_b64encode(signature)}"


def verify_auth_payload(token: str, *, purpose: str) -> dict[str, Any]:
    try:
        encoded_payload, encoded_sig = token.split(".", 1)
    except ValueError as exc:
        raise ValueError("Invalid auth token") from exc
    raw = _urlsafe_b64decode(encoded_payload)
    provided_sig = _urlsafe_b64decode(encoded_sig)
    expected_sig = hmac.new(_signing_material().encode("utf-8"), raw, hashlib.sha256).digest()
    if not secrets.compare_digest(provided_sig, expected_sig):
        raise ValueError("Invalid auth token signature")
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("purpose") != purpose:
        raise ValueError("Unexpected auth token purpose")
    exp = int(payload.get("exp") or 0)
    if exp <= int(datetime.utcnow().timestamp()):
        raise ValueError("Auth token expired")
    return payload


def generate_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode("ascii").rstrip("=")


def _totp_counter(at: Optional[datetime], *, period_seconds: int) -> int:
    now = at or datetime.utcnow()
    return int(now.timestamp()) // period_seconds


def generate_totp_code(
    secret: str,
    *,
    at: Optional[datetime] = None,
    period_seconds: int = 30,
    digits: int = 6,
) -> str:
    normalized = (secret or "").strip().upper()
    if not normalized:
        raise ValueError("Missing TOTP secret")
    key = base64.b32decode(normalized + "=" * (-len(normalized) % 8), casefold=True)
    counter = _totp_counter(at, period_seconds=period_seconds)
    message = struct.pack(">Q", counter)
    digest = hmac.new(key, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    code = code_int % (10 ** digits)
    return str(code).zfill(digits)


def verify_totp_code(
    secret: str,
    code: str,
    *,
    at: Optional[datetime] = None,
    period_seconds: int = 30,
    digits: int = 6,
    window: int = 1,
) -> bool:
    candidate = (code or "").strip()
    if not candidate.isdigit() or len(candidate) != digits:
        return False
    baseline = at or datetime.utcnow()
    for offset in range(-window, window + 1):
        probe = baseline + timedelta(seconds=offset * period_seconds)
        if generate_totp_code(secret, at=probe, period_seconds=period_seconds, digits=digits) == candidate:
            return True
    return False


def build_totp_uri(secret: str, *, email: str, issuer: str = "LastPing") -> str:
    label = urllib.parse.quote(f"{issuer}:{email}")
    params = urllib.parse.urlencode({"secret": secret, "issuer": issuer})
    return f"otpauth://totp/{label}?{params}"


@dataclass
class SsoProvider:
    name: str
    label: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: str
    emails_url: Optional[str] = None


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _oidc_like_provider(
    *,
    name: str,
    label: str,
    authorize_url: str,
    token_url: str,
    userinfo_url: str,
    default_scopes: str,
) -> Optional[SsoProvider]:
    client_id = _env(f"SSO_{name.upper()}_CLIENT_ID")
    client_secret = _env(f"SSO_{name.upper()}_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return SsoProvider(
        name=name,
        label=label,
        client_id=client_id,
        client_secret=client_secret,
        authorize_url=_env(f"SSO_{name.upper()}_AUTHORIZE_URL") or authorize_url,
        token_url=_env(f"SSO_{name.upper()}_TOKEN_URL") or token_url,
        userinfo_url=_env(f"SSO_{name.upper()}_USERINFO_URL") or userinfo_url,
        scopes=_env(f"SSO_{name.upper()}_SCOPES") or default_scopes,
        emails_url=_env(f"SSO_{name.upper()}_EMAILS_URL") or None,
    )


def configured_sso_providers() -> list[SsoProvider]:
    providers: list[Optional[SsoProvider]] = [
        _oidc_like_provider(
            name="google",
            label="Google",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
            default_scopes="openid email profile",
        ),
        _oidc_like_provider(
            name="github",
            label="GitHub",
            authorize_url="https://github.com/login/oauth/authorize",
            token_url="https://github.com/login/oauth/access_token",
            userinfo_url="https://api.github.com/user",
            default_scopes="read:user user:email",
        ),
    ]

    okta_issuer = _env("SSO_OKTA_ISSUER").rstrip("/")
    if okta_issuer:
        okta = _oidc_like_provider(
            name="okta",
            label="Okta",
            authorize_url=f"{okta_issuer}/v1/authorize",
            token_url=f"{okta_issuer}/v1/token",
            userinfo_url=f"{okta_issuer}/v1/userinfo",
            default_scopes="openid email profile",
        )
        providers.append(okta)

    generic = _oidc_like_provider(
        name="oidc",
        label="OIDC",
        authorize_url=_env("SSO_OIDC_AUTHORIZE_URL"),
        token_url=_env("SSO_OIDC_TOKEN_URL"),
        userinfo_url=_env("SSO_OIDC_USERINFO_URL"),
        default_scopes="openid email profile",
    )
    providers.append(generic)
    return [provider for provider in providers if provider is not None]


def get_sso_provider(name: str) -> SsoProvider:
    normalized = (name or "").strip().lower()
    for provider in configured_sso_providers():
        if provider.name == normalized:
            return provider
    raise KeyError(normalized)


def build_sso_authorize_url(provider: SsoProvider, *, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": provider.scopes,
        "state": state,
    }
    return f"{provider.authorize_url}?{urllib.parse.urlencode(params)}"


def exchange_sso_code(provider: SsoProvider, *, code: str, redirect_uri: str) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": provider.client_id,
        "client_secret": provider.client_secret,
        "redirect_uri": redirect_uri,
    }
    response = httpx.post(provider.token_url, data=payload, headers=headers, timeout=15.0)
    response.raise_for_status()
    return response.json()


def _pick_github_email(access_token: str, emails_url: str) -> Optional[str]:
    response = httpx.get(
        emails_url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=15.0,
    )
    response.raise_for_status()
    for row in response.json():
        if isinstance(row, dict) and row.get("primary") and row.get("verified") and row.get("email"):
            return str(row["email"]).strip().lower()
    for row in response.json():
        if isinstance(row, dict) and row.get("verified") and row.get("email"):
            return str(row["email"]).strip().lower()
    return None


def fetch_sso_profile(provider: SsoProvider, token_payload: dict[str, Any]) -> dict[str, str]:
    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("SSO provider did not return an access token")
    response = httpx.get(
        provider.userinfo_url,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=15.0,
    )
    response.raise_for_status()
    payload = response.json()
    if provider.name == "github":
        email = (payload.get("email") or "").strip().lower()
        if not email:
            email = _pick_github_email(access_token, provider.emails_url or "https://api.github.com/user/emails") or ""
        subject = str(payload.get("id") or "").strip()
        display_name = (payload.get("name") or payload.get("login") or email).strip()
    else:
        email = str(payload.get("email") or "").strip().lower()
        subject = str(payload.get("sub") or payload.get("id") or email).strip()
        display_name = str(payload.get("name") or payload.get("preferred_username") or email).strip()
    if not subject or not email:
        raise RuntimeError("SSO provider response is missing subject or email")
    return {"subject": subject, "email": email, "display_name": display_name}
