import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import HTTPException, Request
from sqlmodel import Session, select

from . import db as dbmod
from .models import AuditLog

SECRET_ROTATION_ACTIONS = {
    "rotate_project_key",
    "rotate_primary_api_key",
    "rotate_project_key_admin",
    "rotate_all_keys",
    "update_project_webhooks",
    "set_project_alert_settings",
    "set_project_pagerduty_settings",
    "set_project_jira_settings",
}
TOKEN_LIFECYCLE_ACTIONS = {
    "create_apikey",
    "revoke_apikey",
    "create_scoped_project_token",
    "revoke_scoped_project_token",
}
WEBHOOK_FAILURE_ACTIONS = {
    "notification_failed",
    "notification_retry_failed",
    "webhook_missing_secret",
    "webhook_invalid_secret",
    "webhook_missing_timestamp",
    "webhook_missing_signature",
    "webhook_invalid_signature",
    "webhook_replay_window",
}
AUTH_FAILURE_PREFIX = "auth_"
MAX_SECTION_ROWS = 25
_PROJECT_ID_PATTERNS = (
    re.compile(r"^/projects/(?P<project_id>\d+)(?:/|$)"),
    re.compile(r"^/ui/projects/(?P<project_id>\d+)(?:/|$)"),
)


def _client_ip(request: Request) -> Optional[str]:
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip.strip()
    return request.client.host if request.client else None


def _extract_project_id(path: str) -> Optional[int]:
    for pattern in _PROJECT_ID_PATTERNS:
        match = pattern.match(path or "")
        if match:
            try:
                return int(match.group("project_id"))
            except Exception:
                return None
    return None


def _to_detail_text(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        nested = detail.get("detail")
        if isinstance(nested, str):
            return nested
        return json.dumps(detail, sort_keys=True)
    if isinstance(detail, list):
        return json.dumps(detail, sort_keys=True)
    return str(detail)


def _load_json(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _details_preview(row: AuditLog) -> str:
    details = _load_json(row.details)
    if details:
        for key in ("path", "reason", "source", "channel", "target", "message"):
            value = details.get(key)
            if value:
                return str(value)
        return json.dumps(details, sort_keys=True)
    return row.details or ""


def _serialize_row(row: AuditLog) -> dict:
    details = _load_json(row.details)
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "actor": row.actor,
        "action": row.action,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "project_id": row.project_id,
        "org_id": row.org_id,
        "team_id": row.team_id,
        "actor_ip": row.actor_ip,
        "user_agent": row.user_agent,
        "details": details or (row.details or ""),
        "details_preview": _details_preview(row),
    }


def _is_auth_failure(action: str) -> bool:
    return bool(action) and action.startswith(AUTH_FAILURE_PREFIX)


def _is_webhook_failure(action: str) -> bool:
    return action in WEBHOOK_FAILURE_ACTIONS


def _classify_http_exception(request: Request, exc: HTTPException) -> Optional[dict]:
    if exc.status_code not in {401, 403}:
        return None

    path = request.url.path
    detail_text = _to_detail_text(exc.detail)

    if path == "/users/login" and detail_text == "Invalid credentials":
        return {"action": "auth_invalid_credentials", "target_type": "auth", "source": "login"}
    if detail_text == "Missing token":
        return {"action": "auth_missing_token", "target_type": "auth", "source": "user_token"}
    if detail_text == "Invalid token":
        return {"action": "auth_invalid_token", "target_type": "auth", "source": "user_token"}
    if detail_text == "Token expired":
        return {"action": "auth_expired_token", "target_type": "auth", "source": "user_token"}
    if detail_text == "Missing API key":
        return {"action": "auth_missing_api_key", "target_type": "auth", "source": "project_api_key"}
    if detail_text == "Invalid API key":
        return {"action": "auth_invalid_api_key", "target_type": "auth", "source": "project_api_key"}
    if detail_text == "Missing credentials":
        return {"action": "auth_missing_credentials", "target_type": "auth", "source": "mixed_auth"}
    if detail_text == "Admin token required":
        return {"action": "auth_admin_token_required", "target_type": "auth", "source": "admin_token"}
    if detail_text in {"Missing PagerDuty webhook secret", "Missing Jira webhook secret"}:
        return {"action": "webhook_missing_secret", "target_type": "webhook", "source": path}
    if detail_text in {"Invalid PagerDuty webhook secret", "Invalid Jira webhook secret"}:
        return {"action": "webhook_invalid_secret", "target_type": "webhook", "source": path}
    if detail_text.startswith("Missing signed ") and "webhook headers" in detail_text:
        return {"action": "webhook_missing_signature", "target_type": "webhook", "source": path}
    if "Missing webhook timestamp" in detail_text:
        return {"action": "webhook_missing_timestamp", "target_type": "webhook", "source": path}
    if "Missing webhook signature" in detail_text:
        return {"action": "webhook_missing_signature", "target_type": "webhook", "source": path}
    if "Invalid webhook signature" in detail_text or "sha256 hex digest" in detail_text:
        return {"action": "webhook_invalid_signature", "target_type": "webhook", "source": path}
    if "outside the replay window" in detail_text:
        return {"action": "webhook_replay_window", "target_type": "webhook", "source": path}
    return None


def audit_http_exception(request: Request, exc: HTTPException) -> None:
    spec = _classify_http_exception(request, exc)
    if spec is None:
        return

    details = {
        "path": request.url.path,
        "method": request.method,
        "detail": _to_detail_text(exc.detail),
        "reason": spec["source"],
    }
    project_id = _extract_project_id(request.url.path)
    try:
        with Session(dbmod.ensure_engine()) as session:
            session.add(
                AuditLog(
                    actor="security",
                    action=spec["action"],
                    target_type=spec["target_type"],
                    target_id=project_id,
                    project_id=project_id,
                    details=json.dumps(details, sort_keys=True),
                    actor_ip=_client_ip(request),
                    user_agent=request.headers.get("user-agent"),
                )
            )
            session.commit()
    except Exception:
        return


def build_security_summary(
    session: Session,
    *,
    hours: int = 168,
    project_id: Optional[int] = None,
    limit: int = MAX_SECTION_ROWS,
) -> dict:
    safe_hours = max(1, min(int(hours or 168), 24 * 365))
    safe_limit = max(1, min(int(limit or MAX_SECTION_ROWS), 100))
    cutoff = datetime.utcnow() - timedelta(hours=safe_hours)

    stmt = select(AuditLog).where(AuditLog.created_at >= cutoff)
    if project_id is not None:
        stmt = stmt.where(AuditLog.project_id == project_id)
    rows = session.exec(stmt.order_by(AuditLog.created_at.desc())).all()

    secret_rows = [row for row in rows if row.action in SECRET_ROTATION_ACTIONS]
    token_rows = [row for row in rows if row.action in TOKEN_LIFECYCLE_ACTIONS]
    webhook_rows = [row for row in rows if _is_webhook_failure(row.action)]
    admin_rows = [row for row in rows if row.actor == "admin"]
    auth_rows = [row for row in rows if _is_auth_failure(row.action)]

    patterns: dict[str, dict] = {}
    for row in auth_rows:
        ip = row.actor_ip or "unknown"
        entry = patterns.setdefault(
            ip,
            {
                "actor_ip": ip,
                "count": 0,
                "last_seen_at": None,
                "actions": {},
                "paths": [],
            },
        )
        entry["count"] += 1
        entry["actions"][row.action] = entry["actions"].get(row.action, 0) + 1
        if row.created_at and (entry["last_seen_at"] is None or row.created_at.isoformat() > entry["last_seen_at"]):
            entry["last_seen_at"] = row.created_at.isoformat()
        path = _load_json(row.details).get("path")
        if path and path not in entry["paths"]:
            entry["paths"].append(path)
    suspicious_auth_patterns = sorted(patterns.values(), key=lambda item: (-item["count"], item["actor_ip"]))[:safe_limit]

    return {
        "window": {
            "hours": safe_hours,
            "since": cutoff.isoformat(),
            "project_id": project_id,
        },
        "counts": {
            "secret_changes": len(secret_rows),
            "token_events": len(token_rows),
            "webhook_failures": len(webhook_rows),
            "admin_actions": len(admin_rows),
            "suspicious_auth_events": len(auth_rows),
            "suspicious_auth_patterns": len(suspicious_auth_patterns),
        },
        "secret_changes": [_serialize_row(row) for row in secret_rows[:safe_limit]],
        "token_events": [_serialize_row(row) for row in token_rows[:safe_limit]],
        "webhook_failures": [_serialize_row(row) for row in webhook_rows[:safe_limit]],
        "admin_actions": [_serialize_row(row) for row in admin_rows[:safe_limit]],
        "suspicious_auth_events": [_serialize_row(row) for row in auth_rows[:safe_limit]],
        "suspicious_auth_patterns": suspicious_auth_patterns,
        "recent_events": [_serialize_row(row) for row in rows[:safe_limit]],
    }
