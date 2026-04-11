from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from .db import ensure_engine
from .models import ApiKey, ProjectSecretLifecycle


UNSET = object()

SECRET_PAGERDUTY_INTEGRATION_KEY = "pagerduty_integration_key"
SECRET_JIRA_API_TOKEN = "jira_api_token"
SECRET_DISCORD_WEBHOOK_URL = "discord_webhook_url"
SECRET_SLACK_WEBHOOK_URL = "slack_webhook_url"
SECRET_GENERIC_WEBHOOK_URL = "generic_webhook_url"

_PROJECT_SECRET_ATTRS = {
    SECRET_PAGERDUTY_INTEGRATION_KEY: "pagerduty_integration_key",
    SECRET_JIRA_API_TOKEN: "jira_api_token",
    SECRET_DISCORD_WEBHOOK_URL: "discord_webhook_url",
    SECRET_SLACK_WEBHOOK_URL: "slack_webhook_url",
    SECRET_GENERIC_WEBHOOK_URL: "generic_webhook_url",
}


def utcnow() -> datetime:
    return datetime.utcnow()


def normalize_utc_naive(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def rotation_due_at(last_rotated_at: Optional[datetime], rotation_interval_days: Optional[int]) -> Optional[datetime]:
    last_rotated_at = normalize_utc_naive(last_rotated_at)
    if not last_rotated_at or not rotation_interval_days or rotation_interval_days <= 0:
        return None
    return last_rotated_at + timedelta(days=int(rotation_interval_days))


def api_key_rotation_due_at(api_key: ApiKey) -> Optional[datetime]:
    rotated_at = getattr(api_key, "last_rotated_at", None) or getattr(api_key, "created_at", None)
    return rotation_due_at(rotated_at, getattr(api_key, "rotation_interval_days", None))


def api_key_rotation_required(api_key: ApiKey, *, now: Optional[datetime] = None) -> bool:
    now = now or utcnow()
    expires_at = getattr(api_key, "expires_at", None)
    expires_at = normalize_utc_naive(expires_at)
    if expires_at and expires_at <= now:
        return True
    due_at = api_key_rotation_due_at(api_key)
    return bool(due_at and due_at <= now)


def api_key_is_expired(api_key: ApiKey, *, now: Optional[datetime] = None) -> bool:
    now = now or utcnow()
    expires_at = normalize_utc_naive(getattr(api_key, "expires_at", None))
    return bool(expires_at and expires_at <= now)


def touch_api_key_last_used(session: Session, api_key: ApiKey, *, used_at: Optional[datetime] = None, min_interval_seconds: int = 60) -> None:
    when = used_at or utcnow()
    last_used_at = normalize_utc_naive(getattr(api_key, "last_used_at", None))
    if last_used_at and (when - last_used_at).total_seconds() < max(0, int(min_interval_seconds)):
        return
    api_key.last_used_at = when
    session.add(api_key)
    session.commit()
    session.refresh(api_key)


def _secret_attr(secret_name: str) -> str:
    attr = _PROJECT_SECRET_ATTRS.get(secret_name)
    if not attr:
        raise ValueError(f"Unsupported project secret: {secret_name}")
    return attr


def get_project_secret_value(project, secret_name: str) -> Optional[str]:
    return getattr(project, _secret_attr(secret_name), None)


def set_project_secret_value(project, secret_name: str, value: Optional[str]) -> None:
    setattr(project, _secret_attr(secret_name), value)


def get_project_secret_lifecycle(
    session: Session,
    project_id: int,
    secret_name: str,
    *,
    create: bool = False,
) -> Optional[ProjectSecretLifecycle]:
    row = session.exec(
        select(ProjectSecretLifecycle).where(
            ProjectSecretLifecycle.project_id == project_id,
            ProjectSecretLifecycle.secret_name == secret_name,
        )
    ).first()
    if row or not create:
        return row
    row = ProjectSecretLifecycle(
        project_id=project_id,
        secret_name=secret_name,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def update_project_secret_policy(
    session: Session,
    project_id: int,
    secret_name: str,
    *,
    expires_at=UNSET,
    rotation_interval_days=UNSET,
) -> ProjectSecretLifecycle:
    row = get_project_secret_lifecycle(session, project_id, secret_name, create=True)
    row.updated_at = utcnow()
    if expires_at is not UNSET:
        row.expires_at = normalize_utc_naive(expires_at)
    if rotation_interval_days is not UNSET:
        row.rotation_interval_days = rotation_interval_days
    session.add(row)
    return row


def clear_project_secret_lifecycle(
    session: Session,
    project_id: int,
    secret_name: str,
    *,
    clear_policy: bool = True,
) -> ProjectSecretLifecycle:
    row = get_project_secret_lifecycle(session, project_id, secret_name, create=True)
    row.previous_secret_value = None
    row.previous_secret_expires_at = None
    row.last_rotated_at = utcnow()
    row.updated_at = utcnow()
    row.last_used_at = None
    if clear_policy:
        row.expires_at = None
        row.rotation_interval_days = None
    session.add(row)
    return row


def rotate_project_secret(
    session: Session,
    project,
    secret_name: str,
    *,
    new_value: Optional[str],
    grace_seconds: int = 3600,
    expires_at=UNSET,
    rotation_interval_days=UNSET,
) -> ProjectSecretLifecycle:
    now = utcnow()
    row = get_project_secret_lifecycle(session, project.id, secret_name, create=True)
    current_value = get_project_secret_value(project, secret_name)

    if current_value and new_value and current_value != new_value and int(grace_seconds or 0) > 0:
        row.previous_secret_value = current_value
        row.previous_secret_expires_at = now + timedelta(seconds=int(grace_seconds))
    else:
        row.previous_secret_value = None
        row.previous_secret_expires_at = None

    set_project_secret_value(project, secret_name, new_value)
    row.last_rotated_at = now
    row.updated_at = now
    if expires_at is not UNSET:
        row.expires_at = normalize_utc_naive(expires_at)
    if rotation_interval_days is not UNSET:
        row.rotation_interval_days = rotation_interval_days
    session.add(project)
    session.add(row)
    return row


def active_project_secret_candidates(project, secret_name: str, *, session: Optional[Session] = None, now: Optional[datetime] = None) -> list[str]:
    current = get_project_secret_value(project, secret_name)
    when = now or utcnow()

    def _build(row: Optional[ProjectSecretLifecycle]) -> list[str]:
        values: list[str] = []
        row_expires_at = normalize_utc_naive(getattr(row, "expires_at", None)) if row else None
        previous_expires_at = normalize_utc_naive(getattr(row, "previous_secret_expires_at", None)) if row else None
        if current and (not row or not row_expires_at or row_expires_at > when):
            values.append(current)
        if row and row.previous_secret_value and previous_expires_at and previous_expires_at > when:
            values.append(row.previous_secret_value)
        deduped: list[str] = []
        for value in values:
            if value and value not in deduped:
                deduped.append(value)
        return deduped

    if session is not None:
        row = get_project_secret_lifecycle(session, project.id, secret_name, create=False)
        return _build(row)

    with Session(ensure_engine()) as direct_session:
        row = get_project_secret_lifecycle(direct_session, project.id, secret_name, create=False)
        return _build(row)


def touch_project_secret_last_used(
    project_id: int,
    secret_name: str,
    *,
    session: Optional[Session] = None,
    used_at: Optional[datetime] = None,
    min_interval_seconds: int = 60,
) -> None:
    when = used_at or utcnow()

    def _touch(row_session: Session) -> None:
        row = get_project_secret_lifecycle(row_session, project_id, secret_name, create=True)
        last_used_at = normalize_utc_naive(row.last_used_at)
        if last_used_at and (when - last_used_at).total_seconds() < max(0, int(min_interval_seconds)):
            return
        row.last_used_at = when
        row.updated_at = when
        row_session.add(row)
        row_session.commit()

    if session is not None:
        row = get_project_secret_lifecycle(session, project_id, secret_name, create=True)
        last_used_at = normalize_utc_naive(row.last_used_at)
        if last_used_at and (when - last_used_at).total_seconds() < max(0, int(min_interval_seconds)):
            return
        row.last_used_at = when
        row.updated_at = when
        session.add(row)
        return

    with Session(ensure_engine()) as direct_session:
        _touch(direct_session)


def project_secret_lifecycle_payload(session: Session, project, secret_name: str) -> dict:
    row = get_project_secret_lifecycle(session, project.id, secret_name, create=False)
    when = utcnow()
    due_at = rotation_due_at(getattr(row, "last_rotated_at", None), getattr(row, "rotation_interval_days", None)) if row else None
    rollover_active_until = None
    previous_secret_expires_at = normalize_utc_naive(getattr(row, "previous_secret_expires_at", None)) if row else None
    if row and row.previous_secret_value and previous_secret_expires_at and previous_secret_expires_at > when:
        rollover_active_until = previous_secret_expires_at
    expires_at = normalize_utc_naive(getattr(row, "expires_at", None)) if row else None
    return {
        "last_used_at": getattr(row, "last_used_at", None) if row else None,
        "expires_at": expires_at,
        "last_rotated_at": getattr(row, "last_rotated_at", None) if row else None,
        "rotation_interval_days": getattr(row, "rotation_interval_days", None) if row else None,
        "rotation_due_at": due_at,
        "rotation_required": bool((expires_at and expires_at <= when) or (due_at and due_at <= when)),
        "rollover_active_until": rollover_active_until,
        "usable_now": bool(active_project_secret_candidates(project, secret_name, session=session, now=when)),
    }
