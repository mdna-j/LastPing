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

logger = logging.getLogger("lastping.alerts")


def _sms_allowed(project) -> bool:
    enabled = getattr(project, "sms_enabled", None)
    if enabled is None:
        return bool(os.environ.get("ALERT_SMS_TO"))
    return bool(enabled)


def _sms_to(project) -> Optional[str]:
    return getattr(project, "sms_to", None)


def _oncall_allowed(project) -> bool:
    enabled = getattr(project, "oncall_enabled", None)
    return bool(enabled)


def _oncall_email(project) -> Optional[str]:
    return getattr(project, "oncall_email", None)


def _post_json(url: str, payload: dict, timeout: int = 10) -> bool:
    if not url:
        logger.debug("No webhook URL configured")
        return False
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    attempts = 3
    backoff = 0.5
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                logger.debug("Webhook response code: %s", resp.getcode())
            return True
        except urllib.error.HTTPError as he:
            logger.exception("HTTP error sending webhook (attempt %s): %s", i + 1, he)
        except Exception as e:
            logger.exception("Error sending webhook (attempt %s): %s", i + 1, e)
        time.sleep(backoff)
        backoff *= 2
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


def send_sms(message: str, to: Optional[str] = None) -> bool:
    """Send SMS via Twilio when configured."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_num = os.environ.get("TWILIO_FROM")
    to_num = to or os.environ.get("ALERT_SMS_TO")
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


def send_pagerduty_event(routing_key: str, summary: str, severity: str = "critical") -> bool:
    if not routing_key:
        logger.debug("No PagerDuty routing key configured")
        return False
    # include timestamp and a structured payload for better incident context
    timestamp = datetime.utcnow().isoformat() + "Z"
    pd_payload = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "payload": {
            "summary": summary,
            "severity": severity,
            "source": "lastping",
            "timestamp": timestamp,
        },
    }
    return _post_json("https://events.pagerduty.com/v2/enqueue", pd_payload)


def notify_down(check, project, reason: str = None) -> None:
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
        if getattr(project, "discord_webhook_url", None):
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
            _post_json(project.discord_webhook_url, {"embeds": [embed]})
            sent = True

        # Slack: send Block Kit payload for structured messages
        if getattr(project, "slack_webhook_url", None):
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
            _post_json(project.slack_webhook_url, {"attachments": [attachment]})
            sent = True

        # PagerDuty: use existing helper which builds proper event payload
        if getattr(project, 'pagerduty_integration_key', None):
            # include check/project context in PagerDuty event details
            pd_details = details.copy()
            if project_url:
                pd_details["project_url"] = project_url
            if check_url:
                pd_details["check_url"] = check_url
            timestamp_pd = now_iso
            pd_summary = summary
            pd_payload = {
                "routing_key": project.pagerduty_integration_key,
                "event_action": "trigger",
                "payload": {
                    "summary": pd_summary,
                    "severity": "critical",
                    "source": project.name,
                    "timestamp": timestamp_pd,
                    "component": check.name,
                    "custom_details": pd_details,
                },
            }
            _post_json("https://events.pagerduty.com/v2/enqueue", pd_payload)
            sent = True

        # Generic webhook: send structured JSON payload so receivers can process
        if getattr(project, "generic_webhook_url", None):
            payload = {
                "event": "down",
                "summary": summary,
                "details": details,
                "project_url": project_url,
                "check_url": check_url,
                "timestamp": now_iso,
            }
            send_generic_webhook(project.generic_webhook_url, payload)
            sent = True

        # fall back to global endpoints if no project-specific webhook is set
        if not sent:
            msg = (
                f":rotating_light: **DOWN** — Project `{project.name}` — Check `{check.name}`\n"
                f"Last ping: `{check.last_ping}` — expected every `{getattr(check, 'expected_interval', getattr(check, 'interval', None))}s` + grace `{getattr(check, 'grace_period', None)}s`{(' — Reason: ' + reason) if reason else ''}"
            )
            send_discord_message(msg)
            send_slack_message(msg)
        try:
            if _sms_allowed(project):
                sms_msg = f"[LastPing] DOWN: {project.name}/{check.name} {reason or ''}".strip()
                send_sms(sms_msg, to=_sms_to(project))
        except Exception:
            pass
        try:
            if _oncall_allowed(project) and _oncall_email(project):
                subj = f"[LastPing] DOWN: {project.name}/{check.name}"
                body = f"Project {project.name} check {check.name} is DOWN. {reason or ''}".strip()
                send_email(subj, body, to=_oncall_email(project))
        except Exception:
            pass
    except Exception:
        logger.exception("Failed to send DOWN notification")


def notify_degraded(check, project, reason: str = None) -> None:
    try:
        now_iso = datetime.utcnow().isoformat() + "Z"
        summary = f"Project {project.name} -- Check {check.name} is DEGRADED"
        sent = False
        if getattr(project, "discord_webhook_url", None):
            embed = {"title": ":warning: DEGRADED", "description": summary, "color": 16753920, "timestamp": now_iso}
            if reason:
                embed["fields"] = [{"name": "Reason", "value": reason, "inline": False}]
            _post_json(project.discord_webhook_url, {"embeds": [embed]})
            sent = True
        if getattr(project, "slack_webhook_url", None):
            blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f":warning: *{summary}*"}}]
            if reason:
                blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*Reason:* {reason}"}]})
            attachment = {"color": "#F5A623", "blocks": blocks}
            _post_json(project.slack_webhook_url, {"attachments": [attachment]})
            sent = True
        if getattr(project, "generic_webhook_url", None):
            payload = {"event": "degraded", "summary": summary, "details": {"reason": reason}, "timestamp": now_iso}
            send_generic_webhook(project.generic_webhook_url, payload)
            sent = True
        if not sent:
            msg = f":warning: **DEGRADED** -- Project `{project.name}` Check `{check.name}` {reason or ''}".strip()
            send_discord_message(msg)
            send_slack_message(msg)
        try:
            if _sms_allowed(project):
                sms_msg = f"[LastPing] DEGRADED: {project.name}/{check.name} {reason or ''}".strip()
                send_sms(sms_msg, to=_sms_to(project))
        except Exception:
            pass
        try:
            if _oncall_allowed(project) and _oncall_email(project):
                subj = f"[LastPing] DEGRADED: {project.name}/{check.name}"
                body = f"Project {project.name} check {check.name} is DEGRADED. {reason or ''}".strip()
                send_email(subj, body, to=_oncall_email(project))
        except Exception:
            pass
    except Exception:
        logger.exception("Failed to send DEGRADED notification")


def notify_recovery(check, project) -> None:
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
        if getattr(project, "discord_webhook_url", None):
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
            _post_json(project.discord_webhook_url, {"embeds": [embed]})
            sent = True
        if getattr(project, "slack_webhook_url", None):
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
            _post_json(project.slack_webhook_url, {"attachments": [attachment]})
            sent = True
        if getattr(project, 'pagerduty_integration_key', None):
            pd_details = details.copy()
            if project_url:
                pd_details["project_url"] = project_url
            if check_url:
                pd_details["check_url"] = check_url
            timestamp_pd = now_iso
            pd_payload = {
                "routing_key": project.pagerduty_integration_key,
                "event_action": "trigger",
                "payload": {
                    "summary": summary,
                    "severity": "info",
                    "source": project.name,
                    "timestamp": timestamp_pd,
                    "component": check.name,
                    "custom_details": pd_details,
                },
            }
            _post_json("https://events.pagerduty.com/v2/enqueue", pd_payload)
            sent = True
        if getattr(project, "generic_webhook_url", None):
            send_generic_webhook(
                project.generic_webhook_url,
                {"event": "recovery", "summary": summary, "details": details, "timestamp": now_iso, "project_url": project_url, "check_url": check_url},
            )
            sent = True

        # fall back to global endpoints if no project-specific webhook is configured
        if not sent:
            msg = (f":white_check_mark: **RECOVERY** — Project `{project.name}` — Check `{check.name}` is UP again\n" f"Last ping: `{check.last_ping}`")
            send_discord_message(msg)
            send_slack_message(msg)
    except Exception:
        logger.exception("Failed to send recovery notification")


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


def notify_escalation(project, reason: str, check=None):
    """Notify escalation via project webhooks and optional email."""
    sent = False
    now_iso = datetime.utcnow().isoformat() + "Z"
    summary = f"Escalation: project {project.name} alert threshold exceeded"
    details = {"project": project.name, "reason": reason, "timestamp": now_iso}
    if check is not None:
        summary = f"Escalation: {project.name}/{getattr(check, 'name', 'check')} still failing"
        details.update({"check": getattr(check, "name", None), "check_id": getattr(check, "id", None)})

    if getattr(project, "discord_webhook_url", None):
        embed = {
            "title": ":warning: ESCALATION",
            "description": summary,
            "color": 16753920,
            "timestamp": now_iso,
            "fields": [{"name": "Reason", "value": reason or "n/a", "inline": False}],
        }
        if check is not None:
            embed["fields"].append({"name": "Check", "value": getattr(check, "name", "n/a"), "inline": True})
        _post_json(project.discord_webhook_url, {"embeds": [embed]})
        sent = True

    if getattr(project, "slack_webhook_url", None):
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f":warning: *{summary}*"}},
            {"type": "section", "fields": [{"type": "mrkdwn", "text": f"*Reason:* {reason or 'n/a'}"}]},
        ]
        if check is not None:
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*Check:* {getattr(check, 'name', 'n/a')}"}]})
        attachment = {"color": "#F5A623", "blocks": blocks}
        _post_json(project.slack_webhook_url, {"attachments": [attachment]})
        sent = True

    if getattr(project, "pagerduty_integration_key", None):
        pd_payload = {
            "routing_key": project.pagerduty_integration_key,
            "event_action": "trigger",
            "payload": {
                "summary": summary,
                "severity": "critical",
                "source": project.name,
                "timestamp": now_iso,
                "custom_details": details,
            },
        }
        _post_json("https://events.pagerduty.com/v2/enqueue", pd_payload)
        sent = True

    if getattr(project, "generic_webhook_url", None):
        payload = {"event": "escalation", "summary": summary, "details": details}
        send_generic_webhook(project.generic_webhook_url, payload)
        sent = True

    esc = os.environ.get("ALERT_ESCALATION_EMAIL")
    if esc:
        if check is not None:
            subj = f"[LastPing] Escalation: {project.name}/{getattr(check, 'name', 'check')}"
            body = f"Project {project.name} check {getattr(check, 'name', 'check')} escalation: {reason}"
        else:
            subj = f"[LastPing] Escalation: project {project.name} alert threshold exceeded"
            body = f"Project {project.name} has exceeded its alert threshold. Latest reason: {reason}"
        sent = send_email(subj, body, to=esc) or sent
    try:
        if _sms_allowed(project):
            if check is not None:
                sms_msg = f"[LastPing] ESCALATION: {project.name}/{getattr(check, 'name', 'check')} {reason or ''}".strip()
            else:
                sms_msg = f"[LastPing] ESCALATION: {project.name} {reason or ''}".strip()
            sent = send_sms(sms_msg, to=_sms_to(project)) or sent
    except Exception:
        pass
    try:
        if _oncall_allowed(project) and _oncall_email(project):
            if check is not None:
                subj = f"[LastPing] ESCALATION: {project.name}/{getattr(check, 'name', 'check')}"
                body = f"Project {project.name} check {getattr(check, 'name', 'check')} escalation: {reason}"
            else:
                subj = f"[LastPing] ESCALATION: {project.name}"
                body = f"Project {project.name} escalation: {reason}"
            sent = send_email(subj, body, to=_oncall_email(project)) or sent
    except Exception:
        pass

    if not sent:
        logger.debug("No escalation channels configured")
    return sent
    
