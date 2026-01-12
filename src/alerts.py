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


def notify_down(check, project, reason: str = None) -> None:
    try:
        reason_text = f" — Reason: {reason}" if reason else ""
        msg = (
            f":rotating_light: **DOWN** — Project `{project.name}` — Check `{check.name}`\n"
            f"Last ping: `{check.last_ping}` — expected every `{check.expected_interval}s` + grace `{check.grace_period}s`{reason_text}"
        )
        send_discord_message(msg)
        send_slack_message(msg)
    except Exception:
        logger.exception("Failed to send DOWN notification")


def notify_recovery(check, project) -> None:
    try:
        msg = (
            f":white_check_mark: **RECOVERY** — Project `{project.name}` — Check `{check.name}` is UP again\n"
            f"Last ping: `{check.last_ping}`"
        )
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
