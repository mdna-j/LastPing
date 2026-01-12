import os
import json
import logging
import urllib.request
import urllib.error
from typing import Optional

logger = logging.getLogger("lastping.alerts")


def _post_json(url: str, payload: dict, timeout: int = 10) -> bool:
    if not url:
        logger.debug("No webhook URL configured")
        return False
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            logger.debug("Webhook response code: %s", resp.getcode())
        return True
    except urllib.error.HTTPError as he:
        logger.exception("HTTP error sending webhook: %s", he)
    except Exception as e:
        logger.exception("Error sending webhook: %s", e)
    return False


def send_discord_message(content: str) -> bool:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    payload = {"content": content}
    return _post_json(url, payload)


def notify_down(check, project, reason: str = None) -> None:
    try:
        reason_text = f" — Reason: {reason}" if reason else ""
        msg = (
            f":rotating_light: **DOWN** — Project `{project.name}` — Check `{check.name}`\n"
            f"Last ping: `{check.last_ping}` — expected every `{check.expected_interval}s` + grace `{check.grace_period}s`{reason_text}"
        )
        send_discord_message(msg)
    except Exception:
        logger.exception("Failed to send DOWN notification")


def notify_recovery(check, project) -> None:
    try:
        msg = (
            f":white_check_mark: **RECOVERY** — Project `{project.name}` — Check `{check.name}` is UP again\n"
            f"Last ping: `{check.last_ping}`"
        )
        send_discord_message(msg)
    except Exception:
        logger.exception("Failed to send recovery notification")


def notify_email_placeholder(subject: str, body: str, to: Optional[str] = None) -> None:
    # Placeholder: integrate real email provider in future
    logger.info("EMAIL placeholder — to=%s subject=%s body=%s", to, subject, body)
