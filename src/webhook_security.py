import json
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, delete

from .models import WebhookReceipt
from .security import verify_signed_webhook


def webhook_replay_window_seconds() -> int:
    raw = (os.environ.get("LASTPING_WEBHOOK_REPLAY_WINDOW_SECONDS") or "").strip()
    try:
        value = int(raw) if raw else 300
    except ValueError:
        return 300
    return max(60, min(value, 3600))


def parse_signed_json_body(raw_body: bytes) -> dict:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object")
    return payload


def verify_signed_webhook_request(
    *,
    source: str,
    secret: str,
    timestamp: Optional[str],
    signature: Optional[str],
    raw_body: bytes,
) -> tuple[datetime, str]:
    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail=f"Missing signed {source} webhook headers")
    try:
        return verify_signed_webhook(
            secret=secret,
            timestamp=timestamp,
            signature=signature,
            body=raw_body,
            max_skew_seconds=webhook_replay_window_seconds(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def register_webhook_receipt(
    session: Session,
    *,
    source: str,
    signature: str,
    request_timestamp: datetime,
) -> bool:
    receipt = WebhookReceipt(
        source=source,
        signature=signature,
        request_timestamp=request_timestamp,
    )
    session.add(receipt)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return False

    cutoff = datetime.utcnow() - timedelta(days=7)
    session.exec(
        delete(WebhookReceipt).where(
            WebhookReceipt.source == source,
            WebhookReceipt.received_at < cutoff,
        )
    )
    session.flush()
    return True
