import os
import json
import logging
import urllib.request
import urllib.error
import time
import smtplib
from email.message import EmailMessage
from typing import Optional

logger = logging.getLogger("lastping.alerts")


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


def send_pagerduty_event(routing_key: str, summary: str, severity: str = "critical") -> bool:
    if not routing_key:
        logger.debug("No PagerDuty routing key configured")
        return False
    pd_payload = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "payload": {"summary": summary, "severity": severity, "source": "lastping"},
    }
    return _post_json("https://events.pagerduty.com/v2/enqueue", pd_payload)


def notify_down(check, project, reason: str = None) -> None:
    try:
        reason_text = f"Reason: {reason}" if reason else None
        timestamp = None
        try:
            timestamp = check.last_ping.isoformat() if getattr(check, 'last_ping', None) else None
        except Exception:
            timestamp = None

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
        # Discord: use embed for nicer display
        if getattr(project, 'discord_webhook_url', None):
            embed = {
                "title": ":rotating_light: DOWN",
                "description": summary,
                "fields": [
                    {"name": "Check", "value": check.name, "inline": True},
                    {"name": "Last ping", "value": timestamp or "n/a", "inline": True},
                    {"name": "Failures", "value": str(getattr(check, 'consecutive_failures', 0)), "inline": True},
                ],
            }
            if reason_text:
                embed["fields"].append({"name": "Reason", "value": reason_text, "inline": False})
            _post_json(project.discord_webhook_url, {"embeds": [embed]})
            sent = True

        # Slack: blocks
        if getattr(project, 'slack_webhook_url', None):
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
            _post_json(project.slack_webhook_url, {"blocks": blocks})
            sent = True

        # PagerDuty: use existing helper which builds proper event payload
        if getattr(project, 'pagerduty_integration_key', None):
            send_pagerduty_event(project.pagerduty_integration_key, summary, severity="critical")
            sent = True

        # Generic webhook: send structured JSON
        if getattr(project, 'generic_webhook_url', None):
            payload = {"event": "down", "summary": summary, "details": details}
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
    except Exception:
        logger.exception("Failed to send DOWN notification")


def notify_recovery(check, project) -> None:
    try:
        timestamp = None
        try:
            timestamp = check.last_ping.isoformat() if getattr(check, 'last_ping', None) else None
        except Exception:
            timestamp = None

        summary = f"Project {project.name} — Check {check.name} recovered"
        details = {"project": project.name, "check": check.name, "last_ping": timestamp}

        sent = False
        if getattr(project, 'discord_webhook_url', None):
            embed = {"title": ":white_check_mark: RECOVERY", "description": summary, "fields": [{"name": "Last ping", "value": timestamp or "n/a", "inline": True}]}
            _post_json(project.discord_webhook_url, {"embeds": [embed]})
            sent = True
        if getattr(project, 'slack_webhook_url', None):
            blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f":white_check_mark: *{summary}*"}}, {"type": "section", "fields": [{"type": "mrkdwn", "text": f"*Last ping:* {timestamp or 'n/a'}"}]}]
            _post_json(project.slack_webhook_url, {"blocks": blocks})
            sent = True
        if getattr(project, 'pagerduty_integration_key', None):
            send_pagerduty_event(project.pagerduty_integration_key, summary, severity="info")
            sent = True
        if getattr(project, 'generic_webhook_url', None):
            send_generic_webhook(project.generic_webhook_url, {"event": "recovery", "summary": summary, "details": details})
            sent = True

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


def notify_escalation(project, reason: str):
    esc = os.environ.get("ALERT_ESCALATION_EMAIL")
    if not esc:
        logger.debug("No escalation email configured")
        return False
    subj = f"[LastPing] Escalation: project {project.name} alert threshold exceeded"
    body = f"Project {project.name} has exceeded its alert threshold. Latest reason: {reason}"
    return send_email(subj, body, to=esc)
    