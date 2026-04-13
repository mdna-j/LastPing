import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import update
from sqlmodel import Session, select

from .models import AuditLog, Check, Incident, NotificationDelivery, Project

logger = logging.getLogger("lastping.notification_queue")

STATUS_QUEUED = "queued"
STATUS_RETRY = "retry"
STATUS_PROCESSING = "processing"
STATUS_DELIVERED = "delivered"
STATUS_DEAD = "dead"

KIND_DISCORD = "discord"
KIND_SLACK = "slack"
KIND_PAGERDUTY = "pagerduty"
KIND_PROJECT_WEBHOOK = "project_webhook"
KIND_EMAIL = "email"
KIND_JIRA_TICKET = "jira_ticket"


def _now() -> datetime:
    return datetime.utcnow()


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, sort_keys=True, default=str)


def _json_loads(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _worker_identity(default: str = "worker") -> str:
    host = (os.environ.get("HOSTNAME") or os.environ.get("COMPUTERNAME") or "").strip()
    region = (os.environ.get("WORKER_REGION") or "").strip()
    if host and region:
        return f"{host}:{region}"
    if host:
        return host
    if region:
        return f"{default}:{region}"
    return default


def _retry_delay_seconds(attempt_count: int) -> int:
    base = max(5, int(os.environ.get("NOTIFICATION_QUEUE_BACKOFF_BASE_SECONDS", "30")))
    maximum = max(base, int(os.environ.get("NOTIFICATION_QUEUE_BACKOFF_MAX_SECONDS", "3600")))
    exponent = max(0, int(attempt_count) - 1)
    return min(base * (2 ** exponent), maximum)


def _safe_error_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:1000]


def enqueue_notification_delivery(
    session: Session,
    *,
    project_id: int,
    channel: str,
    event: str,
    request_kind: str,
    payload: dict[str, Any],
    target: Optional[str] = None,
    check_id: Optional[int] = None,
    incident_id: Optional[int] = None,
    subscription_id: Optional[int] = None,
    max_attempts: Optional[int] = None,
    next_attempt_at: Optional[datetime] = None,
) -> NotificationDelivery:
    delivery = NotificationDelivery(
        project_id=project_id,
        check_id=check_id,
        incident_id=incident_id,
        subscription_id=subscription_id,
        channel=channel,
        event=event,
        request_kind=request_kind,
        target=target,
        payload_json=_json_dumps(payload),
        status=STATUS_QUEUED,
        attempt_count=0,
        max_attempts=max(1, int(max_attempts or os.environ.get("NOTIFICATION_QUEUE_MAX_ATTEMPTS", "5"))),
        next_attempt_at=next_attempt_at or _now(),
        created_at=_now(),
        updated_at=_now(),
    )
    session.add(delivery)
    session.flush()
    return delivery


def queue_email_delivery(
    session: Session,
    *,
    project_id: int,
    subject: str,
    body: str,
    to: str,
    event: str,
    check_id: Optional[int] = None,
    incident_id: Optional[int] = None,
    target: Optional[str] = None,
    max_attempts: Optional[int] = None,
) -> NotificationDelivery:
    return enqueue_notification_delivery(
        session,
        project_id=project_id,
        check_id=check_id,
        incident_id=incident_id,
        channel="email",
        event=event,
        request_kind=KIND_EMAIL,
        target=target or to,
        payload={"subject": subject, "body": body, "to": to},
        max_attempts=max_attempts,
    )


def queue_discord_delivery(
    session: Session,
    *,
    project_id: int,
    event: str,
    payload: dict[str, Any],
    check_id: Optional[int] = None,
    incident_id: Optional[int] = None,
    target: Optional[str] = None,
    max_attempts: Optional[int] = None,
) -> NotificationDelivery:
    return enqueue_notification_delivery(
        session,
        project_id=project_id,
        check_id=check_id,
        incident_id=incident_id,
        channel="discord",
        event=event,
        request_kind=KIND_DISCORD,
        target=target or "discord route",
        payload={"payload": payload},
        max_attempts=max_attempts,
    )


def queue_slack_delivery(
    session: Session,
    *,
    project_id: int,
    event: str,
    payload: dict[str, Any],
    fallback_text: str,
    check_id: Optional[int] = None,
    incident_id: Optional[int] = None,
    target: Optional[str] = None,
    max_attempts: Optional[int] = None,
) -> NotificationDelivery:
    return enqueue_notification_delivery(
        session,
        project_id=project_id,
        check_id=check_id,
        incident_id=incident_id,
        channel="slack",
        event=event,
        request_kind=KIND_SLACK,
        target=target or "slack route",
        payload={"payload": payload, "fallback_text": fallback_text},
        max_attempts=max_attempts,
    )


def queue_pagerduty_delivery(
    session: Session,
    *,
    project_id: int,
    event: str,
    summary: str,
    severity: str,
    event_action: str,
    check_id: Optional[int] = None,
    incident_id: Optional[int] = None,
    source: Optional[str] = None,
    component: Optional[str] = None,
    custom_details: Optional[dict[str, Any]] = None,
    dedup_key: Optional[str] = None,
    target: Optional[str] = None,
    max_attempts: Optional[int] = None,
) -> NotificationDelivery:
    return enqueue_notification_delivery(
        session,
        project_id=project_id,
        check_id=check_id,
        incident_id=incident_id,
        channel="pagerduty",
        event=event,
        request_kind=KIND_PAGERDUTY,
        target=target or "pagerduty integration",
        payload={
            "summary": summary,
            "severity": severity,
            "event_action": event_action,
            "source": source,
            "component": component,
            "custom_details": custom_details or {},
            "dedup_key": dedup_key,
        },
        max_attempts=max_attempts,
    )


def queue_project_webhook_delivery(
    session: Session,
    *,
    project_id: int,
    event: str,
    payload: dict[str, Any],
    check_id: Optional[int] = None,
    incident_id: Optional[int] = None,
    target: Optional[str] = None,
    max_attempts: Optional[int] = None,
) -> NotificationDelivery:
    return enqueue_notification_delivery(
        session,
        project_id=project_id,
        check_id=check_id,
        incident_id=incident_id,
        channel="webhook",
        event=event,
        request_kind=KIND_PROJECT_WEBHOOK,
        target=target or "project webhook",
        payload={"payload": payload},
        max_attempts=max_attempts,
    )


def queue_jira_ticket_delivery(
    session: Session,
    *,
    project_id: int,
    incident_id: int,
    summary: str,
    description: str,
    labels: list[str],
    issue_type: Optional[str] = None,
    check_id: Optional[int] = None,
    target: Optional[str] = None,
    audit_actor: Optional[str] = None,
    audit_actor_ip: Optional[str] = None,
    audit_user_agent: Optional[str] = None,
    max_attempts: Optional[int] = None,
) -> NotificationDelivery:
    return enqueue_notification_delivery(
        session,
        project_id=project_id,
        check_id=check_id,
        incident_id=incident_id,
        channel="jira",
        event="jira_ticket",
        request_kind=KIND_JIRA_TICKET,
        target=target or "jira project",
        payload={
            "summary": summary,
            "description": description,
            "labels": labels,
            "issue_type": issue_type,
            "audit_actor": audit_actor,
            "audit_actor_ip": audit_actor_ip,
            "audit_user_agent": audit_user_agent,
        },
        max_attempts=max_attempts,
    )


def recover_stuck_notification_deliveries(session: Session, *, now: Optional[datetime] = None) -> int:
    now = now or _now()
    timeout_seconds = max(60, int(os.environ.get("NOTIFICATION_QUEUE_PROCESSING_TIMEOUT_SECONDS", "300")))
    cutoff = now - timedelta(seconds=timeout_seconds)
    result = session.exec(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.status == STATUS_PROCESSING,
            NotificationDelivery.claimed_at != None,
            NotificationDelivery.claimed_at <= cutoff,
        )
        .values(
            status=STATUS_RETRY,
            next_attempt_at=now,
            claimed_by=None,
            claimed_at=None,
            updated_at=now,
            last_error="Processing timeout exceeded; re-queued",
        )
    )
    count = max(0, int(getattr(result, "rowcount", 0) or 0))
    if count:
        session.commit()
    return count


def claim_due_notification_deliveries(
    session: Session,
    *,
    now: Optional[datetime] = None,
    limit: Optional[int] = None,
    worker_id: Optional[str] = None,
) -> list[NotificationDelivery]:
    now = now or _now()
    worker_id = worker_id or _worker_identity()
    recover_stuck_notification_deliveries(session, now=now)
    batch_limit = max(1, int(limit or os.environ.get("NOTIFICATION_QUEUE_BATCH_SIZE", "25")))
    due_ids = session.exec(
        select(NotificationDelivery.id)
        .where(
            NotificationDelivery.status.in_([STATUS_QUEUED, STATUS_RETRY]),
            NotificationDelivery.next_attempt_at <= now,
        )
        .order_by(NotificationDelivery.next_attempt_at.asc(), NotificationDelivery.id.asc())
        .limit(batch_limit)
    ).all()
    claimed_ids: list[int] = []
    for delivery_id in due_ids:
        result = session.exec(
            update(NotificationDelivery)
            .where(
                NotificationDelivery.id == delivery_id,
                NotificationDelivery.status.in_([STATUS_QUEUED, STATUS_RETRY]),
                NotificationDelivery.next_attempt_at <= now,
            )
            .values(
                status=STATUS_PROCESSING,
                claimed_by=worker_id,
                claimed_at=now,
                updated_at=now,
            )
        )
        if int(getattr(result, "rowcount", 0) or 0) == 1:
            claimed_ids.append(int(delivery_id))
    if not claimed_ids:
        return []
    session.commit()
    return session.exec(
        select(NotificationDelivery)
        .where(NotificationDelivery.id.in_(claimed_ids))
        .order_by(NotificationDelivery.next_attempt_at.asc(), NotificationDelivery.id.asc())
    ).all()


def _delivery_retryable(delivery: NotificationDelivery) -> bool:
    return delivery.request_kind in {
        KIND_DISCORD,
        KIND_SLACK,
        KIND_PAGERDUTY,
        KIND_PROJECT_WEBHOOK,
        KIND_EMAIL,
        KIND_JIRA_TICKET,
    }


def _record_dead_letter_audit(session: Session, delivery: NotificationDelivery) -> None:
    details = {
        "project_id": delivery.project_id,
        "check_id": delivery.check_id,
        "incident_id": delivery.incident_id,
        "subscription_id": delivery.subscription_id,
        "channel": delivery.channel,
        "event": delivery.event,
        "detail": delivery.last_error,
        "target": delivery.target,
        "request_kind": delivery.request_kind,
        "retryable": _delivery_retryable(delivery),
        "attempt_count": delivery.attempt_count,
        "status": delivery.status,
        "last_status_code": delivery.last_status_code,
        "recorded_at": _now().isoformat(),
        "notification_delivery_id": delivery.id,
    }
    session.add(
        AuditLog(
            actor="delivery_queue",
            action="notification_failed",
            target_type="project",
            target_id=delivery.project_id,
            project_id=delivery.project_id,
            details=_json_dumps(details),
            actor_ip=None,
            user_agent=None,
        )
    )
    session.add(
        AuditLog(
            actor="delivery_queue",
            action="notification_dead",
            target_type="notification_delivery",
            target_id=delivery.id,
            project_id=delivery.project_id,
            details=_json_dumps(details),
            actor_ip=None,
            user_agent=None,
        )
    )


def _mark_delivery_result(
    session: Session,
    delivery: NotificationDelivery,
    *,
    now: datetime,
    ok: bool,
    detail: Optional[str] = None,
    status_code: Optional[int] = None,
) -> dict[str, Any]:
    delivery.attempt_count = int(delivery.attempt_count or 0) + 1
    delivery.last_error = None if ok else _safe_error_text(detail)
    delivery.last_status_code = status_code
    delivery.claimed_by = None
    delivery.claimed_at = None
    delivery.updated_at = now
    if ok:
        delivery.status = STATUS_DELIVERED
        delivery.delivered_at = now
        delivery.dead_at = None
        delivery.next_attempt_at = now
    else:
        if delivery.attempt_count >= max(1, int(delivery.max_attempts or 1)):
            delivery.status = STATUS_DEAD
            delivery.dead_at = now
            delivery.next_attempt_at = now
            _record_dead_letter_audit(session, delivery)
        else:
            delivery.status = STATUS_RETRY
            delivery.dead_at = None
            delivery.next_attempt_at = now + timedelta(seconds=_retry_delay_seconds(delivery.attempt_count))
    session.add(delivery)
    return {
        "ok": ok,
        "status": status_code,
        "detail": delivery.last_error,
        "delivery_status": delivery.status,
        "attempt_count": delivery.attempt_count,
    }


def _load_delivery_scope(session: Session, delivery: NotificationDelivery) -> tuple[Project, Optional[Check], Optional[Incident]]:
    project = session.get(Project, delivery.project_id)
    if project is None:
        raise RuntimeError(f"Project {delivery.project_id} not found for notification delivery")
    check = session.get(Check, delivery.check_id) if delivery.check_id is not None else None
    incident = session.get(Incident, delivery.incident_id) if delivery.incident_id is not None else None
    return project, check, incident


def _execute_email_delivery(session: Session, delivery: NotificationDelivery, payload: dict[str, Any]) -> dict[str, Any]:
    from .alerts import send_email

    ok = send_email(
        str(payload.get("subject") or ""),
        str(payload.get("body") or ""),
        to=payload.get("to"),
    )
    return {"ok": ok, "status": 200 if ok else None, "detail": None if ok else "Email delivery failed"}


def _execute_discord_delivery(session: Session, delivery: NotificationDelivery, payload: dict[str, Any]) -> dict[str, Any]:
    from .alerts import _discord_url, _post_json_with_response

    project, check, _incident = _load_delivery_scope(session, delivery)
    url = _discord_url(project, check) or os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return {"ok": False, "detail": "Discord webhook is not configured"}
    response = _post_json_with_response(url, payload.get("payload") or {})
    ok = bool(response and response.get("ok"))
    return {
        "ok": ok,
        "status": (response or {}).get("status"),
        "detail": None if ok else ((response or {}).get("body") or "Discord delivery failed"),
    }


def _execute_project_webhook_delivery(session: Session, delivery: NotificationDelivery, payload: dict[str, Any]) -> dict[str, Any]:
    from .alerts import _generic_webhook_url, _post_json_with_response

    project, check, _incident = _load_delivery_scope(session, delivery)
    url = _generic_webhook_url(project, check)
    if not url:
        return {"ok": False, "detail": "Project webhook is not configured"}
    response = _post_json_with_response(url, payload.get("payload") or {})
    ok = bool(response and response.get("ok"))
    return {
        "ok": ok,
        "status": (response or {}).get("status"),
        "detail": None if ok else ((response or {}).get("body") or "Webhook delivery failed"),
    }


def _execute_slack_delivery(session: Session, delivery: NotificationDelivery, payload: dict[str, Any]) -> dict[str, Any]:
    from .alerts import _post_slack_message

    project, check, incident = _load_delivery_scope(session, delivery)
    ok, _target = _post_slack_message(
        project=project,
        check=check,
        incident=incident,
        session=session,
        payload=payload.get("payload") or {},
        fallback_text=str(payload.get("fallback_text") or delivery.event),
    )
    return {"ok": ok, "status": 200 if ok else None, "detail": None if ok else "Slack delivery failed"}


def _execute_pagerduty_delivery(session: Session, delivery: NotificationDelivery, payload: dict[str, Any]) -> dict[str, Any]:
    from .alerts import _pagerduty_dedup_key, _remember_incident_pagerduty_dedup_key, _send_pagerduty_event_for_project

    project, check, incident = _load_delivery_scope(session, delivery)
    dedup_key = payload.get("dedup_key") or _pagerduty_dedup_key(project, check, incident)
    if incident is not None and dedup_key:
        _remember_incident_pagerduty_dedup_key(incident, dedup_key=dedup_key, session=session)
    ok = _send_pagerduty_event_for_project(
        project,
        str(payload.get("summary") or ""),
        str(payload.get("severity") or "critical"),
        check=check,
        event_action=str(payload.get("event_action") or "trigger"),
        dedup_key=dedup_key,
        source=str(payload.get("source") or getattr(project, "name", "lastping")),
        component=payload.get("component") or getattr(check, "name", None),
        custom_details=payload.get("custom_details") or {},
    )
    return {"ok": ok, "status": 202 if ok else None, "detail": None if ok else "PagerDuty delivery failed"}


def _execute_jira_ticket_delivery(session: Session, delivery: NotificationDelivery, payload: dict[str, Any]) -> dict[str, Any]:
    from .alerts import notify_incident_slack_update
    from .jira import create_jira_issue
    from .secret_lifecycle import (
        SECRET_JIRA_API_TOKEN,
        active_project_secret_candidates,
        touch_project_secret_last_used,
    )

    project, check, incident = _load_delivery_scope(session, delivery)
    if incident is None:
        return {"ok": False, "detail": "Incident not found for Jira delivery"}
    if incident.jira_issue_key and incident.jira_issue_url:
        return {"ok": True, "status": 200, "detail": None}
    if not (project.jira_base_url and project.jira_user_email and project.jira_project_key):
        return {"ok": False, "detail": "Jira settings are incomplete for this project"}

    jira_tokens = active_project_secret_candidates(project, SECRET_JIRA_API_TOKEN, session=session)
    if not jira_tokens:
        return {"ok": False, "detail": "Jira API token is not configured"}

    issue_type = payload.get("issue_type") or project.jira_issue_type or "Task"
    result = None
    last_error: Optional[Exception] = None
    for jira_token in jira_tokens:
        try:
            result = create_jira_issue(
                base_url=project.jira_base_url or "",
                email=project.jira_user_email or "",
                api_token=jira_token or "",
                project_key=project.jira_project_key or "",
                issue_type=str(issue_type),
                summary=str(payload.get("summary") or ""),
                description=str(payload.get("description") or ""),
                labels=[str(label) for label in (payload.get("labels") or [])],
            )
            touch_project_secret_last_used(project.id, SECRET_JIRA_API_TOKEN, session=session)
            break
        except Exception as exc:
            last_error = exc

    if result is None:
        return {"ok": False, "detail": str(last_error or "Jira issue creation failed")}

    issue_key = result.get("key")
    issue_url = result.get("url")
    if not issue_key or not issue_url:
        return {"ok": False, "detail": "Jira issue creation did not return an issue key"}

    incident.jira_issue_key = issue_key
    incident.jira_issue_url = issue_url
    session.add(incident)
    session.add(
        AuditLog(
            actor=payload.get("audit_actor") or "delivery_queue",
            action="create_jira_ticket",
            target_type="incident",
            target_id=incident.id,
            project_id=incident.project_id,
            details=f"issue_key={issue_key}, issue_url={issue_url}",
            actor_ip=payload.get("audit_actor_ip"),
            user_agent=payload.get("audit_user_agent"),
        )
    )
    actor = payload.get("audit_actor")
    notify_incident_slack_update(
        project,
        incident,
        action="jira_ticket",
        body=f"Created Jira issue `{issue_key}`{f' by `{actor}`' if actor else ''}: {issue_url}",
        check=check,
        session=session,
    )
    return {"ok": True, "status": 200, "detail": None}


def _execute_delivery(session: Session, delivery: NotificationDelivery) -> dict[str, Any]:
    payload = _json_loads(delivery.payload_json)
    if delivery.request_kind == KIND_EMAIL:
        return _execute_email_delivery(session, delivery, payload)
    if delivery.request_kind == KIND_DISCORD:
        return _execute_discord_delivery(session, delivery, payload)
    if delivery.request_kind == KIND_SLACK:
        return _execute_slack_delivery(session, delivery, payload)
    if delivery.request_kind == KIND_PAGERDUTY:
        return _execute_pagerduty_delivery(session, delivery, payload)
    if delivery.request_kind == KIND_PROJECT_WEBHOOK:
        return _execute_project_webhook_delivery(session, delivery, payload)
    if delivery.request_kind == KIND_JIRA_TICKET:
        return _execute_jira_ticket_delivery(session, delivery, payload)
    return {"ok": False, "detail": f"Unknown notification request kind: {delivery.request_kind}"}


def process_notification_delivery(
    session: Session,
    delivery: NotificationDelivery,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now = now or _now()
    try:
        result = _execute_delivery(session, delivery)
    except Exception as exc:
        logger.exception("Notification delivery %s raised an exception", getattr(delivery, "id", None))
        session.rollback()
        delivery = session.get(NotificationDelivery, delivery.id)
        if delivery is None:
            return {"ok": False, "detail": "Notification delivery disappeared during retry"}
        outcome = _mark_delivery_result(session, delivery, now=now, ok=False, detail=str(exc), status_code=None)
        session.commit()
        return outcome

    ok = bool(result.get("ok"))
    outcome = _mark_delivery_result(
        session,
        delivery,
        now=now,
        ok=ok,
        detail=result.get("detail"),
        status_code=result.get("status"),
    )
    session.commit()
    return outcome


def process_notification_queue(
    session: Session,
    *,
    now: Optional[datetime] = None,
    limit: Optional[int] = None,
    worker_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    now = now or _now()
    deliveries = claim_due_notification_deliveries(session, now=now, limit=limit, worker_id=worker_id)
    results: list[dict[str, Any]] = []
    for delivery in deliveries:
        try:
            outcome = process_notification_delivery(session, delivery, now=now)
        except Exception:
            logger.exception("Notification delivery %s could not be processed", getattr(delivery, "id", None))
            continue
        outcome["id"] = delivery.id
        results.append(outcome)
    return results


def retry_notification_delivery(session: Session, delivery: NotificationDelivery, *, worker_id: Optional[str] = None) -> dict[str, Any]:
    now = _now()
    if delivery.status == STATUS_DELIVERED:
        return {"ok": False, "detail": "Notification delivery already succeeded"}
    result = session.exec(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.id == delivery.id,
            NotificationDelivery.status.in_([STATUS_QUEUED, STATUS_RETRY, STATUS_DEAD]),
        )
        .values(
            status=STATUS_PROCESSING,
            claimed_by=worker_id or "manual-retry",
            claimed_at=now,
            updated_at=now,
        )
    )
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        session.rollback()
        return {"ok": False, "detail": "Notification delivery is already being processed"}
    session.commit()
    refreshed = session.get(NotificationDelivery, delivery.id)
    if refreshed is None:
        return {"ok": False, "detail": "Notification delivery not found"}
    return process_notification_delivery(session, refreshed, now=now)


def cancel_notification_delivery(session: Session, delivery: NotificationDelivery, *, reason: Optional[str] = None) -> dict[str, Any]:
    now = _now()
    detail = _safe_error_text(reason) or "Canceled by operator"
    result = session.exec(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.id == delivery.id,
            NotificationDelivery.status.in_([STATUS_QUEUED, STATUS_RETRY]),
        )
        .values(
            status=STATUS_DEAD,
            claimed_by=None,
            claimed_at=None,
            dead_at=now,
            next_attempt_at=now,
            last_error=detail,
            updated_at=now,
        )
    )
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        session.rollback()
        return {"ok": False, "detail": "Only queued or retry deliveries can be canceled"}
    session.commit()
    refreshed = session.get(NotificationDelivery, delivery.id)
    if refreshed is None:
        return {"ok": False, "detail": "Notification delivery not found"}
    return {
        "ok": True,
        "detail": detail,
        "delivery_status": refreshed.status,
        "status": refreshed.last_status_code,
    }


def poison_notification_delivery(session: Session, delivery: NotificationDelivery, *, reason: Optional[str] = None) -> dict[str, Any]:
    now = _now()
    detail = _safe_error_text(reason) or "Poisoned by operator"
    result = session.exec(
        update(NotificationDelivery)
        .where(
            NotificationDelivery.id == delivery.id,
            NotificationDelivery.status.in_([STATUS_QUEUED, STATUS_RETRY]),
        )
        .values(
            status=STATUS_DEAD,
            claimed_by=None,
            claimed_at=None,
            dead_at=now,
            next_attempt_at=now,
            last_error=detail,
            updated_at=now,
        )
    )
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        session.rollback()
        return {"ok": False, "detail": "Only queued or retry deliveries can be poisoned"}
    session.commit()
    refreshed = session.get(NotificationDelivery, delivery.id)
    if refreshed is None:
        return {"ok": False, "detail": "Notification delivery not found"}
    return {
        "ok": True,
        "detail": detail,
        "delivery_status": refreshed.status,
        "status": refreshed.last_status_code,
    }
