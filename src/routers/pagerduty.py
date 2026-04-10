import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlmodel import Session, select

from ..db import get_session
from ..deps import limit_webhook_requests
from ..models import AuditLog, Incident, IncidentNote
from ..webhook_security import parse_signed_json_body, register_webhook_receipt, verify_signed_webhook_request

router = APIRouter(prefix="/integrations/pagerduty", tags=["pagerduty"])


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


def _event_list(payload: dict) -> list[dict]:
    if isinstance(payload.get("messages"), list):
        return [item for item in payload["messages"] if isinstance(item, dict)]
    if isinstance(payload.get("events"), list):
        return [item for item in payload["events"] if isinstance(item, dict)]
    if isinstance(payload.get("event"), dict):
        return [payload["event"]]
    return [payload] if isinstance(payload, dict) and payload else []


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


def _dedup_key(event: dict) -> Optional[str]:
    alerts = _nested(event, "data", "alerts")
    first_alert = alerts[0] if isinstance(alerts, list) and alerts and isinstance(alerts[0], dict) else {}
    body_details = _nested(event, "data", "body", "details")
    if not isinstance(body_details, dict):
        body_details = {}
    custom_details = _nested(event, "data", "custom_details")
    if not isinstance(custom_details, dict):
        custom_details = {}
    return _first_nonempty(
        event.get("dedup_key"),
        _nested(event, "data", "dedup_key"),
        _nested(event, "incident", "dedup_key"),
        _nested(event, "data", "incident", "dedup_key"),
        _nested(event, "data", "alert", "dedup_key"),
        first_alert.get("dedup_key"),
        body_details.get("dedup_key"),
        custom_details.get("dedup_key"),
    )


def _agent_label(event: dict) -> str:
    agent = event.get("agent")
    if isinstance(agent, dict):
        return _first_nonempty(agent.get("summary"), agent.get("name"), agent.get("email")) or "pagerduty"
    return "pagerduty"


def _event_type(event: dict) -> str:
    return (
        _first_nonempty(
            event.get("event_type"),
            event.get("type"),
            _nested(event, "event", "event_type"),
        )
        or ""
    ).lower()


def _assignee_label(event: dict) -> Optional[str]:
    assignees = _nested(event, "data", "assignees")
    if isinstance(assignees, list):
        labels = []
        for assignee in assignees:
            if isinstance(assignee, dict):
                label = _first_nonempty(assignee.get("summary"), assignee.get("name"), assignee.get("email"))
                if label:
                    labels.append(label)
        if labels:
            return ", ".join(labels)
    return _first_nonempty(
        _nested(event, "data", "assignee", "summary"),
        _nested(event, "data", "assignee", "name"),
        _nested(event, "data", "assigned_to_user", "summary"),
    )


def _annotation_text(event: dict) -> str:
    body = _nested(event, "data", "body")
    details = _nested(event, "data", "body", "details")
    if isinstance(details, str) and details.strip():
        return details.strip()
    if isinstance(details, dict):
        text = _first_nonempty(details.get("summary"), details.get("message"), details.get("note"))
        if text:
            return text
    if isinstance(body, dict):
        text = _first_nonempty(body.get("summary"), body.get("details"), body.get("message"))
        if text:
            return text
    return "PagerDuty annotation synced into LastPing."


def _action_from_event_type(event_type: str) -> Optional[str]:
    normalized = event_type.lower()
    if "unack" in normalized:
        return "clear_ack"
    if "ack" in normalized:
        return "ack"
    if "resolve" in normalized:
        return "resolve"
    if "reopen" in normalized or "trigger" in normalized:
        return "reopen"
    if "annotat" in normalized or normalized.endswith(".note"):
        return "note"
    if "reassign" in normalized or normalized.endswith(".assign") or "assigned" in normalized:
        return "assign"
    return None


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
            actor=f"pagerduty:{actor}",
            action=f"pagerduty_{action}",
            target_type="incident",
            target_id=incident.id,
            project_id=incident.project_id,
            details=json.dumps(
                {
                    "source": "pagerduty",
                    "raw_event_type": raw_event_type,
                    **details,
                }
            ),
        )
    )


def _apply_sync_event(session: Session, incident: Incident, event: dict) -> bool:
    raw_event_type = _event_type(event)
    action = _action_from_event_type(raw_event_type)
    if not action:
        return False

    actor = _agent_label(event)
    when = _parse_dt(_first_nonempty(event.get("occurred_at"), event.get("created_at"))) or datetime.utcnow()
    changed = False

    if action == "ack":
        if incident.acknowledged_at is None or incident.acknowledged_by != f"pagerduty:{actor}":
            incident.acknowledged_at = when
            incident.acknowledged_by = f"pagerduty:{actor}"
            session.add(incident)
            changed = True
        _record_sync_audit(
            session,
            incident=incident,
            action=action,
            actor=actor,
            raw_event_type=raw_event_type,
            details={"acknowledged_at": when.isoformat()},
        )
        return changed

    if action == "clear_ack":
        if incident.acknowledged_at is not None or incident.acknowledged_by is not None:
            incident.acknowledged_at = None
            incident.acknowledged_by = None
            session.add(incident)
            changed = True
        _record_sync_audit(
            session,
            incident=incident,
            action=action,
            actor=actor,
            raw_event_type=raw_event_type,
            details={},
        )
        return changed

    if action == "resolve":
        if incident.resolved_at is None or incident.status != "resolved":
            incident.resolved_at = when
            incident.status = "resolved"
            session.add(incident)
            changed = True
        _record_sync_audit(
            session,
            incident=incident,
            action=action,
            actor=actor,
            raw_event_type=raw_event_type,
            details={"resolved_at": when.isoformat()},
        )
        return changed

    if action == "reopen":
        if incident.resolved_at is not None or incident.status != "open":
            incident.resolved_at = None
            incident.status = "open"
            session.add(incident)
            changed = True
        _record_sync_audit(
            session,
            incident=incident,
            action=action,
            actor=actor,
            raw_event_type=raw_event_type,
            details={},
        )
        return changed

    if action == "assign":
        owner = _assignee_label(event)
        if owner and incident.owner != owner:
            incident.owner = owner
            session.add(incident)
            changed = True
        _record_sync_audit(
            session,
            incident=incident,
            action=action,
            actor=actor,
            raw_event_type=raw_event_type,
            details={"owner": owner},
        )
        return changed

    if action == "note":
        note = IncidentNote(
            incident_id=incident.id,
            project_id=incident.project_id,
            author=f"pagerduty:{actor}",
            body=_annotation_text(event),
            created_at=when,
        )
        session.add(note)
        changed = True
        _record_sync_audit(
            session,
            incident=incident,
            action=action,
            actor=actor,
            raw_event_type=raw_event_type,
            details={"note_len": len(note.body)},
        )
        return changed

    return False


@router.post("/webhook")
async def receive_pagerduty_webhook(
    request: Request,
    x_pagerduty_webhook_secret: Optional[str] = Header(None),
    x_pagerduty_webhook_timestamp: Optional[str] = Header(None),
    x_pagerduty_webhook_signature: Optional[str] = Header(None),
    x_lastping_webhook_secret: Optional[str] = Header(None),
    x_lastping_webhook_timestamp: Optional[str] = Header(None),
    x_lastping_webhook_signature: Optional[str] = Header(None),
    _scope = Depends(limit_webhook_requests),
    session: Session = Depends(get_session),
):
    configured_secret = os.environ.get("PAGERDUTY_WEBHOOK_SECRET")
    if not configured_secret:
        raise HTTPException(status_code=503, detail="PagerDuty webhook sync is not configured")

    raw_body = await request.body()
    payload = parse_signed_json_body(raw_body)
    signed_timestamp = x_pagerduty_webhook_timestamp or x_lastping_webhook_timestamp
    signed_signature = x_pagerduty_webhook_signature or x_lastping_webhook_signature
    if signed_timestamp or signed_signature:
        request_timestamp, normalized_signature = verify_signed_webhook_request(
            source="PagerDuty",
            secret=configured_secret,
            timestamp=signed_timestamp,
            signature=signed_signature,
            raw_body=raw_body,
        )
        if not register_webhook_receipt(
            session,
            source="pagerduty",
            signature=normalized_signature,
            request_timestamp=request_timestamp,
        ):
            return {"accepted": True, "processed": 0, "changed": 0, "ignored": 0, "replayed": 1}
    else:
        legacy_secret = x_pagerduty_webhook_secret or x_lastping_webhook_secret
        if not legacy_secret:
            raise HTTPException(status_code=401, detail="Missing PagerDuty webhook secret")
        if legacy_secret != configured_secret:
            raise HTTPException(status_code=403, detail="Invalid PagerDuty webhook secret")

    events = _event_list(payload)
    if not events:
        raise HTTPException(status_code=400, detail="No PagerDuty events found")

    processed = 0
    ignored = 0
    changed = 0
    for event in events:
        dedup_key = _dedup_key(event)
        if not dedup_key:
            ignored += 1
            continue
        incident = session.exec(select(Incident).where(Incident.pagerduty_dedup_key == dedup_key)).first()
        if incident is None:
            ignored += 1
            continue
        processed += 1
        if _apply_sync_event(session, incident, event):
            changed += 1

    session.commit()
    return {"accepted": True, "processed": processed, "changed": changed, "ignored": ignored}
