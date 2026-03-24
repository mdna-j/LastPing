"""
Checks CRUD routes.

Create and list monitoring checks for a project. Checks may be
heartbeat-based or HTTP checks; the worker interprets check fields to
drive scheduling and detection logic.
"""

from typing import List, Optional, Literal
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Header, Request, Path, Body
from pydantic import BaseModel, AnyHttpUrl, conint, constr, root_validator, EmailStr, validator
from sqlmodel import Session, select

from ..db import get_session
from ..models import Check as CheckModel, CheckType, CheckStatus, Project, AuditLog
from ..deps import require_admin_or_project_api_key, get_current_user, require_project_role, limit_by_api_key, get_audit_context, limit_public_requests
from ..schemas import StrictBaseModel


router = APIRouter(prefix="/projects/{project_id}/checks", tags=["checks"])

_BROWSER_ACTIONS = {
    "goto",
    "click",
    "fill",
    "wait_for_selector",
    "wait_for_url",
    "expect_text",
    "expect_url",
    "press",
}


def _diff_details(before: dict, after: dict) -> Optional[str]:
    changes = {}
    for key, old_val in before.items():
        new_val = after.get(key)
        if old_val != new_val:
            changes[key] = {"from": old_val, "to": new_val}
    if not changes:
        return None
    try:
        return json.dumps(changes, default=str)
    except Exception:
        return str(changes)


def _require_check_write_access(
    project_id: int,
    *,
    x_admin_token: Optional[str],
    authorization: Optional[str],
    x_api_key: Optional[str],
    session: Session,
) -> Project:
    """Allow admin/project API key auth, or fall back to owner user auth without masking API-key failures."""
    try:
        return require_admin_or_project_api_key(
            project_id,
            x_admin_token=x_admin_token,
            authorization=authorization,
            x_api_key=x_api_key,
            session=session,
        )
    except HTTPException as original_exc:
        try:
            user = get_current_user(authorization=authorization, session=session)
            require_project_role(project_id, "owner", current_user=user, session=session)
        except HTTPException:
            raise original_exc

        project = session.get(Project, project_id)
        if not project:
            raise original_exc
        return project


class CheckCreate(StrictBaseModel):
    name: constr(min_length=1, max_length=120)
    type: Optional[Literal["heartbeat", "http", "tcp", "dns", "script", "browser"]] = CheckType.HEARTBEAT
    expected_interval: Optional[conint(ge=1, le=86400)] = 600
    grace_period: Optional[conint(ge=0, le=86400)] = 600
    url: Optional[AnyHttpUrl] = None
    timeout: Optional[conint(ge=1, le=60)] = 5
    retries: Optional[conint(ge=0, le=10)] = 1
    host: Optional[constr(min_length=1, max_length=253)] = None
    port: Optional[conint(ge=1, le=65535)] = None
    dns_record_type: Optional[constr(regex=r"^[A-Za-z0-9_-]{1,10}$")] = None
    script_path: Optional[constr(min_length=1, max_length=200, regex=r"^[A-Za-z0-9._\\/-]+$")] = None
    script_args: Optional[List[constr(min_length=1, max_length=200)]] = None
    browser_steps: Optional[List["BrowserStep"]] = None
    browser_capture_screenshot: Optional[bool] = None
    interval: Optional[conint(ge=1, le=86400)] = 60
    latency_threshold_ms: Optional[conint(ge=1, le=600000)] = None
    region: Optional[constr(regex=r"^[A-Za-z0-9._-]{1,32}$")] = None
    alert_enabled: Optional[bool] = True
    alert_after: Optional[conint(ge=1, le=1000)] = 1
    alert_cooldown: Optional[conint(ge=0, le=86400)] = 3600
    alert_sms_enabled: Optional[bool] = None
    alert_oncall_enabled: Optional[bool] = None
    alert_slack_enabled: Optional[bool] = None
    alert_discord_enabled: Optional[bool] = None
    alert_pagerduty_enabled: Optional[bool] = None
    alert_webhook_enabled: Optional[bool] = None
    alert_sms_to: Optional[constr(regex=r"^\\+?[0-9]{7,20}$")] = None
    alert_oncall_email: Optional[EmailStr] = None
    alert_slack_webhook_url: Optional[AnyHttpUrl] = None
    alert_discord_webhook_url: Optional[AnyHttpUrl] = None
    alert_pagerduty_integration_key: Optional[constr(max_length=128)] = None
    alert_generic_webhook_url: Optional[AnyHttpUrl] = None
    escalation_after_minutes: Optional[conint(ge=1, le=10080)] = None
    escalation_cooldown_seconds: Optional[conint(ge=0, le=86400)] = 3600

    @validator("script_args")
    def _validate_script_args(cls, v):
        if v is None:
            return v
        if len(v) > 20:
            raise ValueError("script_args may have at most 20 entries")
        return v

    @validator("browser_steps")
    def _validate_browser_steps(cls, v):
        if v is None:
            return v
        if len(v) > 50:
            raise ValueError("browser_steps may have at most 50 entries")
        return v

    @root_validator
    def _validate_by_type(cls, values):
        ctype = values.get("type") or CheckType.HEARTBEAT
        if isinstance(ctype, str):
            ctype = ctype.lower()
            values["type"] = ctype
        if ctype == "http" and not values.get("url"):
            raise ValueError("url is required for http checks")
        if ctype == "tcp":
            if not values.get("host"):
                raise ValueError("host is required for tcp checks")
            if not values.get("port"):
                raise ValueError("port is required for tcp checks")
        if ctype == "dns":
            if not values.get("host"):
                raise ValueError("host is required for dns checks")
            if not values.get("dns_record_type"):
                raise ValueError("dns_record_type is required for dns checks")
        if ctype == "script":
            sp = values.get("script_path")
            if not sp:
                raise ValueError("script_path is required for script checks")
            if sp.startswith(("/", "\\")) or ":" in sp:
                raise ValueError("script_path must be a relative path (no drive letters or leading slashes)")
            parts = [p for p in sp.replace("\\", "/").split("/") if p]
            if any(p == ".." for p in parts):
                raise ValueError("script_path must not contain '..'")
        if ctype == "browser":
            if not values.get("url"):
                raise ValueError("url is required for browser checks")
            if not values.get("browser_steps"):
                raise ValueError("browser_steps is required for browser checks")
        else:
            if values.get("script_path") is not None:
                raise ValueError("script_path is only valid for script checks")
            if values.get("script_args") is not None:
                raise ValueError("script_args is only valid for script checks")
            if values.get("browser_steps") is not None:
                raise ValueError("browser_steps is only valid for browser checks")
            if values.get("browser_capture_screenshot") is not None:
                raise ValueError("browser_capture_screenshot is only valid for browser checks")
        return values


class BrowserStep(StrictBaseModel):
    action: Literal["goto", "click", "fill", "wait_for_selector", "wait_for_url", "expect_text", "expect_url", "press"]
    selector: Optional[constr(min_length=1, max_length=500)] = None
    value: Optional[constr(min_length=1, max_length=4000)] = None
    timeout_ms: Optional[conint(ge=1, le=120000)] = None

    @root_validator
    def _validate_step(cls, values):
        action = values.get("action")
        selector = values.get("selector")
        value = values.get("value")

        if action in {"click", "wait_for_selector"} and not selector:
            raise ValueError(f"selector is required for browser action '{action}'")
        if action in {"fill", "expect_text", "press"}:
            if not selector:
                raise ValueError(f"selector is required for browser action '{action}'")
            if not value:
                raise ValueError(f"value is required for browser action '{action}'")
        if action in {"goto", "wait_for_url", "expect_url"} and not value:
            raise ValueError(f"value is required for browser action '{action}'")
        return values


CheckCreate.update_forward_refs(BrowserStep=BrowserStep)


class CheckRead(BaseModel):
    id: int
    project_id: int
    name: str
    type: str
    status: str
    last_ping: Optional[datetime]
    maintenance_starts_at: Optional[datetime] = None
    maintenance_ends_at: Optional[datetime] = None
    url: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    dns_record_type: Optional[str] = None
    script_path: Optional[str] = None
    script_args: Optional[List[str]] = None
    browser_steps: Optional[List[dict]] = None
    browser_capture_screenshot: Optional[bool] = None
    interval: Optional[int] = None
    latency_threshold_ms: Optional[int] = None
    last_latency_ms: Optional[float] = None
    region: Optional[str] = None
    alert_enabled: Optional[bool] = None
    alert_after: Optional[int] = None
    alert_cooldown: Optional[int] = None
    alert_sms_enabled: Optional[bool] = None
    alert_oncall_enabled: Optional[bool] = None
    alert_slack_enabled: Optional[bool] = None
    alert_discord_enabled: Optional[bool] = None
    alert_pagerduty_enabled: Optional[bool] = None
    alert_webhook_enabled: Optional[bool] = None
    alert_sms_to: Optional[str] = None
    alert_oncall_email: Optional[str] = None
    alert_slack_webhook_url: Optional[str] = None
    alert_discord_webhook_url: Optional[str] = None
    alert_pagerduty_integration_key: Optional[str] = None
    alert_generic_webhook_url: Optional[str] = None
    escalation_after_minutes: Optional[int] = None
    escalation_cooldown_seconds: Optional[int] = None

    @validator("script_args", pre=True)
    def _parse_script_args(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except Exception:
                return None
        return None

    @validator("browser_steps", pre=True)
    def _parse_browser_steps(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except Exception:
                return None
        return None

    class Config:
        orm_mode = True


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=CheckRead)
def create_check(project_id: int = Path(..., ge=1), payload: CheckCreate = Body(...), request: Request = None, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), _rl = Depends(limit_by_api_key), session: Session = Depends(get_session)):
    """Create a check for the given project.

    Names must be unique within a project. HTTP checks should provide a
    `url`; heartbeat checks are created automatically by the heartbeat
    endpoint on first use as well.
    """
    # allow either admin/project API key OR a user with owner role
    project = _require_check_write_access(
        project_id,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )

    # ensure name uniqueness within project
    existing = session.exec(select(CheckModel).where(CheckModel.project_id == project_id, CheckModel.name == payload.name)).first()
    if existing:
        raise HTTPException(status_code=409, detail="Check with that name already exists")

    check = CheckModel(
        project_id=project_id,
        name=payload.name,
        type=payload.type,
        expected_interval=payload.expected_interval,
        grace_period=payload.grace_period,
        url=payload.url,
        host=payload.host,
        port=payload.port,
        dns_record_type=payload.dns_record_type,
        script_path=payload.script_path,
        script_args=(json.dumps(payload.script_args) if payload.script_args is not None else None),
        browser_steps=(json.dumps([step.dict(exclude_none=True) for step in payload.browser_steps]) if payload.browser_steps is not None else None),
        browser_capture_screenshot=(payload.browser_capture_screenshot if payload.browser_capture_screenshot is not None else True),
        timeout=payload.timeout,
        retries=payload.retries,
        interval=payload.interval,
        latency_threshold_ms=payload.latency_threshold_ms,
        region=payload.region,
        alert_enabled=payload.alert_enabled if payload.alert_enabled is not None else True,
        alert_after=payload.alert_after,
        alert_cooldown=payload.alert_cooldown,
        alert_sms_enabled=payload.alert_sms_enabled,
        alert_oncall_enabled=payload.alert_oncall_enabled,
        alert_slack_enabled=payload.alert_slack_enabled,
        alert_discord_enabled=payload.alert_discord_enabled,
        alert_pagerduty_enabled=payload.alert_pagerduty_enabled,
        alert_webhook_enabled=payload.alert_webhook_enabled,
        alert_sms_to=payload.alert_sms_to,
        alert_oncall_email=payload.alert_oncall_email,
        alert_slack_webhook_url=payload.alert_slack_webhook_url,
        alert_discord_webhook_url=payload.alert_discord_webhook_url,
        alert_pagerduty_integration_key=payload.alert_pagerduty_integration_key,
        alert_generic_webhook_url=payload.alert_generic_webhook_url,
        escalation_after_minutes=payload.escalation_after_minutes,
        escalation_cooldown_seconds=payload.escalation_cooldown_seconds,
        status=CheckStatus.UP,
    )
    session.add(check)
    session.commit()
    session.refresh(check)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="create_check", target_type="check", target_id=check.id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return check


class CheckUpdate(StrictBaseModel):
    name: Optional[constr(min_length=1, max_length=120)] = None
    url: Optional[AnyHttpUrl] = None
    interval: Optional[conint(ge=1, le=86400)] = None
    expected_interval: Optional[conint(ge=1, le=86400)] = None
    grace_period: Optional[conint(ge=0, le=86400)] = None
    host: Optional[constr(min_length=1, max_length=253)] = None
    port: Optional[conint(ge=1, le=65535)] = None
    dns_record_type: Optional[constr(regex=r"^[A-Za-z0-9_-]{1,10}$")] = None
    script_path: Optional[constr(min_length=1, max_length=200, regex=r"^[A-Za-z0-9._\\/-]+$")] = None
    script_args: Optional[List[constr(min_length=1, max_length=200)]] = None
    browser_steps: Optional[List[BrowserStep]] = None
    browser_capture_screenshot: Optional[bool] = None
    latency_threshold_ms: Optional[conint(ge=1, le=600000)] = None
    region: Optional[constr(regex=r"^[A-Za-z0-9._-]{1,32}$")] = None
    alert_enabled: Optional[bool] = None
    alert_after: Optional[conint(ge=1, le=1000)] = None
    alert_cooldown: Optional[conint(ge=0, le=86400)] = None
    alert_sms_enabled: Optional[bool] = None
    alert_oncall_enabled: Optional[bool] = None
    alert_slack_enabled: Optional[bool] = None
    alert_discord_enabled: Optional[bool] = None
    alert_pagerduty_enabled: Optional[bool] = None
    alert_webhook_enabled: Optional[bool] = None
    alert_sms_to: Optional[constr(regex=r"^\\+?[0-9]{7,20}$")] = None
    alert_oncall_email: Optional[EmailStr] = None
    alert_slack_webhook_url: Optional[AnyHttpUrl] = None
    alert_discord_webhook_url: Optional[AnyHttpUrl] = None
    alert_pagerduty_integration_key: Optional[constr(max_length=128)] = None
    alert_generic_webhook_url: Optional[AnyHttpUrl] = None
    escalation_after_minutes: Optional[conint(ge=1, le=10080)] = None
    escalation_cooldown_seconds: Optional[conint(ge=0, le=86400)] = None

    @validator("script_args")
    def _validate_script_args(cls, v):
        if v is None:
            return v
        if len(v) > 20:
            raise ValueError("script_args may have at most 20 entries")
        return v

    @validator("browser_steps")
    def _validate_browser_steps(cls, v):
        if v is None:
            return v
        if len(v) > 50:
            raise ValueError("browser_steps may have at most 50 entries")
        return v


@router.put("/{check_id}", response_model=CheckRead)
def update_check(project_id: int = Path(..., ge=1), check_id: int = Path(..., ge=1), payload: CheckUpdate = Body(...), request: Request = None, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)):
    # require owner/admin/api-key
    project = _require_check_write_access(
        project_id,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")

    # Script checks are intentionally constrained: you can only point at an on-disk script
    # under CUSTOM_CHECKS_DIR. We reject script fields on non-script checks to prevent
    # confusing configuration that the worker will ignore.
    if check.type != CheckType.SCRIPT and (payload.script_path is not None or payload.script_args is not None):
        raise HTTPException(status_code=400, detail="script_path/script_args are only valid for script checks")
    if check.type == CheckType.SCRIPT and payload.script_path is not None:
        sp = payload.script_path
        if sp.startswith(("/", "\\")) or ":" in sp:
            raise HTTPException(status_code=400, detail="script_path must be relative (no drive letters or leading slashes)")
        parts = [p for p in sp.replace("\\", "/").split("/") if p]
        if any(p == ".." for p in parts):
            raise HTTPException(status_code=400, detail="script_path must not contain '..'")
    if check.type != CheckType.BROWSER and (payload.browser_steps is not None or payload.browser_capture_screenshot is not None):
        raise HTTPException(status_code=400, detail="browser_steps/browser_capture_screenshot are only valid for browser checks")
    if check.type == CheckType.BROWSER and payload.browser_steps is not None and not payload.browser_steps:
        raise HTTPException(status_code=400, detail="browser_steps must not be empty for browser checks")
    before = {
        "name": check.name,
        "url": check.url,
        "interval": check.interval,
        "expected_interval": check.expected_interval,
        "grace_period": check.grace_period,
        "host": check.host,
        "port": check.port,
        "dns_record_type": check.dns_record_type,
        "script_path": getattr(check, "script_path", None),
        "script_args": getattr(check, "script_args", None),
        "browser_steps": getattr(check, "browser_steps", None),
        "browser_capture_screenshot": getattr(check, "browser_capture_screenshot", True),
        "latency_threshold_ms": check.latency_threshold_ms,
        "region": check.region,
        "alert_enabled": check.alert_enabled,
        "alert_after": check.alert_after,
        "alert_cooldown": check.alert_cooldown,
        "alert_sms_enabled": check.alert_sms_enabled,
        "alert_oncall_enabled": check.alert_oncall_enabled,
        "alert_slack_enabled": check.alert_slack_enabled,
        "alert_discord_enabled": check.alert_discord_enabled,
        "alert_pagerduty_enabled": check.alert_pagerduty_enabled,
        "alert_webhook_enabled": check.alert_webhook_enabled,
        "alert_sms_to": check.alert_sms_to,
        "alert_oncall_email": check.alert_oncall_email,
        "alert_slack_webhook_url": check.alert_slack_webhook_url,
        "alert_discord_webhook_url": check.alert_discord_webhook_url,
        "alert_pagerduty_integration_key": check.alert_pagerduty_integration_key,
        "alert_generic_webhook_url": check.alert_generic_webhook_url,
        "escalation_after_minutes": check.escalation_after_minutes,
        "escalation_cooldown_seconds": check.escalation_cooldown_seconds,
    }
    if payload.name is not None:
        # ensure name uniqueness
        existing = session.exec(select(CheckModel).where(CheckModel.project_id == project_id, CheckModel.name == payload.name, CheckModel.id != check_id)).first()
        if existing:
            raise HTTPException(status_code=409, detail="Check with that name already exists")
        check.name = payload.name
    if payload.url is not None:
        check.url = payload.url
    if payload.interval is not None:
        check.interval = payload.interval
    if payload.expected_interval is not None:
        check.expected_interval = payload.expected_interval
    if payload.grace_period is not None:
        check.grace_period = payload.grace_period
    if payload.host is not None:
        check.host = payload.host
    if payload.port is not None:
        check.port = payload.port
    if payload.dns_record_type is not None:
        check.dns_record_type = payload.dns_record_type
    if payload.script_path is not None:
        check.script_path = payload.script_path
    if payload.script_args is not None:
        check.script_args = json.dumps(payload.script_args)
    if payload.browser_steps is not None:
        check.browser_steps = json.dumps([step.dict(exclude_none=True) for step in payload.browser_steps])
    if payload.browser_capture_screenshot is not None:
        check.browser_capture_screenshot = payload.browser_capture_screenshot
    if payload.latency_threshold_ms is not None:
        check.latency_threshold_ms = payload.latency_threshold_ms
    if payload.region is not None:
        check.region = payload.region
    if payload.alert_enabled is not None:
        check.alert_enabled = payload.alert_enabled
    if payload.alert_after is not None:
        check.alert_after = payload.alert_after
    if payload.alert_cooldown is not None:
        check.alert_cooldown = payload.alert_cooldown
    if payload.alert_sms_enabled is not None:
        check.alert_sms_enabled = payload.alert_sms_enabled
    if payload.alert_oncall_enabled is not None:
        check.alert_oncall_enabled = payload.alert_oncall_enabled
    if payload.alert_slack_enabled is not None:
        check.alert_slack_enabled = payload.alert_slack_enabled
    if payload.alert_discord_enabled is not None:
        check.alert_discord_enabled = payload.alert_discord_enabled
    if payload.alert_pagerduty_enabled is not None:
        check.alert_pagerduty_enabled = payload.alert_pagerduty_enabled
    if payload.alert_webhook_enabled is not None:
        check.alert_webhook_enabled = payload.alert_webhook_enabled
    if payload.alert_sms_to is not None:
        check.alert_sms_to = payload.alert_sms_to
    if payload.alert_oncall_email is not None:
        check.alert_oncall_email = payload.alert_oncall_email
    if payload.alert_slack_webhook_url is not None:
        check.alert_slack_webhook_url = payload.alert_slack_webhook_url
    if payload.alert_discord_webhook_url is not None:
        check.alert_discord_webhook_url = payload.alert_discord_webhook_url
    if payload.alert_pagerduty_integration_key is not None:
        check.alert_pagerduty_integration_key = payload.alert_pagerduty_integration_key
    if payload.alert_generic_webhook_url is not None:
        check.alert_generic_webhook_url = payload.alert_generic_webhook_url
    if payload.escalation_after_minutes is not None:
        check.escalation_after_minutes = payload.escalation_after_minutes
    if payload.escalation_cooldown_seconds is not None:
        check.escalation_cooldown_seconds = payload.escalation_cooldown_seconds
    session.add(check)
    session.commit()
    session.refresh(check)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        after = {
            "name": check.name,
            "url": check.url,
            "interval": check.interval,
            "expected_interval": check.expected_interval,
            "grace_period": check.grace_period,
            "host": check.host,
            "port": check.port,
            "dns_record_type": check.dns_record_type,
            "script_path": getattr(check, "script_path", None),
            "script_args": getattr(check, "script_args", None),
            "browser_steps": getattr(check, "browser_steps", None),
            "browser_capture_screenshot": getattr(check, "browser_capture_screenshot", True),
            "latency_threshold_ms": check.latency_threshold_ms,
            "region": check.region,
            "alert_enabled": check.alert_enabled,
            "alert_after": check.alert_after,
            "alert_cooldown": check.alert_cooldown,
            "alert_sms_enabled": check.alert_sms_enabled,
            "alert_oncall_enabled": check.alert_oncall_enabled,
            "alert_slack_enabled": check.alert_slack_enabled,
            "alert_discord_enabled": check.alert_discord_enabled,
            "alert_pagerduty_enabled": check.alert_pagerduty_enabled,
            "alert_webhook_enabled": check.alert_webhook_enabled,
            "alert_sms_to": check.alert_sms_to,
            "alert_oncall_email": check.alert_oncall_email,
            "alert_slack_webhook_url": check.alert_slack_webhook_url,
            "alert_discord_webhook_url": check.alert_discord_webhook_url,
            "alert_pagerduty_integration_key": check.alert_pagerduty_integration_key,
            "alert_generic_webhook_url": check.alert_generic_webhook_url,
            "escalation_after_minutes": check.escalation_after_minutes,
            "escalation_cooldown_seconds": check.escalation_cooldown_seconds,
        }
        al = AuditLog(actor=actor, action="update_check", target_type="check", target_id=check.id, details=_diff_details(before, after), actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return check


@router.delete("/{check_id}")
def delete_check(project_id: int = Path(..., ge=1), check_id: int = Path(..., ge=1), request: Request = None, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)):
    project = _require_check_write_access(
        project_id,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    session.delete(check)
    session.commit()
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        al = AuditLog(actor=actor, action="delete_check", target_type="check", target_id=check_id, details=None, actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return {"status": "deleted"}


@router.get("/", response_model=List[CheckRead], dependencies=[Depends(limit_public_requests)])
def list_checks(project_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    """List checks for a project (minimal visibility endpoint)."""
    checks = session.exec(select(CheckModel).where(CheckModel.project_id == project_id)).all()
    return checks


@router.get("/{check_id}", response_model=CheckRead, dependencies=[Depends(limit_public_requests)])
def get_check(project_id: int = Path(..., ge=1), check_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    return check


class MaintenanceWindow(StrictBaseModel):
    maintenance_starts_at: Optional[datetime] = None
    maintenance_ends_at: Optional[datetime] = None

    @root_validator
    def _validate_window(cls, values):
        start = values.get("maintenance_starts_at")
        end = values.get("maintenance_ends_at")
        if start and end and end <= start:
            raise ValueError("maintenance_ends_at must be after maintenance_starts_at")
        return values


@router.get("/{check_id}/maintenance", response_model=MaintenanceWindow, dependencies=[Depends(limit_public_requests)])
def get_check_maintenance(project_id: int = Path(..., ge=1), check_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    return MaintenanceWindow(maintenance_starts_at=check.maintenance_starts_at, maintenance_ends_at=check.maintenance_ends_at)


@router.post("/{check_id}/maintenance", response_model=MaintenanceWindow)
def set_check_maintenance(project_id: int = Path(..., ge=1), check_id: int = Path(..., ge=1), payload: MaintenanceWindow = Body(...), request: Request = None, x_admin_token: Optional[str] = Header(None), authorization: Optional[str] = Header(None), x_api_key: Optional[str] = Header(None), session: Session = Depends(get_session)):
    project = _require_check_write_access(
        project_id,
        x_admin_token=x_admin_token,
        authorization=authorization,
        x_api_key=x_api_key,
        session=session,
    )
    check = session.get(CheckModel, check_id)
    if not check or check.project_id != project_id:
        raise HTTPException(status_code=404, detail="Check not found")
    before = {
        "maintenance_starts_at": check.maintenance_starts_at,
        "maintenance_ends_at": check.maintenance_ends_at,
    }
    check.maintenance_starts_at = payload.maintenance_starts_at
    check.maintenance_ends_at = payload.maintenance_ends_at
    session.add(check)
    session.commit()
    session.refresh(check)
    try:
        actor, actor_ip, user_agent = get_audit_context(request, authorization, x_admin_token, session)
        after = {
            "maintenance_starts_at": check.maintenance_starts_at,
            "maintenance_ends_at": check.maintenance_ends_at,
        }
        al = AuditLog(actor=actor, action="set_check_maintenance", target_type="check", target_id=check.id, details=_diff_details(before, after), actor_ip=actor_ip, user_agent=user_agent)
        session.add(al)
        session.commit()
    except Exception:
        pass
    return payload
