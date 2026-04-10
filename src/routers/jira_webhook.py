import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlmodel import Session, select

from ..db import get_session
from ..models import AuditLog, Incident, IncidentNote
from ..webhook_security import parse_signed_json_body, register_webhook_receipt, verify_signed_webhook_request

router = APIRouter(prefix="/integrations/jira", tags=["jira"])


def _nested(data: Any, *path: str) -> Any:
    cur = data
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _first_nonempty(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _event_type(payload: dict) -> str:
    return (_first_nonempty(payload.get("webhookEvent"), payload.get("issue_event_type")) or "").lower()


def _issue_key(payload: dict) -> Optional[str]:
    return _first_nonempty(_nested(payload, "issue", "key"), payload.get("issue_key"))


def _actor_label(payload: dict) -> str:
    user = payload.get("user")
    if isinstance(user, dict):
        return _first_nonempty(user.get("displayName"), user.get("emailAddress"), user.get("accountId")) or "jira"
    return "jira"


def _assignee_label(payload: dict) -> Optional[str]:
    assignee = _nested(payload, "issue", "fields", "assignee")
    if isinstance(assignee, dict):
        return _first_nonempty(assignee.get("displayName"), assignee.get("emailAddress"), assignee.get("accountId"))
    return None


def _comment_text(payload: dict) -> Optional[str]:
    comment = payload.get("comment")
    if not isinstance(comment, dict):
        return None
    body = comment.get("body")
    if isinstance(body, str) and body.strip():
        return body.strip()
    if isinstance(body, dict):
        texts: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                text = node.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text.strip())
                content = node.get("content")
                if isinstance(content, list):
                    for child in content:
                        walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(body)
        joined = " ".join(texts).strip()
        return joined or None
    return None


def _status_done(payload: dict) -> Optional[bool]:
    category = _nested(payload, "issue", "fields", "status", "statusCategory")
    if isinstance(category, dict):
        key = (_first_nonempty(category.get("key"), category.get("name")) or "").lower()
        if not key:
            return None
        return key == "done"
    return None


def _changed_fields(payload: dict) -> set[str]:
    items = _nested(payload, "changelog", "items")
    names: set[str] = set()
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                field = _first_nonempty(item.get("field"), item.get("fieldId"))
                if field:
                    names.add(field.lower())
    return names


def _record_sync_audit(
    session: Session,
    *,
    incident: Incident,
    action: str,
    actor: str,
    raw_event_type: str,
    details: dict,
) -> None:
    session.add(
        AuditLog(
            actor=f"jira:{actor}",
            action=f"jira_{action}",
            target_type="incident",
            target_id=incident.id,
            project_id=incident.project_id,
            details=json.dumps({"source": "jira", "raw_event_type": raw_event_type, **details}),
        )
    )


def _apply_sync_event(session: Session, incident: Incident, payload: dict) -> int:
    raw_event_type = _event_type(payload)
    actor = _actor_label(payload)
    when = _parse_dt(_first_nonempty(payload.get("timestamp"), payload.get("issue", {}).get("fields", {}).get("updated"))) or datetime.utcnow()
    changed = 0
    changed_fields = _changed_fields(payload)

    comment_text = _comment_text(payload)
    if comment_text and raw_event_type in {"comment_created", "comment_updated"}:
        session.add(
            IncidentNote(
                incident_id=incident.id,
                project_id=incident.project_id,
                author=f"jira:{actor}",
                body=comment_text,
                created_at=when,
            )
        )
        _record_sync_audit(
            session,
            incident=incident,
            action="note",
            actor=actor,
            raw_event_type=raw_event_type,
            details={"note_len": len(comment_text)},
        )
        changed += 1

    assignee = _assignee_label(payload)
    if assignee and (raw_event_type in {"jira:issue_updated", "jira:issue_created"} or "assignee" in changed_fields):
        if incident.owner != assignee:
            incident.owner = assignee
            session.add(incident)
            changed += 1
        _record_sync_audit(
            session,
            incident=incident,
            action="assign",
            actor=actor,
            raw_event_type=raw_event_type,
            details={"owner": assignee},
        )

    done = _status_done(payload)
    if done is True and (raw_event_type in {"jira:issue_updated", "jira:issue_created"} or "status" in changed_fields):
        if incident.resolved_at is None or incident.status != "resolved":
            incident.resolved_at = when
            incident.status = "resolved"
            session.add(incident)
            changed += 1
        _record_sync_audit(
            session,
            incident=incident,
            action="resolve",
            actor=actor,
            raw_event_type=raw_event_type,
            details={"resolved_at": when.isoformat()},
        )
    elif done is False and (raw_event_type in {"jira:issue_updated", "jira:issue_created"} or "status" in changed_fields):
        if incident.resolved_at is not None or incident.status != "open":
            incident.resolved_at = None
            incident.status = "open"
            session.add(incident)
            changed += 1
        _record_sync_audit(
            session,
            incident=incident,
            action="reopen",
            actor=actor,
            raw_event_type=raw_event_type,
            details={},
        )

    return changed


@router.post("/webhook")
async def receive_jira_webhook(
    request: Request,
    x_jira_webhook_secret: Optional[str] = Header(None),
    x_jira_webhook_timestamp: Optional[str] = Header(None),
    x_jira_webhook_signature: Optional[str] = Header(None),
    x_lastping_webhook_secret: Optional[str] = Header(None),
    x_lastping_webhook_timestamp: Optional[str] = Header(None),
    x_lastping_webhook_signature: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    configured_secret = os.environ.get("JIRA_WEBHOOK_SECRET")
    if not configured_secret:
        raise HTTPException(status_code=503, detail="Jira webhook sync is not configured")

    raw_body = await request.body()
    payload = parse_signed_json_body(raw_body)
    signed_timestamp = x_jira_webhook_timestamp or x_lastping_webhook_timestamp
    signed_signature = x_jira_webhook_signature or x_lastping_webhook_signature
    if signed_timestamp or signed_signature:
        request_timestamp, normalized_signature = verify_signed_webhook_request(
            source="Jira",
            secret=configured_secret,
            timestamp=signed_timestamp,
            signature=signed_signature,
            raw_body=raw_body,
        )
        if not register_webhook_receipt(
            session,
            source="jira",
            signature=normalized_signature,
            request_timestamp=request_timestamp,
        ):
            return {"accepted": True, "processed": 0, "changed": 0, "ignored": 0, "replayed": 1}
    else:
        legacy_secret = x_jira_webhook_secret or x_lastping_webhook_secret
        if not legacy_secret:
            raise HTTPException(status_code=401, detail="Missing Jira webhook secret")
        if legacy_secret != configured_secret:
            raise HTTPException(status_code=403, detail="Invalid Jira webhook secret")

    issue_key = _issue_key(payload)
    if not issue_key:
        raise HTTPException(status_code=400, detail="No Jira issue key found")

    incident = session.exec(select(Incident).where(Incident.jira_issue_key == issue_key)).first()
    if incident is None:
        return {"accepted": True, "processed": 0, "changed": 0, "ignored": 1}

    changed = _apply_sync_event(session, incident, payload)
    session.commit()
    return {"accepted": True, "processed": 1, "changed": changed, "ignored": 0}
