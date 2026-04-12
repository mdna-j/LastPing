"""
Alert helpers and channel adapters.

This module centralises all outbound alerting logic (Discord, Slack,
PagerDuty, email, generic webhooks). Functions retry transient
failures and prefer project-specific webhooks when configured.
"""

import os
import json
import logging
import urllib.request
import urllib.error
import urllib.parse
import time
import smtplib
import base64
from email.message import EmailMessage
from typing import Optional
from datetime import datetime
from sqlmodel import Session, select

from .db import ensure_engine
from .models import AuditLog, Incident, StatusSubscription
from .notification_queue import (
    queue_discord_delivery,
    queue_email_delivery,
    queue_pagerduty_delivery,
    queue_project_webhook_delivery,
    queue_slack_delivery,
)
from .secret_lifecycle import SECRET_PAGERDUTY_INTEGRATION_KEY, active_project_secret_candidates, touch_project_secret_last_used

logger = logging.getLogger("lastping.alerts")


def _json_safe(value):
    if value is None:
        return None
    try:
        return json.loads(json.dumps(value))
    except Exception:
        return str(value)


def _check_channel_enabled(check, attr: str) -> bool:
    if check is None:
        return True
    val = getattr(check, attr, None)
    if val is None:
        return True
    return bool(val)


def _sms_allowed(project, check=None) -> bool:
    # per-check override
    if check is not None and getattr(check, "alert_sms_enabled", None) is not None:
        return bool(getattr(check, "alert_sms_enabled"))
    enabled = getattr(project, "sms_enabled", None)
    if enabled is None:
        # allow per-project overrides to enable SMS even if env is unset
        if project and (getattr(project, "sms_account_sid", None) or getattr(project, "sms_auth_token", None) or getattr(project, "sms_from", None) or getattr(project, "sms_to", None)):
            return True
        return bool(os.environ.get("ALERT_SMS_TO"))
    return bool(enabled)


def _sms_to(project, check=None) -> Optional[str]:
    if check is not None and getattr(check, "alert_sms_to", None):
        return getattr(check, "alert_sms_to", None)
    return getattr(project, "sms_to", None) or os.environ.get("ALERT_SMS_TO")


def _sms_from(project) -> Optional[str]:
    return getattr(project, "sms_from", None) or os.environ.get("TWILIO_FROM")


def _sms_account_sid(project) -> Optional[str]:
    return getattr(project, "sms_account_sid", None) or os.environ.get("TWILIO_ACCOUNT_SID")


def _sms_auth_token(project) -> Optional[str]:
    return getattr(project, "sms_auth_token", None) or os.environ.get("TWILIO_AUTH_TOKEN")


def _oncall_allowed(project, check=None) -> bool:
    if check is not None and getattr(check, "alert_oncall_enabled", None) is not None:
        return bool(getattr(check, "alert_oncall_enabled"))
    enabled = getattr(project, "oncall_enabled", None)
    return bool(enabled)


def _oncall_email(project, check=None) -> Optional[str]:
    if check is not None and getattr(check, "alert_oncall_email", None):
        return getattr(check, "alert_oncall_email", None)
    return getattr(project, "oncall_email", None)


def _discord_url(project, check=None) -> Optional[str]:
    return getattr(check, "alert_discord_webhook_url", None) or getattr(project, "discord_webhook_url", None)


def _slack_url(project, check=None) -> Optional[str]:
    return getattr(check, "alert_slack_webhook_url", None) or getattr(project, "slack_webhook_url", None)


def _slack_channel(project, check=None, incident=None) -> Optional[str]:
    return (
        getattr(incident, "slack_channel_id", None)
        or getattr(check, "alert_slack_channel", None)
        or getattr(project, "slack_channel", None)
        or os.environ.get("SLACK_ALERT_CHANNEL")
    )


def _slack_bot_token() -> Optional[str]:
    return os.environ.get("SLACK_BOT_TOKEN")


def _pagerduty_key(project, check=None) -> Optional[str]:
    return getattr(check, "alert_pagerduty_integration_key", None) or getattr(project, "pagerduty_integration_key", None)


def _pagerduty_keys(project, check=None) -> list[str]:
    override = getattr(check, "alert_pagerduty_integration_key", None) if check is not None else None
    if override:
        return [override]
    if getattr(project, "id", None) is None:
        current = getattr(project, "pagerduty_integration_key", None)
        return [current] if current else []
    return active_project_secret_candidates(project, SECRET_PAGERDUTY_INTEGRATION_KEY)


def _send_pagerduty_event_for_project(
    project,
    summary: str,
    severity: str = "critical",
    *,
    check=None,
    **kwargs,
) -> bool:
    used_project_secret = check is None or not getattr(check, "alert_pagerduty_integration_key", None)
    for routing_key in _pagerduty_keys(project, check):
        if send_pagerduty_event(routing_key, summary, severity, **kwargs):
            if used_project_secret and getattr(project, "id", None) is not None:
                touch_project_secret_last_used(project.id, SECRET_PAGERDUTY_INTEGRATION_KEY)
            return True
    return False


def _pagerduty_dedup_key(project, check=None, incident: Optional[Incident] = None) -> Optional[str]:
    if incident is not None and getattr(incident, "pagerduty_dedup_key", None):
        return incident.pagerduty_dedup_key
    if incident is not None and getattr(incident, "id", None) is not None:
        return f"lastping:incident:{getattr(project, 'id', 'na')}:{incident.id}"
    if check is not None and getattr(check, "id", None) is not None:
        return f"lastping:check:{getattr(project, 'id', 'na')}:{check.id}"
    return None


def _generic_webhook_url(project, check=None) -> Optional[str]:
    return getattr(check, "alert_generic_webhook_url", None) or getattr(project, "generic_webhook_url", None)


def _record_notification_failure(
    *,
    project,
    channel: str,
    event: str,
    detail: str,
    check=None,
    target: Optional[str] = None,
    subscription=None,
    retry_payload: Optional[dict] = None,
    request_kind: Optional[str] = None,
) -> None:
    payload = {
        "project_id": getattr(project, "id", None),
        "check_id": getattr(check, "id", None) if check is not None else None,
        "subscription_id": getattr(subscription, "id", None) if subscription is not None else None,
        "channel": channel,
        "event": event,
        "detail": detail,
        "target": target,
        "recorded_at": datetime.utcnow().isoformat(),
        "request_kind": request_kind,
        "retry_payload": _json_safe(retry_payload) if retry_payload is not None else None,
        "retryable": bool(
            request_kind == "json_post"
            and isinstance(target, str)
            and target.startswith(("http://", "https://"))
            and retry_payload is not None
        ),
    }
    try:
        with Session(ensure_engine()) as audit_session:
            audit_session.add(
                AuditLog(
                    actor="alerts",
                    action="notification_failed",
                    target_type="project",
                    target_id=getattr(project, "id", None),
                    details=json.dumps(payload),
                    actor_ip=None,
                    user_agent=None,
                )
            )
            audit_session.commit()
    except Exception:
        logger.exception(
            "Failed to persist notification failure for project=%s channel=%s event=%s",
            getattr(project, "id", None),
            channel,
            event,
        )


def _track_notification_result(
    ok: bool,
    *,
    project,
    channel: str,
    event: str,
    detail: str,
    check=None,
    target: Optional[str] = None,
    subscription=None,
    retry_payload: Optional[dict] = None,
    request_kind: Optional[str] = None,
) -> bool:
    if ok:
        return True
    _record_notification_failure(
        project=project,
        check=check,
        subscription=subscription,
        channel=channel,
        event=event,
        detail=detail,
        target=target,
        retry_payload=retry_payload,
        request_kind=request_kind,
    )
    return False


def _queue_delivery(queue_func, **kwargs) -> bool:
    session = kwargs.pop("session", None)
    if session is None:
        return False
    try:
        queue_func(session, **kwargs)
        return True
    except Exception:
        logger.exception(
            "Failed to enqueue notification delivery channel=%s event=%s project=%s",
            kwargs.get("channel"),
            kwargs.get("event"),
            kwargs.get("project_id"),
        )
        return False


def _post_json_with_response(url: str, payload: dict, timeout: int = 10, headers: Optional[dict] = None) -> Optional[dict]:
    if not url:
        logger.debug("No webhook URL configured")
        return None
    data = json.dumps(payload).encode("utf-8")
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers)
    attempts = 3
    backoff = 0.5
    last_response = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                logger.debug("Webhook response code: %s", resp.getcode())
                body = resp.read()
                if not body:
                    return {"ok": True, "status": resp.getcode()}
                try:
                    parsed = json.loads(body.decode("utf-8"))
                    if "ok" not in parsed:
                        parsed["ok"] = 200 <= resp.getcode() < 300
                    parsed.setdefault("status", resp.getcode())
                    return parsed
                except Exception:
                    return {"ok": True, "status": resp.getcode(), "body": body.decode("utf-8", "replace")}
        except urllib.error.HTTPError as he:
            logger.exception("HTTP error sending webhook (attempt %s): %s", i + 1, he)
            try:
                body = he.read()
                if body:
                    try:
                        parsed = json.loads(body.decode("utf-8"))
                        parsed.setdefault("ok", False)
                        parsed.setdefault("status", he.code)
                        last_response = parsed
                    except Exception:
                        last_response = {"ok": False, "status": he.code, "body": body.decode("utf-8", "replace")}
            except Exception:
                pass
        except Exception as e:
            logger.exception("Error sending webhook (attempt %s): %s", i + 1, e)
        time.sleep(backoff)
        backoff *= 2
    return last_response


def _post_json(url: str, payload: dict, timeout: int = 10) -> bool:
    response = _post_json_with_response(url, payload, timeout=timeout)
    return bool(response and response.get("ok"))


def _remember_incident_slack_thread(
    incident: Optional[Incident],
    *,
    thread_ts: Optional[str],
    channel_id: Optional[str],
    session: Optional[Session] = None,
) -> None:
    if incident is None:
        return
    changed = False
    if thread_ts and getattr(incident, "slack_thread_ts", None) != thread_ts:
        incident.slack_thread_ts = thread_ts
        changed = True
    if channel_id and getattr(incident, "slack_channel_id", None) != channel_id:
        incident.slack_channel_id = channel_id
        changed = True
    if not changed:
        return
    if session is not None:
        session.add(incident)
        return
    if getattr(incident, "id", None) is None:
        return
    try:
        with Session(ensure_engine()) as update_session:
            db_incident = update_session.get(Incident, incident.id)
            if db_incident is None:
                return
            if thread_ts:
                db_incident.slack_thread_ts = thread_ts
            if channel_id:
                db_incident.slack_channel_id = channel_id
            update_session.add(db_incident)
            update_session.commit()
    except Exception:
        logger.exception("Failed to persist Slack thread metadata for incident=%s", getattr(incident, "id", None))


def _remember_incident_pagerduty_dedup_key(
    incident: Optional[Incident],
    *,
    dedup_key: Optional[str],
    session: Optional[Session] = None,
) -> None:
    if incident is None or not dedup_key:
        return
    if getattr(incident, "pagerduty_dedup_key", None) == dedup_key:
        return
    incident.pagerduty_dedup_key = dedup_key
    if session is not None:
        session.add(incident)
        return
    if getattr(incident, "id", None) is None:
        return
    try:
        with Session(ensure_engine()) as update_session:
            db_incident = update_session.get(Incident, incident.id)
            if db_incident is None:
                return
            db_incident.pagerduty_dedup_key = dedup_key
            update_session.add(db_incident)
            update_session.commit()
    except Exception:
        logger.exception("Failed to persist PagerDuty dedup key for incident=%s", getattr(incident, "id", None))


def _post_slack_message(
    *,
    project,
    check=None,
    incident: Optional[Incident] = None,
    session: Optional[Session] = None,
    payload: dict,
    fallback_text: str,
) -> tuple[bool, Optional[str]]:
    token = _slack_bot_token()
    channel = _slack_channel(project, check, incident)
    if token and channel:
        api_payload = dict(payload)
        api_payload["channel"] = channel
        api_payload.setdefault("text", fallback_text)
        thread_ts = getattr(incident, "slack_thread_ts", None) if incident is not None else None
        if thread_ts:
            api_payload["thread_ts"] = thread_ts
        response = _post_json_with_response(
            "https://slack.com/api/chat.postMessage",
            api_payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        ok = bool(response and response.get("ok"))
        if ok and incident is not None:
            _remember_incident_slack_thread(
                incident,
                thread_ts=response.get("ts") or thread_ts,
                channel_id=response.get("channel") or channel,
                session=session,
            )
        return ok, (response or {}).get("channel") or channel

    slack_url = _slack_url(project, check)
    if slack_url:
        hook_payload = dict(payload)
        hook_payload.setdefault("text", fallback_text)
        thread_ts = getattr(incident, "slack_thread_ts", None) if incident is not None else None
        if thread_ts:
            hook_payload["thread_ts"] = thread_ts
        return _post_json(slack_url, hook_payload), slack_url

    return send_slack_message(fallback_text), os.environ.get("SLACK_WEBHOOK_URL")
    return False


def send_discord_message(content: str) -> bool:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    payload = {"content": content}
    return _post_json(url, payload)


def send_slack_message(content: str) -> bool:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    payload = {"text": content}
    return _post_json(url, payload)


def send_generic_webhook(url: str, payload: dict) -> bool:
    return _post_json(url, payload)


def send_sms(message: str, to: Optional[str] = None, project=None) -> bool:
    """Send SMS via Twilio when configured (env vars or per-project overrides)."""
    sid = _sms_account_sid(project)
    token = _sms_auth_token(project)
    from_num = _sms_from(project)
    to_num = to or _sms_to(project)
    if not sid or not token or not from_num or not to_num:
        logger.debug("SMS not configured")
        return False
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urllib.parse.urlencode({"From": from_num, "To": to_num, "Body": message}).encode("utf-8")
    auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("utf-8")
    req = urllib.request.Request(url, data=data)
    req.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.getcode() < 300
    except Exception:
        logger.exception("Failed to send SMS")
    return False


def send_pagerduty_event(
    routing_key: str,
    summary: str,
    severity: str = "critical",
    *,
    event_action: str = "trigger",
    dedup_key: Optional[str] = None,
    source: str = "lastping",
    component: Optional[str] = None,
    custom_details: Optional[dict] = None,
) -> bool:
    if not routing_key:
        logger.debug("No PagerDuty routing key configured")
        return False
    # include timestamp and a structured payload for better incident context
    timestamp = datetime.utcnow().isoformat() + "Z"
    pd_payload = {
        "routing_key": routing_key,
        "event_action": event_action,
        "payload": {
            "summary": summary,
            "severity": severity,
            "source": source,
            "timestamp": timestamp,
        },
    }
    if dedup_key:
        pd_payload["dedup_key"] = dedup_key
    if component:
        pd_payload["payload"]["component"] = component
    if custom_details:
        pd_payload["payload"]["custom_details"] = custom_details
    return _post_json("https://events.pagerduty.com/v2/enqueue", pd_payload)


def notify_down(check, project, reason: str = None, incident: Optional[Incident] = None, session: Optional[Session] = None) -> None:
    try:
        reason_text = f"Reason: {reason}" if reason else None
        timestamp = None
        try:
            timestamp = check.last_ping.isoformat() if getattr(check, "last_ping", None) else None
        except Exception:
            timestamp = None
        now_iso = datetime.utcnow().isoformat() + "Z"
        base_url = os.environ.get("BASE_URL")
        project_url = f"{base_url}/projects/{getattr(project, 'id', '')}" if base_url and getattr(project, 'id', None) else None
        check_url = f"{project_url}/checks/{getattr(check, 'id', '')}" if project_url and getattr(check, 'id', None) else None

        # human-friendly summary
        summary = f"Project {project.name} — Check {check.name} is DOWN"
        details = {
            "project": project.name,
            "check": check.name,
            "last_ping": timestamp,
            "expected_interval": getattr(check, 'expected_interval', None) or getattr(check, 'interval', None),
            "grace_period": getattr(check, 'grace_period', None),
            "consecutive_failures": getattr(check, 'consecutive_failures', None),
            "reason": reason_text,
        }

        sent = False
        # Discord: use embed for nicer display (structured data improves readability)
        discord_url = _discord_url(project, check)
        if discord_url and _check_channel_enabled(check, "alert_discord_enabled"):
            # Discord embed with color, timestamp and optional links
            embed = {
                "title": ":rotating_light: DOWN",
                "description": summary,
                "color": 16711680,
                "timestamp": now_iso,
                "fields": [
                    {"name": "Check", "value": check.name, "inline": True},
                    {"name": "Last ping", "value": timestamp or "n/a", "inline": True},
                    {"name": "Failures", "value": str(getattr(check, "consecutive_failures", 0)), "inline": True},
                ],
            }
            if project_url:
                embed.setdefault("fields", []).append({"name": "Project URL", "value": project_url, "inline": False})
            if check_url:
                embed.setdefault("fields", []).append({"name": "Check URL", "value": check_url, "inline": False})
            if reason_text:
                embed["fields"].append({"name": "Reason", "value": reason_text, "inline": False})
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_discord_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    incident_id=getattr(incident, "id", None) if incident is not None else None,
                    event="down",
                    payload={"embeds": [embed]},
                    target="project discord route",
                ) or sent
            else:
                sent = _track_notification_result(
                    _post_json(discord_url, {"embeds": [embed]}),
                    project=project,
                    check=check,
                    channel="discord",
                    event="down",
                    target=discord_url,
                    detail="project Discord webhook send failed",
                    retry_payload={"embeds": [embed]},
                    request_kind="json_post",
                ) or sent

        # Slack: send Block Kit payload for structured messages
        slack_url = _slack_url(project, check)
        if (slack_url or _slack_channel(project, check, incident)) and _check_channel_enabled(check, "alert_slack_enabled"):
            # Slack Block Kit with attachments for color and optional action
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": f":rotating_light: *{summary}*"}},
                {"type": "section", "fields": [
                    {"type": "mrkdwn", "text": f"*Check:* {check.name}"},
                    {"type": "mrkdwn", "text": f"*Last ping:* {timestamp or 'n/a'}"},
                    {"type": "mrkdwn", "text": f"*Failures:* {getattr(check, 'consecutive_failures', 0)}"},
                ]},
            ]
            if reason_text:
                blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*Reason:* {reason_text}"}]})
            attachment = {"color": "#E74C3C", "blocks": blocks}
            # Add an action button linking to the project/check if available
            if check_url or project_url:
                action_elements = []
                if project_url:
                    action_elements.append({"type": "button", "text": {"type": "plain_text", "text": "Open Project"}, "url": project_url})
                if check_url:
                    action_elements.append({"type": "button", "text": {"type": "plain_text", "text": "Open Check"}, "url": check_url})
                attachment["actions"] = action_elements
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_slack_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    incident_id=getattr(incident, "id", None) if incident is not None else None,
                    event="down",
                    payload={"attachments": [attachment]},
                    fallback_text=summary,
                    target=_slack_channel(project, check, incident) or "project slack route",
                ) or sent
            else:
                slack_ok, slack_target = _post_slack_message(
                    project=project,
                    check=check,
                    incident=incident,
                    session=session,
                    payload={"attachments": [attachment]},
                    fallback_text=summary,
                )
                sent = _track_notification_result(
                    slack_ok,
                    project=project,
                    check=check,
                    channel="slack",
                    event="down",
                    target=slack_target,
                    detail="project Slack webhook send failed",
                    retry_payload={"attachments": [attachment]},
                    request_kind="json_post" if isinstance(slack_target, str) and slack_target.startswith(("http://", "https://")) else None,
                ) or sent

        # PagerDuty: use existing helper which builds proper event payload
        if _pagerduty_keys(project, check) and _check_channel_enabled(check, "alert_pagerduty_enabled"):
            # include check/project context in PagerDuty event details
            pd_details = details.copy()
            if project_url:
                pd_details["project_url"] = project_url
            if check_url:
                pd_details["check_url"] = check_url
            dedup_key = _pagerduty_dedup_key(project, check, incident)
            if incident is not None:
                _remember_incident_pagerduty_dedup_key(incident, dedup_key=dedup_key, session=session)
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_pagerduty_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    incident_id=getattr(incident, "id", None) if incident is not None else None,
                    event="down",
                    summary=summary,
                    severity="critical",
                    event_action="trigger",
                    source=project.name,
                    component=check.name,
                    custom_details=pd_details,
                    dedup_key=dedup_key,
                    target="pagerduty integration",
                ) or sent
            else:
                sent = _track_notification_result(
                    _send_pagerduty_event_for_project(
                        project,
                        summary,
                        "critical",
                        check=check,
                        event_action="trigger",
                        dedup_key=dedup_key,
                        source=project.name,
                        component=check.name,
                        custom_details=pd_details,
                    ),
                    project=project,
                    check=check,
                    channel="pagerduty",
                    event="down",
                    target="https://events.pagerduty.com/v2/enqueue",
                    detail="PagerDuty trigger event send failed",
                ) or sent

        # Generic webhook: send structured JSON payload so receivers can process
        gen_url = _generic_webhook_url(project, check)
        if gen_url and _check_channel_enabled(check, "alert_webhook_enabled"):
            payload = {
                "event": "down",
                "summary": summary,
                "details": details,
                "project_url": project_url,
                "check_url": check_url,
                "timestamp": now_iso,
            }
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_project_webhook_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    incident_id=getattr(incident, "id", None) if incident is not None else None,
                    event="down",
                    payload=payload,
                    target="project webhook",
                ) or sent
            else:
                sent = _track_notification_result(
                    send_generic_webhook(gen_url, payload),
                    project=project,
                    check=check,
                    channel="webhook",
                    event="down",
                    target=gen_url,
                    detail="generic webhook send failed",
                    retry_payload=payload,
                    request_kind="json_post",
                ) or sent

        # fall back to global endpoints if no project-specific webhook is set
        if not sent:
            msg = (
                f":rotating_light: **DOWN** — Project `{project.name}` — Check `{check.name}`\n"
                f"Last ping: `{check.last_ping}` — expected every `{getattr(check, 'expected_interval', getattr(check, 'interval', None))}s` + grace `{getattr(check, 'grace_period', None)}s`{(' — Reason: ' + reason) if reason else ''}"
            )
            if _check_channel_enabled(check, "alert_discord_enabled"):
                if session is not None and getattr(project, "id", None) is not None:
                    _queue_delivery(
                        queue_discord_delivery,
                        session=session,
                        project_id=project.id,
                        check_id=getattr(check, "id", None),
                        incident_id=getattr(incident, "id", None) if incident is not None else None,
                        event="down",
                        payload={"content": msg},
                        target="global discord route",
                    )
                else:
                    _track_notification_result(
                        send_discord_message(msg),
                        project=project,
                        check=check,
                        channel="discord",
                        event="down",
                        target=os.environ.get("DISCORD_WEBHOOK_URL"),
                        detail="global Discord webhook send failed",
                    )
            if _check_channel_enabled(check, "alert_slack_enabled"):
                if session is not None and getattr(project, "id", None) is not None:
                    _queue_delivery(
                        queue_slack_delivery,
                        session=session,
                        project_id=project.id,
                        check_id=getattr(check, "id", None),
                        incident_id=getattr(incident, "id", None) if incident is not None else None,
                        event="down",
                        payload={"text": msg},
                        fallback_text=msg,
                        target="global slack route",
                    )
                else:
                    _track_notification_result(
                        send_slack_message(msg),
                        project=project,
                        check=check,
                        channel="slack",
                        event="down",
                        target=os.environ.get("SLACK_WEBHOOK_URL"),
                        detail="global Slack webhook send failed",
                    )
        try:
            if _sms_allowed(project, check):
                sms_msg = f"[LastPing] DOWN: {project.name}/{check.name} {reason or ''}".strip()
                _track_notification_result(
                    send_sms(sms_msg, to=_sms_to(project, check), project=project),
                    project=project,
                    check=check,
                    channel="sms",
                    event="down",
                    target=_sms_to(project, check),
                    detail="SMS send failed",
                )
        except Exception:
            pass
        try:
            if _oncall_allowed(project, check) and _oncall_email(project, check):
                subj = f"[LastPing] DOWN: {project.name}/{check.name}"
                body = f"Project {project.name} check {check.name} is DOWN. {reason or ''}".strip()
                if session is not None and getattr(project, "id", None) is not None:
                    _queue_delivery(
                        queue_email_delivery,
                        session=session,
                        project_id=project.id,
                        check_id=getattr(check, "id", None),
                        incident_id=getattr(incident, "id", None) if incident is not None else None,
                        event="down",
                        subject=subj,
                        body=body,
                        to=_oncall_email(project, check),
                        target=_oncall_email(project, check),
                    )
                else:
                    _track_notification_result(
                        send_email(subj, body, to=_oncall_email(project, check)),
                        project=project,
                        check=check,
                        channel="email",
                        event="down",
                        target=_oncall_email(project, check),
                        detail="on-call email send failed",
                    )
        except Exception:
            pass
    except Exception:
        logger.exception("Failed to send DOWN notification")


def notify_degraded(check, project, reason: str = None, session: Optional[Session] = None) -> None:
    try:
        now_iso = datetime.utcnow().isoformat() + "Z"
        if reason is None:
            try:
                last_lat = getattr(check, "last_latency_ms", None)
                thr = getattr(check, "latency_threshold_ms", None)
                if last_lat is not None and thr is not None:
                    reason = f"latency_ms={float(last_lat):.1f} (> {int(thr)}ms)"
            except Exception:
                pass
        summary = f"Project {project.name} -- Check {check.name} is DEGRADED"
        sent = False
        discord_url = _discord_url(project, check)
        if discord_url and _check_channel_enabled(check, "alert_discord_enabled"):
            embed = {"title": ":warning: DEGRADED", "description": summary, "color": 16753920, "timestamp": now_iso}
            if reason:
                embed["fields"] = [{"name": "Reason", "value": reason, "inline": False}]
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_discord_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    event="degraded",
                    payload={"embeds": [embed]},
                    target="project discord route",
                ) or sent
            else:
                sent = _track_notification_result(
                    _post_json(discord_url, {"embeds": [embed]}),
                    project=project,
                    check=check,
                    channel="discord",
                    event="degraded",
                    target=discord_url,
                    detail="project Discord webhook send failed",
                    retry_payload={"embeds": [embed]},
                    request_kind="json_post",
                ) or sent
        slack_url = _slack_url(project, check)
        if slack_url and _check_channel_enabled(check, "alert_slack_enabled"):
            blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f":warning: *{summary}*"}}]
            if reason:
                blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*Reason:* {reason}"}]})
            attachment = {"color": "#F5A623", "blocks": blocks}
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_slack_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    event="degraded",
                    payload={"attachments": [attachment]},
                    fallback_text=summary,
                    target=_slack_channel(project, check) or "project slack route",
                ) or sent
            else:
                sent = _track_notification_result(
                    _post_json(slack_url, {"attachments": [attachment]}),
                    project=project,
                    check=check,
                    channel="slack",
                    event="degraded",
                    target=slack_url,
                    detail="project Slack webhook send failed",
                    retry_payload={"attachments": [attachment]},
                    request_kind="json_post",
                ) or sent
        gen_url = _generic_webhook_url(project, check)
        if gen_url and _check_channel_enabled(check, "alert_webhook_enabled"):
            payload = {"event": "degraded", "summary": summary, "details": {"reason": reason}, "timestamp": now_iso}
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_project_webhook_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    event="degraded",
                    payload=payload,
                    target="project webhook",
                ) or sent
            else:
                sent = _track_notification_result(
                    send_generic_webhook(gen_url, payload),
                    project=project,
                    check=check,
                    channel="webhook",
                    event="degraded",
                    target=gen_url,
                    detail="generic webhook send failed",
                    retry_payload=payload,
                    request_kind="json_post",
                ) or sent
        if not sent:
            msg = f":warning: **DEGRADED** -- Project `{project.name}` Check `{check.name}` {reason or ''}".strip()
            if _check_channel_enabled(check, "alert_discord_enabled"):
                if session is not None and getattr(project, "id", None) is not None:
                    _queue_delivery(
                        queue_discord_delivery,
                        session=session,
                        project_id=project.id,
                        check_id=getattr(check, "id", None),
                        event="degraded",
                        payload={"content": msg},
                        target="global discord route",
                    )
                else:
                    _track_notification_result(
                        send_discord_message(msg),
                        project=project,
                        check=check,
                        channel="discord",
                        event="degraded",
                        target=os.environ.get("DISCORD_WEBHOOK_URL"),
                        detail="global Discord webhook send failed",
                    )
            if _check_channel_enabled(check, "alert_slack_enabled"):
                if session is not None and getattr(project, "id", None) is not None:
                    _queue_delivery(
                        queue_slack_delivery,
                        session=session,
                        project_id=project.id,
                        check_id=getattr(check, "id", None),
                        event="degraded",
                        payload={"text": msg},
                        fallback_text=msg,
                        target="global slack route",
                    )
                else:
                    _track_notification_result(
                        send_slack_message(msg),
                        project=project,
                        check=check,
                        channel="slack",
                        event="degraded",
                        target=os.environ.get("SLACK_WEBHOOK_URL"),
                        detail="global Slack webhook send failed",
                    )
        try:
            if _sms_allowed(project, check):
                sms_msg = f"[LastPing] DEGRADED: {project.name}/{check.name} {reason or ''}".strip()
                _track_notification_result(
                    send_sms(sms_msg, to=_sms_to(project, check), project=project),
                    project=project,
                    check=check,
                    channel="sms",
                    event="degraded",
                    target=_sms_to(project, check),
                    detail="SMS send failed",
                )
        except Exception:
            pass
        try:
            if _oncall_allowed(project, check) and _oncall_email(project, check):
                subj = f"[LastPing] DEGRADED: {project.name}/{check.name}"
                body = f"Project {project.name} check {check.name} is DEGRADED. {reason or ''}".strip()
                if session is not None and getattr(project, "id", None) is not None:
                    _queue_delivery(
                        queue_email_delivery,
                        session=session,
                        project_id=project.id,
                        check_id=getattr(check, "id", None),
                        event="degraded",
                        subject=subj,
                        body=body,
                        to=_oncall_email(project, check),
                        target=_oncall_email(project, check),
                    )
                else:
                    _track_notification_result(
                        send_email(subj, body, to=_oncall_email(project, check)),
                        project=project,
                        check=check,
                        channel="email",
                        event="degraded",
                        target=_oncall_email(project, check),
                        detail="on-call email send failed",
                    )
        except Exception:
            pass
    except Exception:
        logger.exception("Failed to send DEGRADED notification")


def notify_recovery(check, project, incident: Optional[Incident] = None, session: Optional[Session] = None) -> None:
    try:
        timestamp = None
        try:
            timestamp = check.last_ping.isoformat() if getattr(check, 'last_ping', None) else None
        except Exception:
            timestamp = None

        now_iso = datetime.utcnow().isoformat() + "Z"
        base_url = os.environ.get("BASE_URL")
        project_url = f"{base_url}/projects/{getattr(project, 'id', '')}" if base_url and getattr(project, 'id', None) else None
        check_url = f"{project_url}/checks/{getattr(check, 'id', '')}" if project_url and getattr(check, 'id', None) else None

        summary = f"Project {project.name} — Check {check.name} recovered"
        details = {"project": project.name, "check": check.name, "last_ping": timestamp}

        sent = False
        discord_url = _discord_url(project, check)
        if discord_url and _check_channel_enabled(check, "alert_discord_enabled"):
            embed = {
                "title": ":white_check_mark: RECOVERY",
                "description": summary,
                "color": 3066993,
                "timestamp": now_iso,
                "fields": [{"name": "Last ping", "value": timestamp or "n/a", "inline": True}],
            }
            if project_url:
                embed["fields"].append({"name": "Project URL", "value": project_url, "inline": False})
            if check_url:
                embed["fields"].append({"name": "Check URL", "value": check_url, "inline": False})
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_discord_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    incident_id=getattr(incident, "id", None) if incident is not None else None,
                    event="recovery",
                    payload={"embeds": [embed]},
                    target="project discord route",
                ) or sent
            else:
                sent = _track_notification_result(
                    _post_json(discord_url, {"embeds": [embed]}),
                    project=project,
                    check=check,
                    channel="discord",
                    event="recovery",
                    target=discord_url,
                    detail="project Discord webhook send failed",
                    retry_payload={"embeds": [embed]},
                    request_kind="json_post",
                ) or sent
        slack_url = _slack_url(project, check)
        if (slack_url or _slack_channel(project, check, incident)) and _check_channel_enabled(check, "alert_slack_enabled"):
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": f":white_check_mark: *{summary}*"}},
                {"type": "section", "fields": [{"type": "mrkdwn", "text": f"*Last ping:* {timestamp or 'n/a'}"}]},
            ]
            attachment = {"color": "#2ECC71", "blocks": blocks}
            if check_url or project_url:
                actions = []
                if project_url:
                    actions.append({"type": "button", "text": {"type": "plain_text", "text": "Open Project"}, "url": project_url})
                if check_url:
                    actions.append({"type": "button", "text": {"type": "plain_text", "text": "Open Check"}, "url": check_url})
                attachment["actions"] = actions
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_slack_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    incident_id=getattr(incident, "id", None) if incident is not None else None,
                    event="recovery",
                    payload={"attachments": [attachment]},
                    fallback_text=summary,
                    target=_slack_channel(project, check, incident) or "project slack route",
                ) or sent
            else:
                slack_ok, slack_target = _post_slack_message(
                    project=project,
                    check=check,
                    incident=incident,
                    session=session,
                    payload={"attachments": [attachment]},
                    fallback_text=summary,
                )
                sent = _track_notification_result(
                    slack_ok,
                    project=project,
                    check=check,
                    channel="slack",
                    event="recovery",
                    target=slack_target,
                    detail="project Slack webhook send failed",
                    retry_payload={"attachments": [attachment]},
                    request_kind="json_post" if isinstance(slack_target, str) and slack_target.startswith(("http://", "https://")) else None,
                ) or sent
        if _pagerduty_keys(project, check) and _check_channel_enabled(check, "alert_pagerduty_enabled"):
            pd_details = details.copy()
            if project_url:
                pd_details["project_url"] = project_url
            if check_url:
                pd_details["check_url"] = check_url
            dedup_key = _pagerduty_dedup_key(project, check, incident)
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_pagerduty_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    incident_id=getattr(incident, "id", None) if incident is not None else None,
                    event="recovery",
                    summary=summary,
                    severity="info",
                    event_action="resolve",
                    source=project.name,
                    component=check.name,
                    custom_details=pd_details,
                    dedup_key=dedup_key,
                    target="pagerduty integration",
                ) or sent
            else:
                sent = _track_notification_result(
                    _send_pagerduty_event_for_project(
                        project,
                        summary,
                        "info",
                        check=check,
                        event_action="resolve",
                        dedup_key=dedup_key,
                        source=project.name,
                        component=check.name,
                        custom_details=pd_details,
                    ),
                    project=project,
                    check=check,
                    channel="pagerduty",
                    event="recovery",
                    target="https://events.pagerduty.com/v2/enqueue",
                    detail="PagerDuty recovery event send failed",
                ) or sent
        gen_url = _generic_webhook_url(project, check)
        if gen_url and _check_channel_enabled(check, "alert_webhook_enabled"):
            payload = {"event": "recovery", "summary": summary, "details": details, "timestamp": now_iso, "project_url": project_url, "check_url": check_url}
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_project_webhook_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    incident_id=getattr(incident, "id", None) if incident is not None else None,
                    event="recovery",
                    payload=payload,
                    target="project webhook",
                ) or sent
            else:
                sent = _track_notification_result(
                    send_generic_webhook(gen_url, payload),
                    project=project,
                    check=check,
                    channel="webhook",
                    event="recovery",
                    target=gen_url,
                    detail="generic webhook send failed",
                    retry_payload=payload,
                    request_kind="json_post",
                ) or sent

        # fall back to global endpoints if no project-specific webhook is configured
        if not sent:
            msg = (f":white_check_mark: **RECOVERY** — Project `{project.name}` — Check `{check.name}` is UP again\n" f"Last ping: `{check.last_ping}`")
            if _check_channel_enabled(check, "alert_discord_enabled"):
                if session is not None and getattr(project, "id", None) is not None:
                    _queue_delivery(
                        queue_discord_delivery,
                        session=session,
                        project_id=project.id,
                        check_id=getattr(check, "id", None),
                        incident_id=getattr(incident, "id", None) if incident is not None else None,
                        event="recovery",
                        payload={"content": msg},
                        target="global discord route",
                    )
                else:
                    _track_notification_result(
                        send_discord_message(msg),
                        project=project,
                        check=check,
                        channel="discord",
                        event="recovery",
                        target=os.environ.get("DISCORD_WEBHOOK_URL"),
                        detail="global Discord webhook send failed",
                    )
            if _check_channel_enabled(check, "alert_slack_enabled"):
                if session is not None and getattr(project, "id", None) is not None:
                    _queue_delivery(
                        queue_slack_delivery,
                        session=session,
                        project_id=project.id,
                        check_id=getattr(check, "id", None),
                        incident_id=getattr(incident, "id", None) if incident is not None else None,
                        event="recovery",
                        payload={"text": msg},
                        fallback_text=msg,
                        target="global slack route",
                    )
                else:
                    _track_notification_result(
                        send_slack_message(msg),
                        project=project,
                        check=check,
                        channel="slack",
                        event="recovery",
                        target=os.environ.get("SLACK_WEBHOOK_URL"),
                        detail="global Slack webhook send failed",
                    )
    except Exception:
        logger.exception("Failed to send recovery notification")


def notify_incident_slack_update(
    project,
    incident: Incident,
    *,
    action: str,
    body: str,
    check=None,
    session: Optional[Session] = None,
    share_url: Optional[str] = None,
) -> bool:
    if incident is None or not getattr(incident, "slack_thread_ts", None):
        return False
    if not _check_channel_enabled(check, "alert_slack_enabled"):
        return False
    summary = f"Incident #{incident.id} update"
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": f":speech_balloon: *{summary}*"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Action:* {action}"},
            {"type": "mrkdwn", "text": f"*Status:* {getattr(incident, 'status', 'open')}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
    ]
    if share_url:
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Open Shared Incident"},
                        "url": share_url,
                    }
                ],
            }
        )
    if session is not None and getattr(project, "id", None) is not None:
        return _queue_delivery(
            queue_slack_delivery,
            session=session,
            project_id=project.id,
            check_id=getattr(check, "id", None),
            incident_id=getattr(incident, "id", None),
            event=f"incident_{action}",
            payload={"blocks": blocks},
            fallback_text=f"{summary}: {body}",
            target=_slack_channel(project, check, incident) or "incident slack thread",
        )
    slack_ok, slack_target = _post_slack_message(
        project=project,
        check=check,
        incident=incident,
        session=session,
        payload={"blocks": blocks},
        fallback_text=f"{summary}: {body}",
    )
    return _track_notification_result(
        slack_ok,
        project=project,
        check=check,
        channel="slack",
        event=f"incident_{action}",
        target=slack_target,
        detail=f"incident Slack thread update failed for action={action}",
        retry_payload={"blocks": blocks},
        request_kind="json_post" if isinstance(slack_target, str) and slack_target.startswith(("http://", "https://")) else None,
    )


def notify_incident_pagerduty_update(
    project,
    incident: Incident,
    *,
    event_action: str,
    summary: str,
    check=None,
    session: Optional[Session] = None,
    severity: str = "critical",
    custom_details: Optional[dict] = None,
) -> bool:
    if not _pagerduty_keys(project, check) or not _check_channel_enabled(check, "alert_pagerduty_enabled"):
        return False
    dedup_key = _pagerduty_dedup_key(project, check, incident)
    if not dedup_key:
        return False
    _remember_incident_pagerduty_dedup_key(incident, dedup_key=dedup_key, session=session)
    if session is not None and getattr(project, "id", None) is not None:
        return _queue_delivery(
            queue_pagerduty_delivery,
            session=session,
            project_id=project.id,
            check_id=getattr(check, "id", None),
            incident_id=getattr(incident, "id", None),
            event=f"incident_{event_action}",
            summary=summary,
            severity=severity,
            event_action=event_action,
            source=getattr(project, "name", "lastping"),
            component=getattr(check, "name", None),
            custom_details=custom_details or {},
            dedup_key=dedup_key,
            target="pagerduty integration",
        )
    return _track_notification_result(
        _send_pagerduty_event_for_project(
            project,
            summary,
            severity,
            check=check,
            event_action=event_action,
            dedup_key=dedup_key,
            source=getattr(project, "name", "lastping"),
            component=getattr(check, "name", None),
            custom_details=custom_details or {},
        ),
        project=project,
        check=check,
        channel="pagerduty",
        event=f"incident_{event_action}",
        target="https://events.pagerduty.com/v2/enqueue",
        detail=f"incident PagerDuty sync failed for action={event_action}",
    )


def send_email(subject: str, body: str, to: Optional[str] = None) -> bool:
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    from_addr = os.environ.get("ALERT_EMAIL_FROM")
    to_addr = to or os.environ.get("ALERT_EMAIL_TO")
    # Basic configuration check - skip email if required env vars are missing
    if not smtp_host or not from_addr or not to_addr:
        logger.debug("Email not configured (SMTP_HOST or ALERT_EMAIL_FROM/TO missing)")
        return False

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as smtp:
                if smtp_user and smtp_pass:
                    smtp.login(smtp_user, smtp_pass)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as smtp:
                smtp.starttls()
                if smtp_user and smtp_pass:
                    smtp.login(smtp_user, smtp_pass)
                smtp.send_message(msg)
        return True
    except Exception:
        logger.exception("Failed to send email")
    return False


def notify_escalation(project, reason: str, check=None, session: Optional[Session] = None):
    """Notify escalation via project webhooks and optional email."""
    sent = False
    now_iso = datetime.utcnow().isoformat() + "Z"
    summary = f"Escalation: project {project.name} alert threshold exceeded"
    details = {"project": project.name, "reason": reason, "timestamp": now_iso}
    if check is not None:
        summary = f"Escalation: {project.name}/{getattr(check, 'name', 'check')} still failing"
        details.update({"check": getattr(check, "name", None), "check_id": getattr(check, "id", None)})

    discord_url = _discord_url(project, check)
    if discord_url and _check_channel_enabled(check, "alert_discord_enabled"):
        embed = {
            "title": ":warning: ESCALATION",
            "description": summary,
            "color": 16753920,
            "timestamp": now_iso,
            "fields": [{"name": "Reason", "value": reason or "n/a", "inline": False}],
        }
        if check is not None:
            embed["fields"].append({"name": "Check", "value": getattr(check, "name", "n/a"), "inline": True})
        if session is not None and getattr(project, "id", None) is not None:
            sent = _queue_delivery(
                queue_discord_delivery,
                session=session,
                project_id=project.id,
                check_id=getattr(check, "id", None),
                event="escalation",
                payload={"embeds": [embed]},
                target="project discord route",
            ) or sent
        else:
            sent = _track_notification_result(
                _post_json(discord_url, {"embeds": [embed]}),
                project=project,
                check=check,
                channel="discord",
                event="escalation",
                target=discord_url,
                detail="project Discord webhook send failed",
                retry_payload={"embeds": [embed]},
                request_kind="json_post",
            ) or sent

    slack_url = _slack_url(project, check)
    if slack_url and _check_channel_enabled(check, "alert_slack_enabled"):
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f":warning: *{summary}*"}},
            {"type": "section", "fields": [{"type": "mrkdwn", "text": f"*Reason:* {reason or 'n/a'}"}]},
        ]
        if check is not None:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*Check:* {getattr(check, 'name', 'n/a')}"}]})
        attachment = {"color": "#F5A623", "blocks": blocks}
        if session is not None and getattr(project, "id", None) is not None:
            sent = _queue_delivery(
                queue_slack_delivery,
                session=session,
                project_id=project.id,
                check_id=getattr(check, "id", None),
                event="escalation",
                payload={"attachments": [attachment]},
                fallback_text=summary,
                target=_slack_channel(project, check) or "project slack route",
            ) or sent
        else:
            sent = _track_notification_result(
                _post_json(slack_url, {"attachments": [attachment]}),
                project=project,
                check=check,
                channel="slack",
                event="escalation",
                target=slack_url,
                detail="project Slack webhook send failed",
                retry_payload={"attachments": [attachment]},
                request_kind="json_post",
            ) or sent

    if _pagerduty_keys(project, check) and _check_channel_enabled(check, "alert_pagerduty_enabled"):
        if session is not None and getattr(project, "id", None) is not None:
            sent = _queue_delivery(
                queue_pagerduty_delivery,
                session=session,
                project_id=project.id,
                check_id=getattr(check, "id", None),
                event="escalation",
                summary=summary,
                severity="critical",
                event_action="trigger",
                source=project.name,
                component=getattr(check, "name", None),
                custom_details=details,
                target="pagerduty integration",
            ) or sent
        else:
            sent = _track_notification_result(
                _send_pagerduty_event_for_project(
                    project,
                    summary,
                    "critical",
                    check=check,
                    event_action="trigger",
                    source=project.name,
                    component=getattr(check, "name", None),
                    custom_details=details,
                ),
                project=project,
                check=check,
                channel="pagerduty",
                event="escalation",
                target="https://events.pagerduty.com/v2/enqueue",
                detail="PagerDuty escalation event send failed",
            ) or sent

    gen_url = _generic_webhook_url(project, check)
    if gen_url and _check_channel_enabled(check, "alert_webhook_enabled"):
        payload = {"event": "escalation", "summary": summary, "details": details}
        if session is not None and getattr(project, "id", None) is not None:
            sent = _queue_delivery(
                queue_project_webhook_delivery,
                session=session,
                project_id=project.id,
                check_id=getattr(check, "id", None),
                event="escalation",
                payload=payload,
                target="project webhook",
            ) or sent
        else:
            sent = _track_notification_result(
                send_generic_webhook(gen_url, payload),
                project=project,
                check=check,
                channel="webhook",
                event="escalation",
                target=gen_url,
                detail="generic webhook send failed",
                retry_payload=payload,
                request_kind="json_post",
            ) or sent

    esc = os.environ.get("ALERT_ESCALATION_EMAIL")
    if esc:
        if check is not None:
            subj = f"[LastPing] Escalation: {project.name}/{getattr(check, 'name', 'check')}"
            body = f"Project {project.name} check {getattr(check, 'name', 'check')} escalation: {reason}"
        else:
            subj = f"[LastPing] Escalation: project {project.name} alert threshold exceeded"
            body = f"Project {project.name} has exceeded its alert threshold. Latest reason: {reason}"
        if session is not None and getattr(project, "id", None) is not None:
            sent = _queue_delivery(
                queue_email_delivery,
                session=session,
                project_id=project.id,
                check_id=getattr(check, "id", None),
                event="escalation",
                subject=subj,
                body=body,
                to=esc,
                target=esc,
            ) or sent
        else:
            sent = _track_notification_result(
                send_email(subj, body, to=esc),
                project=project,
                check=check,
                channel="email",
                event="escalation",
                target=esc,
                detail="escalation email send failed",
            ) or sent
    try:
        if _sms_allowed(project, check):
            if check is not None:
                sms_msg = f"[LastPing] ESCALATION: {project.name}/{getattr(check, 'name', 'check')} {reason or ''}".strip()
            else:
                sms_msg = f"[LastPing] ESCALATION: {project.name} {reason or ''}".strip()
            sent = _track_notification_result(
                send_sms(sms_msg, to=_sms_to(project, check), project=project),
                project=project,
                check=check,
                channel="sms",
                event="escalation",
                target=_sms_to(project, check),
                detail="SMS send failed",
            ) or sent
    except Exception:
        pass
    try:
        if _oncall_allowed(project, check) and _oncall_email(project, check):
            if check is not None:
                subj = f"[LastPing] ESCALATION: {project.name}/{getattr(check, 'name', 'check')}"
                body = f"Project {project.name} check {getattr(check, 'name', 'check')} escalation: {reason}"
            else:
                subj = f"[LastPing] ESCALATION: {project.name}"
                body = f"Project {project.name} escalation: {reason}"
            if session is not None and getattr(project, "id", None) is not None:
                sent = _queue_delivery(
                    queue_email_delivery,
                    session=session,
                    project_id=project.id,
                    check_id=getattr(check, "id", None),
                    event="escalation",
                    subject=subj,
                    body=body,
                    to=_oncall_email(project, check),
                    target=_oncall_email(project, check),
                ) or sent
            else:
                sent = _track_notification_result(
                    send_email(subj, body, to=_oncall_email(project, check)),
                    project=project,
                    check=check,
                    channel="email",
                    event="escalation",
                    target=_oncall_email(project, check),
                    detail="on-call email send failed",
                ) or sent
    except Exception:
        pass

    if not sent:
        logger.debug("No escalation channels configured")
    return sent


def notify_status_subscribers(session: Session, project, check, incident, *, event: str) -> bool:
    """Send public status notifications to email/webhook subscribers."""
    if project is None or check is None or incident is None:
        return False

    subscriptions = session.exec(
        select(StatusSubscription).where(
            StatusSubscription.project_id == getattr(project, "id", None),
            StatusSubscription.active == True,
        )
    ).all()
    if not subscriptions:
        return False

    event = (event or "").lower()
    if event not in {"opened", "resolved"}:
        raise ValueError("event must be opened or resolved")

    status_url = None
    base_url = (os.environ.get("BASE_URL") or "").rstrip("/")
    if base_url and getattr(project, "id", None):
        status_url = f"{base_url}/ui/status/{project.id}"

    incident_state = "investigating" if event == "opened" else "resolved"
    subject = (
        f"[LastPing Status] {project.name}: {check.name} incident opened"
        if event == "opened"
        else f"[LastPing Status] {project.name}: {check.name} incident resolved"
    )
    body_lines = [
        f"Project: {project.name}",
        f"Component: {check.name}",
        f"Incident: #{incident.id}",
        f"State: {incident_state}",
        f"Started: {incident.started_at.isoformat()}",
    ]
    if getattr(incident, "resolved_at", None):
        body_lines.append(f"Resolved: {incident.resolved_at.isoformat()}")
    if status_url:
        body_lines.append(f"Status page: {status_url}")

    payload = {
        "event": f"incident_{event}",
        "project": {"id": getattr(project, "id", None), "name": getattr(project, "name", None)},
        "component": {
            "id": getattr(check, "id", None),
            "name": getattr(check, "name", None),
            "status": getattr(check, "status", None),
            "type": getattr(check, "type", None),
        },
        "incident": {
            "id": getattr(incident, "id", None),
            "status": getattr(incident, "status", None),
            "started_at": incident.started_at.isoformat() if getattr(incident, "started_at", None) else None,
            "resolved_at": incident.resolved_at.isoformat() if getattr(incident, "resolved_at", None) else None,
        },
        "status_url": status_url,
        "sent_at": datetime.utcnow().isoformat() + "Z",
    }

    sent = False
    for subscription in subscriptions:
        try:
            if subscription.channel == "email":
                if getattr(project, "id", None) is not None:
                    sent = _queue_delivery(
                        queue_email_delivery,
                        session=session,
                        project_id=project.id,
                        check_id=getattr(check, "id", None),
                        incident_id=getattr(incident, "id", None),
                        event=f"status_{event}",
                        subject=subject,
                        body="\n".join(body_lines),
                        to=subscription.target,
                        target=subscription.target,
                    ) or sent
                else:
                    sent = _track_notification_result(
                        send_email(subject, "\n".join(body_lines), to=subscription.target),
                        project=project,
                        check=check,
                        subscription=subscription,
                        channel="email",
                        event=f"status_{event}",
                        target=subscription.target,
                        detail="public status email send failed",
                    ) or sent
            elif subscription.channel == "webhook":
                logger.info(
                    "Skipping disabled public status webhook subscription id=%s project=%s",
                    getattr(subscription, "id", None),
                    getattr(project, "id", None),
                )
                continue
        except Exception:
            _record_notification_failure(
                project=project,
                check=check,
                subscription=subscription,
                channel=subscription.channel,
                event=f"status_{event}",
                target=getattr(subscription, "target", None),
                detail="public status notification raised an exception",
                retry_payload=payload if subscription.channel == "webhook" else None,
                request_kind="json_post" if subscription.channel == "webhook" else None,
            )
            logger.exception(
                "Failed public status notification for project=%s subscription=%s",
                getattr(project, "id", None),
                getattr(subscription, "id", None),
            )
    return sent
