from datetime import datetime, timedelta
import html
import os
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Header, Path, Body, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, constr
from sqlmodel import Session, select

from ..db import get_session
from ..deps import (
    get_audit_context,
    get_current_user,
    get_current_user_token,
    get_effective_org_role,
    get_effective_project_role,
    limit_auth_requests,
    limit_public_requests,
    require_project_role,
)
from ..enterprise_auth import (
    build_sso_authorize_url,
    build_totp_uri,
    configured_sso_providers,
    exchange_sso_code,
    fetch_sso_profile,
    generate_totp_secret,
    sign_auth_payload,
    verify_auth_payload,
    verify_totp_code,
)
from ..identity_sync import sync_identity_groups
from ..models import (
    AuditLog,
    OrgRole,
    Organization,
    OrganizationMembership,
    ProjectMembership,
    Role,
    User,
    UserIdentity,
    UserToken,
)
from ..schemas import StrictBaseModel
from ..security import hash_password, verify_password

router = APIRouter(prefix="/users", tags=["users"])


def _normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


def _record_audit(
    session: Session,
    *,
    action: str,
    target_type: str,
    target_id: Optional[int],
    details: str,
    request: Optional[Request] = None,
    authorization: Optional[str] = None,
    x_admin_token: Optional[str] = None,
    actor_override: Optional[str] = None,
) -> None:
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        if actor_override:
            actor = actor_override
        session.add(
            AuditLog(
                actor=actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                details=details,
                actor_ip=actor_ip,
                user_agent=user_agent,
            )
        )
        session.commit()
    except Exception:
        session.rollback()


def _user_has_admin_privileges(session: Session, user_id: int) -> bool:
    org_admin = session.exec(
        select(OrganizationMembership).where(
            OrganizationMembership.user_id == user_id,
            OrganizationMembership.role.in_([OrgRole.ADMIN.value, OrgRole.OWNER.value]),
        )
    ).first()
    if org_admin is not None:
        return True
    project_admin = session.exec(
        select(ProjectMembership).where(
            ProjectMembership.user_id == user_id,
            ProjectMembership.role.in_([Role.ADMIN.value, Role.OWNER.value]),
        )
    ).first()
    return project_admin is not None


def _touch_session(session: Session, token_row: UserToken) -> None:
    now = datetime.utcnow()
    last_seen = getattr(token_row, "last_seen_at", None)
    if last_seen is not None and (now - last_seen) < timedelta(minutes=5):
        return
    token_row.last_seen_at = now
    session.add(token_row)
    session.commit()


def _default_session_name(*, auth_method: str, auth_provider: Optional[str], request: Optional[Request]) -> str:
    if auth_provider:
        return f"{auth_provider.title()} SSO"
    if auth_method == "password":
        agent = (request.headers.get("user-agent") if request else "") or ""
        if "chrome" in agent.lower():
            return "Browser session"
        if "python" in agent.lower():
            return "API session"
        return "Password session"
    return "User session"


def _issue_user_session(
    session: Session,
    *,
    user: User,
    request: Optional[Request],
    auth_method: str,
    auth_provider: Optional[str] = None,
    mfa_verified_at: Optional[datetime] = None,
    session_name: Optional[str] = None,
) -> tuple[str, UserToken]:
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    expires = now + timedelta(hours=8)
    row = UserToken(
        user_id=user.id,
        token=token,
        created_at=now,
        expires_at=expires,
        last_seen_at=now,
        session_name=session_name or _default_session_name(auth_method=auth_method, auth_provider=auth_provider, request=request),
        auth_method=auth_method,
        auth_provider=auth_provider,
        issued_from_ip=(request.client.host if request and request.client else None),
        issued_user_agent=(request.headers.get("user-agent") if request else None),
        mfa_verified_at=mfa_verified_at,
    )
    user.last_login_at = now
    session.add(user)
    session.add(row)
    session.commit()
    session.refresh(row)
    return token, row


def _token_payload(token: str, row: UserToken) -> dict:
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": row.expires_at,
        "session_id": row.id,
    }


def _serialize_org_roles(session: Session, user_id: int) -> list[dict]:
    memberships = session.exec(
        select(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
    ).all()
    rows = []
    for membership in memberships:
        org = session.get(Organization, membership.organization_id)
        rows.append(
            {
                "organization_id": membership.organization_id,
                "organization_name": org.name if org else f"org-{membership.organization_id}",
                "role": membership.role,
            }
        )
    return rows


def _serialize_linked_identities(session: Session, user_id: int) -> list[dict]:
    rows = session.exec(select(UserIdentity).where(UserIdentity.user_id == user_id)).all()
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "email": row.email,
            "display_name": row.display_name,
            "last_login_at": row.last_login_at,
        }
        for row in rows
    ]


def _serialize_session(row: UserToken, *, current_session_id: Optional[int]) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "revoked_at": getattr(row, "revoked_at", None),
        "last_seen_at": getattr(row, "last_seen_at", None),
        "session_name": getattr(row, "session_name", None),
        "auth_method": getattr(row, "auth_method", None),
        "auth_provider": getattr(row, "auth_provider", None),
        "issued_from_ip": getattr(row, "issued_from_ip", None),
        "issued_user_agent": getattr(row, "issued_user_agent", None),
        "mfa_verified_at": getattr(row, "mfa_verified_at", None),
        "current": row.id == current_session_id,
    }


def _me_payload(session: Session, user: User, current_token: UserToken) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "mfa_enabled": bool(user.mfa_secret and user.mfa_enabled_at),
        "last_login_at": user.last_login_at,
        "organizations": _serialize_org_roles(session, user.id),
        "linked_identities": _serialize_linked_identities(session, user.id),
        "current_session": _serialize_session(current_token, current_session_id=current_token.id),
    }


def _build_sso_callback_html(*, token: str, redirect_to: str) -> str:
    safe_token = html.escape(token, quote=True)
    safe_redirect = html.escape(redirect_to, quote=True)
    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>LastPing SSO</title>
    <script>
      localStorage.setItem("lastping_user_token", "{safe_token}");
      window.location.replace("{safe_redirect}");
    </script>
  </head>
  <body style="font-family: sans-serif; padding: 24px;">
    Completing sign-in...
  </body>
</html>"""


class CreateUserIn(StrictBaseModel):
    email: EmailStr
    password: constr(min_length=8, max_length=128)
    display_name: Optional[constr(min_length=1, max_length=120)] = None


class LoginIn(StrictBaseModel):
    email: EmailStr
    password: constr(min_length=8, max_length=128)


class LoginOut(BaseModel):
    access_token: Optional[str] = None
    token_type: str = "bearer"
    expires_at: Optional[datetime] = None
    session_id: Optional[int] = None
    mfa_required: bool = False
    mfa_setup_required: bool = False
    mfa_challenge_token: Optional[str] = None
    mfa_enrollment_secret: Optional[str] = None
    mfa_enrollment_uri: Optional[str] = None
    mfa_enforced: bool = False
    auth_provider: Optional[str] = None
    mfa_enabled: bool = False


class MfaChallengeIn(StrictBaseModel):
    challenge_token: str
    code: constr(min_length=6, max_length=12)


class MfaDisableIn(StrictBaseModel):
    code: constr(min_length=6, max_length=12)


class MembershipIn(StrictBaseModel):
    email: EmailStr
    role: constr(regex=r"^(owner|admin|editor|viewer)$") = "viewer"


@router.post("/create", response_model=dict, dependencies=[Depends(limit_public_requests), Depends(limit_auth_requests)])
def create_user(
    payload: CreateUserIn,
    request: Request = None,
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    admin_token = os.environ.get("ADMIN_TOKEN")
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin token required")

    email = _normalize_email(payload.email)
    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already exists")

    user = User(email=email, hashed_password=hash_password(payload.password), display_name=payload.display_name)
    session.add(user)
    session.commit()
    session.refresh(user)
    _record_audit(
        session,
        action="create_user",
        target_type="user",
        target_id=user.id,
        details=f"email={user.email}",
        request=request,
        x_admin_token=x_admin_token,
    )
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


@router.post("/login", response_model=LoginOut, dependencies=[Depends(limit_public_requests), Depends(limit_auth_requests)])
def login(payload: LoginIn, request: Request = None, session: Session = Depends(get_session)):
    email = _normalize_email(payload.email)
    user = session.exec(select(User).where(User.email == email)).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User inactive")

    if user.mfa_secret and user.mfa_enabled_at:
        challenge_token = sign_auth_payload({"user_id": user.id}, purpose="user_login_mfa", ttl_seconds=300)
        _record_audit(
            session,
            action="user_login_mfa_challenge",
            target_type="user",
            target_id=user.id,
            details="password login requires MFA verification",
            request=request,
            actor_override=f"user:{user.id}",
        )
        return LoginOut(
            mfa_required=True,
            mfa_challenge_token=challenge_token,
            mfa_enforced=True,
            mfa_enabled=True,
        )

    if _user_has_admin_privileges(session, user.id):
        secret = generate_totp_secret()
        challenge_token = sign_auth_payload(
            {"user_id": user.id, "secret": secret, "issue_session": True},
            purpose="user_mfa_enroll",
            ttl_seconds=900,
        )
        _record_audit(
            session,
            action="user_login_mfa_enroll_required",
            target_type="user",
            target_id=user.id,
            details="admin login requires MFA enrollment",
            request=request,
            actor_override=f"user:{user.id}",
        )
        return LoginOut(
            mfa_setup_required=True,
            mfa_challenge_token=challenge_token,
            mfa_enrollment_secret=secret,
            mfa_enrollment_uri=build_totp_uri(secret, email=user.email),
            mfa_enforced=True,
        )

    token, token_row = _issue_user_session(session, user=user, request=request, auth_method="password")
    _record_audit(
        session,
        action="user_login",
        target_type="user",
        target_id=user.id,
        details=f"token_expires_at={token_row.expires_at.isoformat()}",
        request=request,
        actor_override=f"user:{user.id}",
    )
    return LoginOut(**_token_payload(token, token_row))


@router.post("/mfa/login/verify", response_model=LoginOut, dependencies=[Depends(limit_public_requests), Depends(limit_auth_requests)])
def verify_login_mfa(payload: MfaChallengeIn, request: Request = None, session: Session = Depends(get_session)):
    challenge = verify_auth_payload(payload.challenge_token, purpose="user_login_mfa")
    user = session.get(User, int(challenge["user_id"]))
    if not user or not user.mfa_secret or not user.mfa_enabled_at:
        raise HTTPException(status_code=400, detail="MFA is not enabled for this user")
    if not verify_totp_code(user.mfa_secret, payload.code):
        raise HTTPException(status_code=401, detail="Invalid MFA code")
    token, token_row = _issue_user_session(
        session,
        user=user,
        request=request,
        auth_method="password",
        mfa_verified_at=datetime.utcnow(),
    )
    _record_audit(
        session,
        action="user_login_mfa",
        target_type="user",
        target_id=user.id,
        details=f"token_expires_at={token_row.expires_at.isoformat()}",
        request=request,
        actor_override=f"user:{user.id}",
    )
    return LoginOut(**_token_payload(token, token_row), mfa_enabled=True)


@router.post("/mfa/enroll", dependencies=[Depends(limit_public_requests), Depends(limit_auth_requests)])
def enroll_mfa(current_user: User = Depends(get_current_user)):
    secret = generate_totp_secret()
    challenge_token = sign_auth_payload(
        {"user_id": current_user.id, "secret": secret, "issue_session": False},
        purpose="user_mfa_enroll",
        ttl_seconds=900,
    )
    return {
        "challenge_token": challenge_token,
        "secret": secret,
        "otpauth_uri": build_totp_uri(secret, email=current_user.email),
    }


@router.post("/mfa/enable", response_model=LoginOut, dependencies=[Depends(limit_public_requests), Depends(limit_auth_requests)])
def enable_mfa(payload: MfaChallengeIn, request: Request = None, session: Session = Depends(get_session)):
    challenge = verify_auth_payload(payload.challenge_token, purpose="user_mfa_enroll")
    user = session.get(User, int(challenge["user_id"]))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    secret = str(challenge.get("secret") or "").strip()
    if not secret:
        raise HTTPException(status_code=400, detail="Missing enrollment secret")
    if not verify_totp_code(secret, payload.code):
        raise HTTPException(status_code=401, detail="Invalid MFA code")
    user.mfa_secret = secret
    user.mfa_enabled_at = datetime.utcnow()
    session.add(user)
    session.commit()
    _record_audit(
        session,
        action="user_mfa_enabled",
        target_type="user",
        target_id=user.id,
        details="enabled TOTP MFA",
        request=request,
        actor_override=f"user:{user.id}",
    )
    if challenge.get("issue_session"):
        token, token_row = _issue_user_session(
            session,
            user=user,
            request=request,
            auth_method="password",
            mfa_verified_at=datetime.utcnow(),
        )
        _record_audit(
            session,
            action="user_login_mfa",
            target_type="user",
            target_id=user.id,
            details=f"token_expires_at={token_row.expires_at.isoformat()}",
            request=request,
            actor_override=f"user:{user.id}",
        )
        return LoginOut(**_token_payload(token, token_row), mfa_enabled=True)
    return LoginOut(mfa_enabled=True)


@router.post("/mfa/disable", dependencies=[Depends(limit_public_requests), Depends(limit_auth_requests)])
def disable_mfa(
    payload: MfaDisableIn,
    request: Request = None,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not current_user.mfa_secret or not current_user.mfa_enabled_at:
        raise HTTPException(status_code=400, detail="MFA is not enabled")
    if not verify_totp_code(current_user.mfa_secret, payload.code):
        raise HTTPException(status_code=401, detail="Invalid MFA code")
    current_user.mfa_secret = None
    current_user.mfa_enabled_at = None
    session.add(current_user)
    session.commit()
    _record_audit(
        session,
        action="user_mfa_disabled",
        target_type="user",
        target_id=current_user.id,
        details="disabled TOTP MFA",
        request=request,
        actor_override=f"user:{current_user.id}",
    )
    return {"disabled": True}


@router.get("/me")
def me(
    authorization: Optional[str] = Header(None),
    current_user: User = Depends(get_current_user),
    current_token: UserToken = Depends(get_current_user_token),
    session: Session = Depends(get_session),
):
    _touch_session(session, current_token)
    session.refresh(current_token)
    return _me_payload(session, current_user, current_token)


@router.get("/sessions")
def list_sessions(
    current_user: User = Depends(get_current_user),
    current_token: UserToken = Depends(get_current_user_token),
    session: Session = Depends(get_session),
):
    _touch_session(session, current_token)
    rows = session.exec(
        select(UserToken).where(UserToken.user_id == current_user.id).order_by(UserToken.created_at.desc())
    ).all()
    return {"sessions": [_serialize_session(row, current_session_id=current_token.id) for row in rows]}


@router.delete("/sessions/{session_id}")
def revoke_session(
    session_id: int = Path(..., ge=1),
    request: Request = None,
    current_user: User = Depends(get_current_user),
    current_token: UserToken = Depends(get_current_user_token),
    session: Session = Depends(get_session),
):
    row = session.get(UserToken, session_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    if not row.revoked_at:
        row.revoked_at = datetime.utcnow()
        session.add(row)
        session.commit()
    _record_audit(
        session,
        action="revoke_user_session",
        target_type="user_token",
        target_id=row.id,
        details=f"current={row.id == current_token.id}",
        request=request,
        actor_override=f"user:{current_user.id}",
    )
    return {"revoked": True, "session_id": row.id}


@router.post("/sessions/revoke-others")
def revoke_other_sessions(
    request: Request = None,
    current_user: User = Depends(get_current_user),
    current_token: UserToken = Depends(get_current_user_token),
    session: Session = Depends(get_session),
):
    rows = session.exec(
        select(UserToken).where(UserToken.user_id == current_user.id, UserToken.id != current_token.id, UserToken.revoked_at == None)
    ).all()
    now = datetime.utcnow()
    for row in rows:
        row.revoked_at = now
        session.add(row)
    session.commit()
    _record_audit(
        session,
        action="revoke_other_user_sessions",
        target_type="user",
        target_id=current_user.id,
        details=f"count={len(rows)}",
        request=request,
        actor_override=f"user:{current_user.id}",
    )
    return {"revoked": len(rows)}


@router.get("/sso/providers")
def list_sso_providers():
    return {
        "providers": [
            {"name": provider.name, "label": provider.label, "enabled": True}
            for provider in configured_sso_providers()
        ]
    }


@router.get("/sso/{provider}/start")
def start_sso_login(
    provider: str,
    request: Request,
    redirect_to: str = Query("/ui/account"),
):
    try:
        provider_cfg = next(item for item in configured_sso_providers() if item.name == provider.lower())
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="SSO provider not configured") from exc
    state = sign_auth_payload(
        {"provider": provider_cfg.name, "redirect_to": redirect_to},
        purpose="user_sso_state",
        ttl_seconds=600,
    )
    redirect_uri = str(request.url_for("users_sso_callback", provider=provider_cfg.name))
    return RedirectResponse(build_sso_authorize_url(provider_cfg, redirect_uri=redirect_uri, state=state), status_code=302)


@router.get("/sso/{provider}/callback", name="users_sso_callback")
def finish_sso_login(
    provider: str,
    code: str = Query(..., min_length=1),
    state: str = Query(..., min_length=1),
    return_json: bool = Query(False),
    request: Request = None,
    session: Session = Depends(get_session),
):
    try:
        provider_cfg = next(item for item in configured_sso_providers() if item.name == provider.lower())
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="SSO provider not configured") from exc
    state_payload = verify_auth_payload(state, purpose="user_sso_state")
    if state_payload.get("provider") != provider_cfg.name:
        raise HTTPException(status_code=400, detail="SSO state provider mismatch")
    redirect_uri = str(request.url_for("users_sso_callback", provider=provider_cfg.name))
    token_payload = exchange_sso_code(provider_cfg, code=code, redirect_uri=redirect_uri)
    profile = fetch_sso_profile(provider_cfg, token_payload)
    email = _normalize_email(profile["email"])
    now = datetime.utcnow()

    identity = session.exec(
        select(UserIdentity).where(
            UserIdentity.provider == provider_cfg.name,
            UserIdentity.provider_subject == profile["subject"],
        )
    ).first()
    user = session.get(User, identity.user_id) if identity else None
    if user is None:
        user = session.exec(select(User).where(User.email == email)).first()
    if user is None:
        user = User(
            email=email,
            hashed_password=hash_password(secrets.token_urlsafe(32)),
            display_name=profile["display_name"],
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    else:
        if profile["display_name"] and user.display_name != profile["display_name"]:
            user.display_name = profile["display_name"]
            session.add(user)
            session.commit()

    if identity is None:
        identity = UserIdentity(
            user_id=user.id,
            provider=provider_cfg.name,
            provider_subject=profile["subject"],
            email=email,
            display_name=profile["display_name"],
            created_at=now,
        )
    identity.user_id = user.id
    identity.email = email
    identity.display_name = profile["display_name"]
    identity.last_login_at = now
    session.add(identity)
    sync_summary = sync_identity_groups(
        session,
        user=user,
        identity=identity,
        provider=provider_cfg.name,
        groups=profile.get("groups"),
        occurred_at=now,
    )
    session.commit()

    token, token_row = _issue_user_session(
        session,
        user=user,
        request=request,
        auth_method="sso",
        auth_provider=provider_cfg.name,
        session_name=f"{provider_cfg.label} SSO",
    )
    _record_audit(
        session,
        action="user_sso_login",
        target_type="user",
        target_id=user.id,
        details=(
            f"provider={provider_cfg.name},groups={sync_summary['group_count']},"
            f"org_added={sync_summary['org_memberships_added']},org_upgraded={sync_summary['org_roles_upgraded']},"
            f"team_added={sync_summary['team_memberships_added']},team_upgraded={sync_summary['team_roles_upgraded']}"
        ),
        request=request,
        actor_override=f"user:{user.id}",
    )
    if return_json:
        return LoginOut(**_token_payload(token, token_row), auth_provider=provider_cfg.name)
    redirect_to = str(state_payload.get("redirect_to") or "/ui/account")
    return HTMLResponse(_build_sso_callback_html(token=token, redirect_to=redirect_to))


@router.get("/projects/{project_id}/role")
def my_role(
    project_id: int = Path(..., ge=1),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    role = get_effective_project_role(session, current_user.id, project_id)
    return {"role": role}


@router.get("/projects/{project_id}/membership")
def list_members(
    project_id: int = Path(..., ge=1),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    user = get_current_user(authorization=authorization, session=session)
    require_project_role(project_id, Role.ADMIN.value, current_user=user, session=session)

    members = session.exec(select(ProjectMembership).where(ProjectMembership.project_id == project_id)).all()
    out = []
    for membership in members:
        member_user = session.get(User, membership.user_id)
        out.append({"id": member_user.id, "email": member_user.email, "role": membership.role})
    _record_audit(
        session,
        action="list_project_members",
        target_type="project",
        target_id=project_id,
        details=f"count={len(out)}",
        request=request,
        authorization=authorization,
    )
    return out


@router.post("/projects/{project_id}/membership")
def add_member(
    project_id: int = Path(..., ge=1),
    payload: MembershipIn = Body(...),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    user = get_current_user(authorization=authorization, session=session)
    require_project_role(project_id, Role.ADMIN.value, current_user=user, session=session)

    target = session.exec(select(User).where(User.email == _normalize_email(payload.email))).first()
    if not target:
        target = User(
            email=_normalize_email(payload.email),
            hashed_password=hash_password(secrets.token_urlsafe(16)),
            is_active=False,
        )
        session.add(target)
        session.commit()
        session.refresh(target)
        _record_audit(
            session,
            action="invite_user",
            target_type="user",
            target_id=target.id,
            details=f"email={target.email}",
            request=request,
            authorization=authorization,
        )

    existing = session.exec(
        select(ProjectMembership).where(
            ProjectMembership.user_id == target.id,
            ProjectMembership.project_id == project_id,
        )
    ).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already a member")
    membership = ProjectMembership(user_id=target.id, project_id=project_id, role=payload.role)
    session.add(membership)
    session.commit()
    session.refresh(membership)
    _record_audit(
        session,
        action="add_project_member",
        target_type="project_membership",
        target_id=membership.id,
        details=f"project_id={project_id}, user_id={target.id}, role={payload.role}",
        request=request,
        authorization=authorization,
    )
    return {"user_id": target.id, "email": target.email, "role": payload.role}


@router.delete("/projects/{project_id}/membership/{user_id}")
def remove_member(
    project_id: int = Path(..., ge=1),
    user_id: int = Path(..., ge=1),
    request: Request = None,
    authorization: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    user = get_current_user(authorization=authorization, session=session)
    require_project_role(project_id, Role.ADMIN.value, current_user=user, session=session)

    target_membership = session.exec(
        select(ProjectMembership).where(
            ProjectMembership.user_id == user_id,
            ProjectMembership.project_id == project_id,
        )
    ).first()
    if not target_membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership_id = target_membership.id
    session.delete(target_membership)
    session.commit()
    _record_audit(
        session,
        action="remove_project_member",
        target_type="project_membership",
        target_id=membership_id,
        details=f"project_id={project_id}, user_id={user_id}",
        request=request,
        authorization=authorization,
    )
    return {"status": "removed"}
