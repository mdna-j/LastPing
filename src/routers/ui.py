import html
import json
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select
from pydantic import AnyHttpUrl, EmailStr, ValidationError, parse_obj_as, validator

from ..db import get_session
from ..models import (
    AuditLog,
    Check,
    CheckLease,
    CheckType,
    Event,
    Incident,
    OnCallAlert,
    PredictiveModel,
    PredictiveModelQuality,
    Project,
    RemediationApproval,
    StatusSubscription,
)
from ..deps import limit_public_requests, limit_public_status_requests
from ..notification_queue import refresh_notification_queue_runtime_metrics
from ..runtime_metrics import snapshot_request_metrics
from ..schemas import StrictBaseModel

router = APIRouter(prefix="/ui", tags=["ui"], dependencies=[Depends(limit_public_requests)])


class StatusSubscriptionCreate(StrictBaseModel):
    channel: str
    target: str

    @validator("channel")
    def validate_channel(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"email", "webhook"}:
            raise ValueError("channel must be email or webhook")
        return normalized

    @validator("target")
    def validate_target(cls, value: str, values) -> str:
        channel = values.get("channel")
        if channel == "email":
            return str(parse_obj_as(EmailStr, value)).lower()
        if channel == "webhook":
            return str(parse_obj_as(AnyHttpUrl, value))
        return value


def _public_status_overall(checks: list[Check]) -> str:
    statuses = {(check.status or "").upper() for check in checks}
    if "DOWN" in statuses:
        return "major_outage"
    if "DEGRADED" in statuses:
        return "degraded"
    if statuses:
        return "operational"
    return "unknown"


def _public_link(path: str) -> str:
    base = (os.environ.get("BASE_URL") or "").rstrip("/")
    return f"{base}{path}" if base else path


def _serialize_public_incident(incident: Incident, *, check_name: str, latest_event: Event | None, now: datetime) -> dict:
    ended_at = incident.resolved_at or now
    duration_seconds = max(0, int((ended_at - incident.started_at).total_seconds()))
    share_path = f"/ui/incidents/public/{incident.share_token}" if incident.share_token else None
    return {
        "id": incident.id,
        "check_id": incident.check_id,
        "check_name": check_name,
        "started_at": incident.started_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "status": incident.status,
        "duration_seconds": duration_seconds,
        "share_token": incident.share_token,
        "share_url": _public_link(share_path) if share_path else None,
        "share_path": share_path,
        "latest_event": {
            "type": latest_event.event_type,
            "message": latest_event.message,
            "created_at": latest_event.created_at.isoformat(),
        } if latest_event else None,
    }


def _build_public_status_payload(session: Session, project_id: int) -> dict:
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    now = datetime.utcnow()
    checks = session.exec(
        select(Check).where(Check.project_id == project_id).order_by(Check.name.asc())
    ).all()
    incidents = session.exec(
        select(Incident).where(
            Incident.project_id == project_id,
            Incident.merged_into == None,
        ).order_by(Incident.started_at.desc())
    ).all()

    check_ids = [check.id for check in checks if check.id is not None]
    event_by_check: dict[int, Event] = {}
    event_by_incident: dict[int, Event] = {}

    if check_ids:
        recent_events = session.exec(
            select(Event).where(
                Event.project_id == project_id,
                Event.check_id.in_(check_ids),
            ).order_by(Event.created_at.desc())
        ).all()
        for event in recent_events:
            if event.check_id not in event_by_check:
                event_by_check[event.check_id] = event

        incident_ids = [incident.id for incident in incidents[:16] if incident.id is not None]
        if incident_ids:
            incident_events = session.exec(
                select(Event).where(
                    Event.project_id == project_id,
                    Event.incident_id.in_(incident_ids),
                ).order_by(Event.created_at.desc())
            ).all()
            for event in incident_events:
                if event.incident_id is not None and event.incident_id not in event_by_incident:
                    event_by_incident[event.incident_id] = event

    check_name_by_id = {check.id: check.name for check in checks}
    open_incident_by_check = {
        incident.check_id: incident
        for incident in incidents
        if incident.resolved_at is None
    }

    components = []
    for check in checks:
        last_event = event_by_check.get(check.id)
        active_incident = open_incident_by_check.get(check.id)
        components.append({
            "id": check.id,
            "name": check.name,
            "type": check.type,
            "status": check.status,
            "region": check.region,
            "last_ping": check.last_ping.isoformat() if check.last_ping else None,
            "incident_open": active_incident is not None,
            "incident_id": active_incident.id if active_incident else None,
            "last_event": {
                "type": last_event.event_type,
                "message": last_event.message,
                "created_at": last_event.created_at.isoformat(),
            } if last_event else None,
        })

    open_incidents = [
        _serialize_public_incident(
            incident,
            check_name=check_name_by_id.get(incident.check_id, f"Check {incident.check_id}"),
            latest_event=event_by_incident.get(incident.id),
            now=now,
        )
        for incident in incidents
        if incident.resolved_at is None
    ]
    incident_history = [
        _serialize_public_incident(
            incident,
            check_name=check_name_by_id.get(incident.check_id, f"Check {incident.check_id}"),
            latest_event=event_by_incident.get(incident.id),
            now=now,
        )
        for incident in incidents[:12]
    ]

    down_count = sum(1 for check in checks if (check.status or "").upper() == "DOWN")
    degraded_count = sum(1 for check in checks if (check.status or "").upper() == "DEGRADED")
    up_count = sum(1 for check in checks if (check.status or "").upper() == "UP")

    return {
        "project": {"id": project.id, "name": project.name},
        "summary": {
            "overall_status": _public_status_overall(checks),
            "component_count": len(checks),
            "up_count": up_count,
            "down_count": down_count,
            "degraded_count": degraded_count,
            "open_incident_count": len(open_incidents),
            "generated_at": now.isoformat(),
        },
        "components": components,
        "checks": components,
        "open_incidents": open_incidents,
        "incident_history": incident_history,
    }


def _parse_json_details(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except Exception:
        value = default
    return max(value, minimum)


def _overdue_check_seconds(check: Check, now: datetime) -> int | None:
    if (check.type or "").lower() == CheckType.HEARTBEAT:
        return None
    interval = int(getattr(check, "interval", None) or getattr(check, "expected_interval", None) or 60)
    interval = max(interval, 1)
    due_at = check.next_run
    if due_at is None:
        anchor = check.last_ping or check.created_at
        if anchor is None:
            return None
        due_at = anchor + timedelta(seconds=interval)
    tolerance = max(15, min(interval, 60))
    if due_at + timedelta(seconds=tolerance) >= now:
        return None
    return max(int((now - due_at).total_seconds()), 0)


def _build_platform_observability(session: Session, project_id: int, checks: list[Check], now: datetime) -> dict:
    polling_checks = [check for check in checks if (check.type or "").lower() != CheckType.HEARTBEAT]
    overdue_checks = []
    for check in polling_checks:
        overdue_seconds = _overdue_check_seconds(check, now)
        if overdue_seconds is None:
            continue
        overdue_checks.append(
            {
                "check_id": check.id,
                "name": check.name,
                "region": check.region,
                "overdue_seconds": overdue_seconds,
            }
        )
    overdue_checks.sort(key=lambda row: row["overdue_seconds"], reverse=True)
    max_overdue_seconds = overdue_checks[0]["overdue_seconds"] if overdue_checks else None
    worker_lag_state = "healthy"
    if overdue_checks:
        worker_lag_state = "critical" if (max_overdue_seconds or 0) >= 300 or len(overdue_checks) >= 3 else "warning"
    elif not polling_checks:
        worker_lag_state = "neutral"

    open_oncall_alerts = session.exec(
        select(OnCallAlert).where(
            OnCallAlert.project_id == project_id,
            OnCallAlert.status == "open",
        )
    ).all()
    pending_approvals = session.exec(
        select(RemediationApproval).where(
            RemediationApproval.project_id == project_id,
            RemediationApproval.status == "pending",
        )
    ).all()
    queue_timestamps = [row.created_at for row in open_oncall_alerts if row.created_at] + [
        row.requested_at for row in pending_approvals if row.requested_at
    ]
    oldest_queue_seconds = None
    if queue_timestamps:
        oldest_queue_seconds = max(int((now - min(queue_timestamps)).total_seconds()), 0)
    queue_total = len(open_oncall_alerts) + len(pending_approvals)
    queue_state = "healthy"
    if queue_total > 0:
        queue_state = "critical" if queue_total >= 5 or (oldest_queue_seconds or 0) >= 1800 else "warning"

    notification_queue = refresh_notification_queue_runtime_metrics(session, project_id, now=now)

    retention_interval_seconds = _env_int("RAW_RETENTION_INTERVAL_SECONDS", 86400, minimum=0)
    latest_retention = session.exec(
        select(AuditLog).where(AuditLog.action == "raw_retention_pruned").order_by(AuditLog.created_at.desc())
    ).first()
    retention_details = _parse_json_details(latest_retention.details if latest_retention else None)
    retention_lag_seconds = None
    truncated_tables = []
    if latest_retention and latest_retention.created_at:
        retention_lag_seconds = max(int((now - latest_retention.created_at).total_seconds()), 0)
    if retention_details.get("truncated_tables") and isinstance(retention_details["truncated_tables"], list):
        truncated_tables = [str(item) for item in retention_details["truncated_tables"]]
    retention_state = "healthy"
    if latest_retention is None:
        retention_state = "warning"
    if retention_lag_seconds is not None and retention_interval_seconds > 0:
        if retention_lag_seconds > retention_interval_seconds * 2:
            retention_state = "critical"
        elif retention_lag_seconds > int(retention_interval_seconds * 1.25) or truncated_tables:
            retention_state = "warning"
    elif truncated_tables:
        retention_state = "warning"

    notification_cutoff = now - timedelta(hours=24)
    notification_failures = session.exec(
        select(AuditLog).where(
            AuditLog.action == "notification_failed",
            AuditLog.target_id == project_id,
            AuditLog.created_at >= notification_cutoff,
        ).order_by(AuditLog.created_at.desc())
    ).all()
    failure_channels = {}
    latest_failure_at = notification_failures[0].created_at.isoformat() if notification_failures else None
    for row in notification_failures:
        details = _parse_json_details(row.details)
        channel = str(details.get("channel") or "unknown")
        failure_channels[channel] = failure_channels.get(channel, 0) + 1
    failed_notification_state = "healthy"
    if notification_failures:
        failed_notification_state = "critical" if len(notification_failures) >= 5 else "warning"

    active_models = session.exec(
        select(PredictiveModel).where(
            PredictiveModel.project_id == project_id,
            PredictiveModel.active == True,
        )
    ).all()
    latest_quality_rows = []
    if active_models:
        active_model_ids = [model.id for model in active_models if model.id is not None]
        quality_rows = session.exec(
            select(PredictiveModelQuality).where(
                PredictiveModelQuality.predictive_model_id.in_(active_model_ids)
            ).order_by(PredictiveModelQuality.created_at.desc())
        ).all()
        seen_model_ids = set()
        for row in quality_rows:
            if row.predictive_model_id in seen_model_ids:
                continue
            latest_quality_rows.append(row)
            seen_model_ids.add(row.predictive_model_id)
    latest_quality_at = None
    if latest_quality_rows:
        latest_quality_at = max(row.created_at for row in latest_quality_rows if row.created_at)
    drifted_models = sum(1 for row in latest_quality_rows if (row.status or "").lower() == "drift")
    insufficient_models = sum(1 for row in latest_quality_rows if (row.status or "").lower() == "insufficient_data")
    model_ops_state = "neutral"
    stale_models = False
    if active_models:
        stale_models = latest_quality_at is None or (now - latest_quality_at).total_seconds() > 172800
        if drifted_models > 0:
            model_ops_state = "critical"
        elif stale_models or insufficient_models > 0:
            model_ops_state = "warning"
        else:
            model_ops_state = "healthy"

    api_latency = snapshot_request_metrics()
    api_latency_state = "neutral"
    if api_latency["request_count"] > 0:
        p95_ms = float(api_latency["p95_ms"] or 0.0)
        error_rate = float(api_latency["error_rate"] or 0.0)
        if error_rate >= 0.05 or p95_ms >= 750:
            api_latency_state = "critical"
        elif error_rate > 0 or p95_ms >= 300:
            api_latency_state = "warning"
        else:
            api_latency_state = "healthy"

    return {
        "worker_lag": {
            "state": worker_lag_state,
            "scheduled_checks": len(polling_checks),
            "overdue_checks": len(overdue_checks),
            "max_overdue_seconds": max_overdue_seconds,
            "top_overdue": overdue_checks[:3],
        },
        "queue_health": {
            "state": queue_state,
            "open_oncall_alerts": len(open_oncall_alerts),
            "pending_approvals": len(pending_approvals),
            "oldest_open_seconds": oldest_queue_seconds,
        },
        "notification_queue": notification_queue,
        "retention": {
            "state": retention_state,
            "last_pruned_at": latest_retention.created_at.isoformat() if latest_retention and latest_retention.created_at else None,
            "lag_seconds": retention_lag_seconds,
            "truncated_tables": truncated_tables,
            "interval_seconds": retention_interval_seconds,
        },
        "failed_notifications": {
            "state": failed_notification_state,
            "failures_24h": len(notification_failures),
            "latest_failure_at": latest_failure_at,
            "channels": failure_channels,
        },
        "model_ops": {
            "state": model_ops_state,
            "active_models": len(active_models),
            "latest_quality_at": latest_quality_at.isoformat() if latest_quality_at else None,
            "drifted_models": drifted_models,
            "insufficient_models": insufficient_models,
            "stale": stale_models,
        },
        "api_latency": {
            "state": api_latency_state,
            **api_latency,
        },
    }


@router.get("/incidents", response_class=HTMLResponse)
def incidents_page():
    return """
    <html>
    <head>
      <title>Incidents</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body class="page-incidents">
    <div class="app-shell">
      <aside class="nav-rail">
        <div class="rail-brand">LP</div>
        <nav class="rail-links">
          <a class="rail-link" href="/ui/dashboard">Dashboard</a>
          <a class="rail-link" href="/ui/snapshots">Snapshots</a>
          <a class="rail-link" href="/ui/reports">Reports</a>
          <a class="rail-link" href="/ui/slo">SLO</a>
          <a class="rail-link active" href="/ui/incidents">Incidents</a>
          <a class="rail-link" href="/ui/projects/1/settings">Settings</a>
          <a class="rail-link" href="/ui/projects/1/oncall">On-call</a>
        </nav>
      </aside>

      <main class="main-stage">
        <header class="topbar">
          <div>
            <h1>Incidents</h1>
            <div class="muted">Acknowledge, assign, silence, and collaborate on active incidents.</div>
          </div>
        </header>

        <section id="incidentHeroBanner" class="card hero-banner hero-banner-hidden" role="status" aria-live="polite">
          <div class="hero-banner-icon">!</div>
          <div class="hero-banner-content">
            <div class="hero-banner-title" id="incidentHeroTitle">No active outages</div>
            <div class="hero-banner-sub" id="incidentHeroSub">All checks currently healthy.</div>
          </div>
        </section>

        <section class="card health-strip">
          <div class="health-item">
            <span class="health-label">Last refresh</span>
            <span class="health-value" id="healthLastRefresh">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Active incidents</span>
            <span class="health-value" id="healthActiveIncidents">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Workers online</span>
            <span class="health-value" id="healthWorkersOnline">-</span>
          </div>
          <div class="health-item health-item-wide">
            <span class="health-label">Region health</span>
            <span class="health-value" id="healthRegionHealth">-</span>
          </div>
        </section>

        <section class="card controls-card">
          <div class="row dashboard-controls-row">
            <div class="dashboard-inputs">
              <label>Project: <input id="projectId" value="1" style="width:80px"/></label>
              <label>API Key: <input id="apiKey" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
              <label>User token: <input id="userToken" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
              <label>Admin token: <input id="adminToken" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
            </div>
            <div class="dashboard-actions">
              <button id="loadIncidentsBtn" class="btn">Load</button>
              <a class="btn btn-secondary" href="/ui/dashboard">Dashboard</a>
              <a class="btn btn-secondary" href="/ui/snapshots">Snapshots</a>
              <a class="btn btn-secondary" href="/ui/reports">Reports</a>
            </div>
          </div>
        </section>

        <section id="incidentCards" class="kpi-grid"></section>

        <section class="card">
          <div class="section-head">
            <h3>Incident Feed</h3>
            <div class="muted">Merged incidents appear nested under their primary incident.</div>
          </div>
          <div id="list">Loading...</div>
        </section>
      </main>
    </div>
    <script src="/static/js/ui_shell.js"></script>
    <script src="/static/js/incidents.js"></script>
    </body>
    </html>
    """


@router.get('/checks', response_class=HTMLResponse)
def checks_page():
    return """
    <html>
    <head>
      <title>Checks</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body>
    <h1>Checks</h1>
    <div class="row"><label>Project: <input id="projectId" value="1" style="width:80px"/></label>
      <label>Admin token: <input id="adminToken" type="password" autocomplete="off" placeholder="optional" style="width:240px"/></label>
      <label>User token: <input id="userToken" type="password" autocomplete="off" placeholder="optional" style="width:240px"/></label>
      <button id="loadChecksBtn">Load</button>
    </div>
    <h2>Create Check</h2>
    <div>
      <input id="name" placeholder="Name" />
      <select id="type"><option value="heartbeat">heartbeat</option><option value="http">http</option><option value="tcp">tcp</option><option value="dns">dns</option><option value="browser">browser</option></select>
      <input id="url" placeholder="URL (for http/browser)" style="width:320px" />
      <input id="host" placeholder="Host (for tcp/dns)" style="width:200px" />
      <input id="port" placeholder="Port (tcp)" style="width:100px" />
      <input id="dnsRecordType" placeholder="DNS type (A/AAAA)" style="width:140px" />
      <input id="interval" placeholder="Interval (s)" style="width:120px" />
      <input id="expectedInterval" placeholder="Expected (s)" style="width:140px" />
      <input id="gracePeriod" placeholder="Grace (s)" style="width:120px" />
      <input id="latencyThreshold" placeholder="Latency threshold ms" style="width:180px" />
      <input id="region" placeholder="Region" style="width:140px" />
      <label><input type="checkbox" id="alertEnabled" checked /> Alerts</label>
      <input id="alertAfter" placeholder="Alert after" style="width:120px" />
      <input id="alertCooldown" placeholder="Cooldown (s)" style="width:140px" />
      <button id="createBtn" onclick="createCheck()">Create</button>
    </div>
    <div class="row" style="margin-top:8px">
      <textarea id="browserSteps" placeholder='Browser steps JSON, e.g. [{"action":"fill","selector":"#email","value":"${browser_secret:login_email}"},{"action":"fill","selector":"#password","value":"${browser_secret:login_password}"},{"action":"click","selector":"button[type=submit]"},{"action":"expect_visible","selector":"[data-test=dashboard]"}]' style="width:720px;height:120px"></textarea>
    </div>
    <div class="muted">Supported browser assertions include expect_visible, expect_hidden, expect_title, expect_value, expect_attribute, and expect_count. Secret placeholders use ${browser_secret:name}; env placeholders still use ${LASTPING_BROWSER_*}.</div>
    <div class="row">
      <label><input type="checkbox" id="browserCaptureScreenshot" checked /> Capture screenshot on browser failure</label>
    </div>
    <h3>Alert Routing (Per-check Overrides)</h3>
    <div class="muted">Use inherit to fall back to project settings. Disabled prevents alerts for that channel even if project settings exist.</div>
    <div class="muted">Validation hints: SMS phone uses +country code (e.g. +15551234567). Webhook URLs must be full https:// URLs.</div>
    <div class="row">
      <label>SMS:
        <select id="alertSmsEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertSmsTo" placeholder="+15551234567" style="width:180px"/>
    </div>
    <div class="row">
      <label>On-call:
        <select id="alertOncallEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertOncallEmail" placeholder="oncall@example.com" style="width:240px"/>
    </div>
    <div class="row">
      <label>Slack:
        <select id="alertSlackEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertSlackWebhook" placeholder="Slack webhook URL" style="width:360px"/>
    </div>
    <div class="row">
      <label>Discord:
        <select id="alertDiscordEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertDiscordWebhook" placeholder="Discord webhook URL" style="width:360px"/>
    </div>
    <div class="row">
      <label>PagerDuty:
        <select id="alertPagerdutyEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertPagerdutyKey" placeholder="Integration key" style="width:220px"/>
    </div>
    <div class="row">
      <label>Webhook:
        <select id="alertWebhookEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertGenericWebhook" placeholder="Webhook URL" style="width:360px"/>
    </div>
    <h2>Existing Checks</h2>
    <div id="list">Loading...</div>
    <script src="/static/js/checks.js"></script>
    </body>
    </html>
    """


@router.get('/checks/{check_id}', response_class=HTMLResponse)
def checks_manage_page(check_id: int = Path(..., ge=1)):
    html = """
    <html>
    <head>
      <title>Manage Check __CHECK_ID__</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body>
    <h1>Manage Check __CHECK_ID__</h1>
    <div class="muted">Project: <input id="projectId" value="1" style="width:80px"/></div>
    <div class="muted">Admin token: <input id="adminToken" type="password" autocomplete="off" placeholder="optional" style="width:240px"/></div>
    <div class="muted">User token: <input id="userToken" type="password" autocomplete="off" placeholder="optional" style="width:240px"/></div>
    <h2>Update</h2>
    <div>
      <input id="name" placeholder="Name"/>
      <input id="url" placeholder="URL" style="width:320px"/>
      <input id="host" placeholder="Host" style="width:200px"/>
      <input id="port" placeholder="Port" style="width:100px"/>
      <input id="dnsRecordType" placeholder="DNS type (A/AAAA)" style="width:140px" />
      <input id="interval" placeholder="Interval (s)" style="width:120px" />
      <input id="expectedInterval" placeholder="Expected (s)" style="width:140px" />
      <input id="gracePeriod" placeholder="Grace (s)" style="width:120px" />
      <input id="latencyThreshold" placeholder="Latency threshold ms" style="width:180px" />
      <input id="region" placeholder="Region" style="width:140px" />
      <label><input type="checkbox" id="alertEnabled" /> Alerts</label>
      <input id="alertAfter" placeholder="Alert after" style="width:120px" />
      <input id="alertCooldown" placeholder="Cooldown (s)" style="width:140px" />
      <button id="saveBtn">Save</button>
      <button id="delBtn" style="margin-left:8px;display:none">Delete</button>
    </div>
    <div class="row" style="margin-top:8px">
      <textarea id="browserSteps" placeholder='Browser steps JSON, e.g. [{"action":"fill","selector":"#email","value":"${browser_secret:login_email}"},{"action":"expect_count","selector":".toast","count":1}]' style="width:720px;height:120px"></textarea>
    </div>
    <div class="muted">Use ${browser_secret:name} for stored browser secrets, or ${LASTPING_BROWSER_*} for environment-backed values. Assertions support visibility, title, field value, attribute, URL, and count checks.</div>
    <div class="row">
      <label><input type="checkbox" id="browserCaptureScreenshot" checked /> Capture screenshot on browser failure</label>
    </div>
    <h2>Alert Routing (Per-check Overrides)</h2>
    <div class="muted">Use inherit to fall back to project settings. Disabled prevents alerts for that channel even if project settings exist.</div>
    <div class="muted">Validation hints: SMS phone uses +country code (e.g. +15551234567). Webhook URLs must be full https:// URLs.</div>
    <div class="row">
      <label>SMS:
        <select id="alertSmsEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertSmsTo" placeholder="+15551234567" style="width:180px"/>
    </div>
    <div class="row">
      <label>On-call:
        <select id="alertOncallEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertOncallEmail" placeholder="oncall@example.com" style="width:240px"/>
    </div>
    <div class="row">
      <label>Slack:
        <select id="alertSlackEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertSlackWebhook" placeholder="Slack webhook URL" style="width:360px"/>
    </div>
    <div class="row">
      <label>Discord:
        <select id="alertDiscordEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertDiscordWebhook" placeholder="Discord webhook URL" style="width:360px"/>
    </div>
    <div class="row">
      <label>PagerDuty:
        <select id="alertPagerdutyEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertPagerdutyKey" placeholder="Integration key" style="width:220px"/>
    </div>
    <div class="row">
      <label>Webhook:
        <select id="alertWebhookEnabled">
          <option value="">inherit</option>
          <option value="true">enabled</option>
          <option value="false">disabled</option>
        </select>
      </label>
      <input id="alertGenericWebhook" placeholder="Webhook URL" style="width:360px"/>
    </div>
    <h2>Maintenance</h2>
    <div>
      <label>Start: <input id="mstart" placeholder="2026-01-14T12:00:00"/></label>
      <label>End: <input id="mend" placeholder="2026-01-14T13:00:00"/></label>
      <button id="setMBtn">Set Maintenance</button>
    </div>
    <div id="status">Loading...</div>
    <div id="checkIdHolder" data-check-id="__CHECK_ID__" style="display:none"></div>
    <script src="/static/js/checks_manage.js"></script>
    </body>
    </html>
    """
    return html.replace('__CHECK_ID__', str(check_id))


@router.get("/incidents/public/{token}", response_class=HTMLResponse)
def public_incident_page(token: str):
    safe_token = html.escape(token, quote=True)
    return f"""
    <html>
    <head>
      <title>Shared Incident</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body class="page-status-public">
    <main class="public-status-shell">
      <header class="public-status-header">
        <div class="public-status-brand">LP</div>
        <div>
          <div class="public-status-kicker">Shared incident</div>
          <h1>Incident Timeline</h1>
          <div class="muted">Customer-safe incident summary, timeline, and links back to the public status page.</div>
        </div>
      </header>
      <div id="publicIncidentRoot" class="status-public-root" data-token="{safe_token}"></div>
    </main>
    <script src="/static/js/public_incident.js"></script>
    </body>
    </html>
    """


@router.get("/incidents/{incident_id}", response_class=HTMLResponse)
def incident_detail_page(incident_id: int = Path(..., ge=1)):
    return """
    <html>
    <head>
      <title>Incident</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body class="page-incidents">
    <div class="app-shell">
      <aside class="nav-rail">
        <div class="rail-brand">LP</div>
        <nav class="rail-links">
          <a class="rail-link" href="/ui/dashboard">Dashboard</a>
          <a class="rail-link" href="/ui/snapshots">Snapshots</a>
          <a class="rail-link" href="/ui/reports">Reports</a>
          <a class="rail-link" href="/ui/slo">SLO</a>
          <a class="rail-link active" href="/ui/incidents">Incidents</a>
          <a class="rail-link" href="/ui/projects/1/settings">Settings</a>
          <a class="rail-link" href="/ui/projects/1/oncall">On-call</a>
        </nav>
      </aside>

      <main class="main-stage">
        <header class="topbar">
          <div>
            <h1>Incident Detail</h1>
            <div class="muted">Investigate history, collaborate with notes, and manage the current response owner.</div>
          </div>
        </header>

        <section class="card controls-card">
          <div class="row dashboard-controls-row">
            <div class="dashboard-inputs">
              <label>Project: <input id="projectId" value="1" style="width:80px"/></label>
              <label>API Key: <input id="apiKey" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
              <label>User token: <input id="userToken" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
              <label>Admin token: <input id="adminToken" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
            </div>
            <div class="dashboard-actions">
              <button id="reloadIncidentBtn" class="btn">Refresh</button>
              <button id="jiraTicketBtn" class="btn btn-secondary">Create Jira Ticket</button>
              <button id="shareBtn" class="btn btn-secondary">Create Share Link</button>
              <button id="exportMarkdownBtn" class="btn btn-secondary">Export Markdown</button>
              <button id="exportPdfBtn" class="btn btn-secondary">Export PDF</button>
              <a class="btn btn-secondary" href="/ui/incidents">Back To Incidents</a>
            </div>
          </div>
          <div id="shareInfo" class="muted"></div>
          <div id="jiraTicketInfo" class="muted"></div>
        </section>

        <section id="incidentLifecycleCard" class="card">
          <div class="section-head">
            <h3>Response Workflow</h3>
            <div class="muted">Owner, acknowledgement, silence, and current state for this incident.</div>
          </div>
          <div id="incidentSummary" class="muted">Provide a project API key, user token, or admin token to load incident details.</div>
          <div id="incidentActions" class="row hidden">
            <button id="assignOwnerBtn" class="btn btn-secondary">Assign Owner</button>
            <button id="ackIncidentBtn" class="btn btn-secondary">Acknowledge</button>
            <button id="silenceIncidentBtn" class="btn btn-secondary">Snooze</button>
            <button id="clearSilenceBtn" class="btn btn-secondary">Clear Silence</button>
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Resolution</h3>
            <div class="muted">Capture the closure summary, resolve the incident, or reopen it if the fix did not hold.</div>
          </div>
          <div id="resolutionMeta" class="muted">Resolution details will appear here once the incident is loaded.</div>
          <div class="row">
            <textarea id="resolutionSummary" rows="4" placeholder="Describe the fix, validation steps, and any remaining risk..." style="width:min(760px,100%)"></textarea>
          </div>
          <div class="row">
            <button id="resolveIncidentBtn" class="btn">Resolve Incident</button>
            <button id="reopenIncidentBtn" class="btn btn-secondary hidden">Reopen Incident</button>
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Timeline</h3>
            <div class="muted">Auto-built chronology from events, alerts, merges, acknowledgements, and remediation steps.</div>
          </div>
          <div id="timelineStats" class="muted">Timeline metrics will appear here.</div>
          <div id="incidentTimeline" class="muted">Timeline will load with incident details.</div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Browser Artifacts</h3>
            <div class="muted">Failure screenshots, videos, traces, reports, and HAR captures linked to this incident.</div>
          </div>
          <div id="incidentArtifacts" class="muted">No browser artifacts linked yet.</div>
          <div id="artifactViewer" class="artifact-viewer-shell">
            <div class="muted">Select an artifact to preview screenshots, videos, HAR summaries, or browser execution reports.</div>
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Notes</h3>
            <div class="muted">Capture investigation context, handoffs, and remediation steps.</div>
          </div>
          <div id="notesList" class="muted">No notes yet.</div>
          <div class="row">
            <textarea id="incidentNoteBody" rows="4" placeholder="Add an incident note..." style="width:min(720px,100%)"></textarea>
            <button id="addNoteBtn" class="btn">Add Note</button>
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Events</h3>
            <div class="muted">Split selected events into a new incident when needed.</div>
          </div>
          <div id="detail">Loading...</div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Similar Incidents</h3>
            <div class="muted">Match against historical failures for faster investigation.</div>
          </div>
          <div id="similarIncidents" class="muted">Provide an API key or token to load similar incidents.</div>
        </section>
      </main>
    </div>
    <script src="/static/js/ui_shell.js"></script>
    <script src="/static/js/incident_detail.js"></script>
    </body>
    </html>
    """


@router.get('/snapshots', response_class=HTMLResponse)
def snapshots_page():
    return """
    <html>
    <head>
      <title>Snapshots</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body class="page-snapshots">
    <div class="app-shell">
      <aside class="nav-rail">
        <div class="rail-brand">LP</div>
        <nav class="rail-links">
          <a class="rail-link" href="/ui/dashboard">Dashboard</a>
          <a class="rail-link active" href="/ui/snapshots">Snapshots</a>
          <a class="rail-link" href="/ui/reports">Reports</a>
          <a class="rail-link" href="/ui/slo">SLO</a>
          <a class="rail-link" href="/ui/incidents">Incidents</a>
          <a class="rail-link" id="settingsLink" href="/ui/projects/1/settings">Settings</a>
          <a class="rail-link" href="/ui/projects/1/oncall">On-call</a>
        </nav>
      </aside>

      <main class="main-stage">
        <header class="topbar">
          <div>
            <h1>Snapshots</h1>
            <div class="muted">Time-windowed uptime and MTTR evidence for each check.</div>
          </div>
        </header>

        <section id="incidentHeroBanner" class="card hero-banner hero-banner-hidden" role="status" aria-live="polite">
          <div class="hero-banner-icon">!</div>
          <div class="hero-banner-content">
            <div class="hero-banner-title" id="incidentHeroTitle">No active outages</div>
            <div class="hero-banner-sub" id="incidentHeroSub">All checks currently healthy.</div>
          </div>
        </section>

        <section class="card health-strip">
          <div class="health-item">
            <span class="health-label">Last refresh</span>
            <span class="health-value" id="healthLastRefresh">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Active incidents</span>
            <span class="health-value" id="healthActiveIncidents">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Workers online</span>
            <span class="health-value" id="healthWorkersOnline">-</span>
          </div>
          <div class="health-item health-item-wide">
            <span class="health-label">Region health</span>
            <span class="health-value" id="healthRegionHealth">-</span>
          </div>
        </section>

        <section class="card controls-card">
          <div class="row dashboard-controls-row">
            <div class="dashboard-inputs">
              <label>Project: <input id="projectId" value="1" style="width:80px"/></label>
              <label>API Key: <input id="apiKey" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
              <label>Check: <select id="checkId" style="width:160px"><option value="">(all)</option></select></label>
              <label>Start: <input id="start" placeholder="YYYY-MM-DDTHH:MM:SS" style="width:190px"/></label>
              <label>End: <input id="end" placeholder="YYYY-MM-DDTHH:MM:SS" style="width:190px"/></label>
            </div>
            <div class="dashboard-actions">
              <button id="loadSnapshotsBtn" class="btn">Load</button>
              <button id="availabilityBtn" class="btn btn-secondary">Availability</button>
              <button id="savePrefsBtn" class="btn btn-secondary">Save Prefs</button>
              <button id="exportCsvBtn" class="btn btn-secondary">Export CSV</button>
              <a class="btn btn-secondary" href="/ui/dashboard">Dashboard</a>
              <a class="btn btn-secondary" href="/ui/reports">Reports</a>
              <a class="btn btn-secondary" href="/ui/incidents">Incidents</a>
            </div>
          </div>
          <div id="snapshotPresetRow" class="row">
            <button class="btn btn-secondary" id="p1h">Last 1h</button>
            <button class="btn btn-secondary" id="p6h">Last 6h</button>
            <button class="btn btn-secondary" id="p24h">Last 24h</button>
            <button class="btn btn-secondary" id="p7d">Last 7d</button>
          </div>
        </section>

        <section id="snapshotCards" class="kpi-grid"></section>

        <section class="card table-card">
          <div class="section-head">
            <h3>Snapshot Rows</h3>
            <div class="muted">Includes recent uptime snapshots and summary metrics.</div>
          </div>
          <div id="list">Loading...</div>
        </section>

        <section id="snapshotUptimeChartCard" class="card chart-card">
          <div class="section-head">
            <h3>Availability Report</h3>
            <div class="muted">Aggregated SLO/SLA status for the selected range.</div>
          </div>
          <div id="availability" class="card">Run Availability to view report.</div>
          <div class="chart-frame" style="margin-top:12px">
            <canvas id="snapshotUptimeChart" height="140"></canvas>
            <div id="snapshotChartEmpty" class="chart-empty hidden">No recent data for selected range.</div>
          </div>
        </section>
      </main>
    </div>
    <script src="/static/js/vendor/chart.min.js"></script>
    <script src="/static/js/ui_shell.js"></script>
    <script src="/static/js/snapshots.js"></script>
    </body>
    </html>
    """


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page():
    return """
    <html>
    <head>
      <title>Project Dashboard</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body class="page-dashboard">
    <div class="app-shell">
      <aside class="nav-rail">
        <div class="rail-brand">LP</div>
        <nav class="rail-links">
          <a class="rail-link active" href="/ui/dashboard">Dashboard</a>
          <a class="rail-link" href="/ui/snapshots">Snapshots</a>
          <a class="rail-link" href="/ui/reports">Reports</a>
          <a class="rail-link" href="/ui/slo">SLO</a>
          <a class="rail-link" href="/ui/incidents">Incidents</a>
          <a class="rail-link" href="/ui/projects/1/settings">Settings</a>
          <a class="rail-link" href="/ui/projects/1/oncall">On-call</a>
        </nav>
      </aside>

      <main class="main-stage">
        <header class="topbar">
          <div>
            <h1>Project Dashboard</h1>
            <div class="muted">Live health, latency, incidents, and forward-looking risk signals.</div>
          </div>
        </header>

        <section id="incidentHeroBanner" class="card hero-banner hero-banner-hidden" role="status" aria-live="polite">
          <div class="hero-banner-icon">!</div>
          <div class="hero-banner-content">
            <div class="hero-banner-title" id="incidentHeroTitle">No active outages</div>
            <div class="hero-banner-sub" id="incidentHeroSub">All checks currently healthy.</div>
          </div>
        </section>

        <section class="card health-strip">
          <div class="health-item">
            <span class="health-label">Last refresh</span>
            <span class="health-value" id="healthLastRefresh">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Active incidents</span>
            <span class="health-value" id="healthActiveIncidents">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Workers online</span>
            <span class="health-value" id="healthWorkersOnline">-</span>
          </div>
          <div class="health-item health-item-wide">
            <span class="health-label">Region health</span>
            <span class="health-value" id="healthRegionHealth">-</span>
          </div>
        </section>

        <section class="card controls-card">
          <div class="row dashboard-controls-row">
            <div class="dashboard-inputs">
              <label>Project: <input id="projectId" value="1" style="width:64px"/></label>
              <label>API Key: <input id="apiKey" type="password" autocomplete="off" placeholder="optional" style="width:210px"/></label>
              <label>Start: <input id="start" placeholder="YYYY-MM-DDTHH:MM:SS" style="width:180px"/></label>
              <label>End: <input id="end" placeholder="YYYY-MM-DDTHH:MM:SS" style="width:180px"/></label>
            </div>
            <div class="dashboard-actions">
              <button id="loadDashboardBtn" class="btn">Load</button>
              <a class="btn btn-secondary" href="/ui/snapshots">Snapshots</a>
              <a class="btn btn-secondary" href="/ui/reports">Reports</a>
              <a class="btn btn-secondary" href="/ui/incidents">Incidents</a>
            </div>
          </div>
        </section>

        <section id="cards" class="kpi-grid"></section>

        <section class="card platform-shell">
          <div class="section-head">
            <h3>LastPing Platform</h3>
            <div class="muted">Worker lag, queue health, retention, notifications, model ops, and API latency.</div>
          </div>
          <div id="platformCards" class="kpi-grid"></div>
        </section>

        <section class="intelligence-grid">
          <article class="card intelligence-card intelligence-summary-card">
            <div class="section-head">
              <h3>Intelligence</h3>
              <div class="muted" id="intelligenceMeta">Recent signal digest</div>
            </div>
            <div id="intelligenceSummary" class="muted">Loading intelligence signals...</div>
          </article>

          <article class="card intelligence-card">
            <h3>Predictive Alerts</h3>
            <div class="muted">Forward-looking signals based on recent failure trends.</div>
            <div id="predictiveList" class="muted">Provide API key to load predictive alerts.</div>
          </article>

          <article class="card intelligence-card">
            <h3>Anomaly Warnings</h3>
            <div class="muted">Unexpected spikes versus recent baseline.</div>
            <div id="anomalyList" class="muted">Provide API key to load anomaly warnings.</div>
          </article>
        </section>

        <section class="chart-grid">
          <article id="uptimeChartCard" class="card chart-card">
            <div class="section-head">
              <h3>Uptime (recent)</h3>
              <div class="muted">Last snapshots</div>
            </div>
            <div class="chart-frame">
              <canvas id="uptimeChart" height="140"></canvas>
              <div id="uptimeChartEmpty" class="chart-empty hidden">No recent data for selected range.</div>
            </div>
          </article>
          <article id="trendChartCard" class="card chart-card">
            <div class="section-head">
              <h3>Failure Trends</h3>
              <div class="muted">Daily down events</div>
            </div>
            <div class="chart-frame">
              <canvas id="trendChart" height="140"></canvas>
              <div id="trendChartEmpty" class="chart-empty hidden">No recent data for selected range.</div>
            </div>
          </article>
        </section>

        <section class="card table-card">
          <div class="section-head">
            <h3>Checks</h3>
            <div class="muted">Current monitor state</div>
          </div>
          <table id="checksTable">
            <thead><tr><th>Name</th><th>Type</th><th>Status</th><th>Last Ping</th><th>Latency</th><th>Region</th></tr></thead>
            <tbody></tbody>
          </table>
        </section>

        <section class="incident-grid">
          <article class="card incident-card">
            <h3>Latest Incidents</h3>
            <div id="incidentsList" class="muted">Provide API key to load incidents.</div>
          </article>
        </section>
      </main>
    </div>

    <script src="/static/js/vendor/chart.min.js"></script>
    <script src="/static/js/ui_shell.js"></script>
    <script src="/static/js/dashboard.js"></script>
    </body>
    </html>
    """


@router.get("/dashboard/health")
def dashboard_health(project_id: int = Query(1, ge=1), session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    now = datetime.utcnow()
    checks = session.exec(select(Check).where(Check.project_id == project_id)).all()

    open_incidents = session.exec(
        select(Incident).where(Incident.project_id == project_id, Incident.resolved_at == None)
    ).all()

    earliest_open_by_check = {}
    for inc in open_incidents:
        cur = earliest_open_by_check.get(inc.check_id)
        if cur is None or (inc.started_at and inc.started_at < cur.started_at):
            earliest_open_by_check[inc.check_id] = inc

    active_worker_rows = session.exec(
        select(CheckLease.lease_owner)
        .join(Check, Check.id == CheckLease.check_id)
        .where(
            Check.project_id == project_id,
            CheckLease.lease_owner != None,
            CheckLease.lease_expires_at != None,
            CheckLease.lease_expires_at > now,
        )
    ).all()
    worker_ids = sorted({w for w in active_worker_rows if w})

    region_stats = {}
    for check in checks:
        raw_region = (check.region or "").strip()
        if not raw_region:
            region = "global"
        elif raw_region.lower() in ("*", "all", "any"):
            region = "any"
        elif "," in raw_region or " " in raw_region:
            region = "multi"
        else:
            region = raw_region

        bucket = region_stats.setdefault(region, {"up": 0, "down": 0, "degraded": 0, "total": 0})
        bucket["total"] += 1
        st = (check.status or "").lower()
        if st == "up":
            bucket["up"] += 1
        elif st == "down":
            bucket["down"] += 1
        elif st == "degraded":
            bucket["degraded"] += 1

    region_items = []
    summary_parts = []
    for name in sorted(region_stats.keys()):
        vals = region_stats[name]
        region_items.append({"name": name, **vals})
        if vals["down"] > 0:
            summary_parts.append(f"{name}: {vals['down']} down")
        elif vals["degraded"] > 0:
            summary_parts.append(f"{name}: {vals['degraded']} degraded")
        else:
            summary_parts.append(f"{name}: healthy")

    down_checks = []
    for check in checks:
        if (check.status or "").lower() != "down":
            continue
        started_at = None
        inc = earliest_open_by_check.get(check.id)
        if inc and inc.started_at:
            started_at = inc.started_at
        elif check.last_ping:
            started_at = check.last_ping
        elif check.created_at:
            started_at = check.created_at

        down_seconds = None
        if started_at:
            try:
                down_seconds = max(int((now - started_at).total_seconds()), 0)
            except Exception:
                down_seconds = None

        down_checks.append(
            {
                "id": check.id,
                "name": check.name,
                "started_at": started_at.isoformat() if started_at else None,
                "down_seconds": down_seconds,
            }
        )

    down_checks.sort(
        key=lambda c: c["down_seconds"] if c["down_seconds"] is not None else -1,
        reverse=True,
    )
    primary_down = down_checks[0] if down_checks else None

    return {
        "project_id": project_id,
        "last_refresh": now.isoformat(),
        "active_incidents": len(open_incidents),
        "workers_online": len(worker_ids),
        "worker_ids": worker_ids,
        "down_checks_count": len(down_checks),
        "primary_down_check": primary_down,
        "down_checks": down_checks[:5],
        "region_health_summary": " | ".join(summary_parts) if summary_parts else "No checks",
        "regions": region_items,
        "platform": _build_platform_observability(session, project_id, checks, now),
    }


@router.get("/reports", response_class=HTMLResponse)
def reports_page():
    return """
    <html>
    <head>
      <title>Availability Reports</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body class="page-reports">
    <div class="app-shell">
      <aside class="nav-rail">
        <div class="rail-brand">LP</div>
        <nav class="rail-links">
          <a class="rail-link" href="/ui/dashboard">Dashboard</a>
          <a class="rail-link" href="/ui/snapshots">Snapshots</a>
          <a class="rail-link" href="/ui/slo">SLO</a>
          <a class="rail-link active" href="/ui/reports">Reports</a>
          <a class="rail-link" href="/ui/incidents">Incidents</a>
          <a class="rail-link" href="/ui/projects/1/settings">Settings</a>
          <a class="rail-link" href="/ui/projects/1/oncall">On-call</a>
        </nav>
      </aside>

      <main class="main-stage">
        <header class="topbar">
          <div>
            <h1>Availability Reports</h1>
            <div class="muted">Daily, monthly, and quarterly uptime rollups with SLO/SLA outcomes.</div>
          </div>
        </header>

        <section id="incidentHeroBanner" class="card hero-banner hero-banner-hidden" role="status" aria-live="polite">
          <div class="hero-banner-icon">!</div>
          <div class="hero-banner-content">
            <div class="hero-banner-title" id="incidentHeroTitle">No active outages</div>
            <div class="hero-banner-sub" id="incidentHeroSub">All checks currently healthy.</div>
          </div>
        </section>

        <section class="card health-strip">
          <div class="health-item">
            <span class="health-label">Last refresh</span>
            <span class="health-value" id="healthLastRefresh">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Active incidents</span>
            <span class="health-value" id="healthActiveIncidents">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Workers online</span>
            <span class="health-value" id="healthWorkersOnline">-</span>
          </div>
          <div class="health-item health-item-wide">
            <span class="health-label">Region health</span>
            <span class="health-value" id="healthRegionHealth">-</span>
          </div>
        </section>

        <section class="card controls-card">
          <div class="row dashboard-controls-row">
            <div class="dashboard-inputs">
              <label>Project: <input id="projectId" value="1" style="width:80px"/></label>
              <label>API Key: <input id="apiKey" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
              <label>Granularity:
                <select id="rollup" style="width:140px">
                  <option value="day">daily</option>
                  <option value="month">monthly</option>
                  <option value="quarter">quarterly</option>
                </select>
              </label>
              <label>Check: <select id="checkId" style="width:140px"><option value="">(all)</option></select></label>
              <label>Start: <input id="start" placeholder="YYYY-MM-DDTHH:MM:SS" style="width:190px"/></label>
              <label>End: <input id="end" placeholder="YYYY-MM-DDTHH:MM:SS" style="width:190px"/></label>
            </div>
            <div class="dashboard-actions">
              <button id="loadBtn" class="btn">Load</button>
              <button id="exportBtn" class="btn btn-secondary">Export CSV</button>
              <a class="btn btn-secondary" href="/ui/dashboard">Dashboard</a>
              <a class="btn btn-secondary" href="/ui/snapshots">Snapshots</a>
              <a class="btn btn-secondary" href="/ui/slo">SLO</a>
              <a class="btn btn-secondary" href="/ui/incidents">Incidents</a>
            </div>
          </div>
          <div class="row">
            <button class="btn btn-secondary" id="p7d">Last 7d</button>
            <button class="btn btn-secondary" id="p30d">Last 30d</button>
            <button class="btn btn-secondary" id="p90d">Last 90d</button>
            <button class="btn btn-secondary" id="p180d">Last 180d</button>
          </div>
        </section>

        <section id="reportCards" class="kpi-grid"></section>

        <section class="card">
          <div class="section-head">
            <h3>Error Budget / Burn Rate</h3>
            <div class="muted">SRE-style budget consumption and multi-window burn-rate detection.</div>
          </div>
          <div id="burnRateCards" class="kpi-grid"></div>
        </section>

        <section id="reportChartCard" class="card chart-card">
          <div class="section-head">
            <h3>Project Availability</h3>
            <div class="muted">Trend view for the selected period granularity.</div>
          </div>
          <div class="chart-frame">
            <canvas id="reportChart" height="140"></canvas>
            <div id="reportChartEmpty" class="chart-empty hidden">No recent data for selected range.</div>
          </div>
        </section>

        <section class="card table-card">
          <div class="section-head">
            <h3>Summary Table</h3>
            <div class="muted">Per-period uptime and SLO/SLA compliance.</div>
          </div>
          <table id="reportTable">
            <thead><tr><th>Date</th><th>Uptime %</th><th>SLO</th><th>SLA</th></tr></thead>
            <tbody></tbody>
          </table>
        </section>
      </main>
    </div>
    <script src="/static/js/vendor/chart.min.js"></script>
    <script src="/static/js/ui_shell.js"></script>
    <script src="/static/js/report.js"></script>
    </body>
    </html>
    """


@router.get("/slo", response_class=HTMLResponse)
def slo_dashboard_page():
    return """
    <html>
    <head>
      <title>SLO Dashboard</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body class="page-reports">
    <div class="app-shell">
      <aside class="nav-rail">
        <div class="rail-brand">LP</div>
        <nav class="rail-links">
          <a class="rail-link" href="/ui/dashboard">Dashboard</a>
          <a class="rail-link" href="/ui/snapshots">Snapshots</a>
          <a class="rail-link active" href="/ui/slo">SLO</a>
          <a class="rail-link" href="/ui/reports">Reports</a>
          <a class="rail-link" href="/ui/incidents">Incidents</a>
          <a class="rail-link" href="/ui/projects/1/settings">Settings</a>
          <a class="rail-link" href="/ui/projects/1/oncall">On-call</a>
        </nav>
      </aside>

      <main class="main-stage">
        <header class="topbar">
          <div>
            <h1>SLO / Error Budget</h1>
            <div class="muted">Budget remaining, multi-window burn rates, component split, offenders, and historical compliance.</div>
          </div>
        </header>

        <section id="incidentHeroBanner" class="card hero-banner hero-banner-hidden" role="status" aria-live="polite">
          <div class="hero-banner-icon">!</div>
          <div class="hero-banner-content">
            <div class="hero-banner-title" id="incidentHeroTitle">No active outages</div>
            <div class="hero-banner-sub" id="incidentHeroSub">All checks currently healthy.</div>
          </div>
        </section>

        <section class="card health-strip">
          <div class="health-item">
            <span class="health-label">Last refresh</span>
            <span class="health-value" id="healthLastRefresh">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Active incidents</span>
            <span class="health-value" id="healthActiveIncidents">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Workers online</span>
            <span class="health-value" id="healthWorkersOnline">-</span>
          </div>
          <div class="health-item health-item-wide">
            <span class="health-label">Region health</span>
            <span class="health-value" id="healthRegionHealth">-</span>
          </div>
        </section>

        <section class="card controls-card">
          <div class="row dashboard-controls-row">
            <div class="dashboard-inputs">
              <label>Project: <input id="projectId" value="1" style="width:80px"/></label>
              <label>API Key: <input id="apiKey" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
              <label>Start: <input id="start" placeholder="YYYY-MM-DDTHH:MM:SS" style="width:190px"/></label>
              <label>End: <input id="end" placeholder="YYYY-MM-DDTHH:MM:SS" style="width:190px"/></label>
            </div>
            <div class="dashboard-actions">
              <button id="loadSloBtn" class="btn">Load</button>
              <a class="btn btn-secondary" href="/ui/dashboard">Dashboard</a>
              <a class="btn btn-secondary" href="/ui/reports">Reports</a>
              <a class="btn btn-secondary" href="/ui/incidents">Incidents</a>
            </div>
          </div>
          <div class="row">
            <button class="btn btn-secondary" id="slo7d">Last 7d</button>
            <button class="btn btn-secondary" id="slo30d">Last 30d</button>
            <button class="btn btn-secondary" id="slo90d">Last 90d</button>
            <button class="btn btn-secondary" id="slo180d">Last 180d</button>
          </div>
        </section>

        <section id="sloSummaryCards" class="kpi-grid"></section>

        <section class="card">
          <div class="section-head">
            <h3>Burn Windows</h3>
            <div class="muted">Error-budget burn over 1h, 6h, and 24h windows.</div>
          </div>
          <div id="sloBurnCards" class="kpi-grid"></div>
        </section>

        <section class="chart-grid">
          <article id="sloHistoryChartCard" class="card chart-card">
            <div class="section-head">
              <h3>Historical SLO Compliance</h3>
              <div class="muted">Daily uptime versus target across the selected range.</div>
            </div>
            <div class="chart-frame">
              <canvas id="sloHistoryChart" height="140"></canvas>
              <div id="sloHistoryChartEmpty" class="chart-empty hidden">No recent data for selected range.</div>
            </div>
          </article>

          <article id="sloComponentChartCard" class="card chart-card">
            <div class="section-head">
              <h3>Component Budget Split</h3>
              <div class="muted">Share of consumed error budget by component.</div>
            </div>
            <div class="chart-frame">
              <canvas id="sloComponentChart" height="140"></canvas>
              <div id="sloComponentChartEmpty" class="chart-empty hidden">No component budget pressure in selected range.</div>
            </div>
          </article>
        </section>

        <section class="insight-grid">
          <article class="card">
            <div class="section-head">
              <h3>Top Offenders</h3>
              <div class="muted">Components consuming the most budget.</div>
            </div>
            <div id="sloTopOffenders" class="muted">Load the dashboard to see current offenders.</div>
          </article>
          <article class="card">
            <div class="section-head">
              <h3>Compliance Summary</h3>
              <div class="muted">Daily and monthly SLO pass / miss counts.</div>
            </div>
            <div id="sloComplianceSummary" class="muted">No compliance summary loaded yet.</div>
          </article>
          <article class="card">
            <div class="section-head">
              <h3>Monthly Rollups</h3>
              <div class="muted">Longer-term compliance periods from rollups.</div>
            </div>
            <div id="sloMonthlySummary" class="muted">No monthly rollups loaded yet.</div>
          </article>
        </section>

        <section class="card table-card">
          <div class="section-head">
            <h3>Component Budget Table</h3>
            <div class="muted">Per-component remaining budget, burn contribution, and compliance.</div>
          </div>
          <div class="table-wrap">
            <table id="sloComponentTable">
              <thead>
                <tr>
                  <th>Component</th>
                  <th>Uptime %</th>
                  <th>Consumed %</th>
                  <th>Remaining %</th>
                  <th>Budget Share</th>
                  <th>SLO</th>
                  <th>SLA</th>
                </tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
    <script src="/static/js/vendor/chart.min.js"></script>
    <script src="/static/js/ui_shell.js"></script>
    <script src="/static/js/slo_dashboard.js"></script>
    </body>
    </html>
    """


@router.get("/projects/{project_id}/settings", response_class=HTMLResponse)
def project_settings_page(project_id: int = Path(..., ge=1)):
    return f"""
    <html>
    <head>
      <title>Project Settings</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body class="page-settings">
    <div class="app-shell">
      <aside class="nav-rail">
        <div class="rail-brand">LP</div>
        <nav class="rail-links">
          <a class="rail-link" href="/ui/dashboard">Dashboard</a>
          <a class="rail-link" href="/ui/snapshots">Snapshots</a>
          <a class="rail-link" href="/ui/slo">SLO</a>
          <a class="rail-link" href="/ui/reports">Reports</a>
          <a class="rail-link" href="/ui/incidents">Incidents</a>
          <a class="rail-link active" href="/ui/projects/{project_id}/settings">Settings</a>
          <a class="rail-link" href="/ui/projects/{project_id}/oncall">On-call</a>
        </nav>
      </aside>

      <main class="main-stage">
        <header class="topbar">
          <div>
            <h1>Project Settings</h1>
            <div class="muted">Manage SLO/SLA targets and project-level alerting defaults.</div>
          </div>
        </header>

        <section id="incidentHeroBanner" class="card hero-banner hero-banner-hidden" role="status" aria-live="polite">
          <div class="hero-banner-icon">!</div>
          <div class="hero-banner-content">
            <div class="hero-banner-title" id="incidentHeroTitle">No active outages</div>
            <div class="hero-banner-sub" id="incidentHeroSub">All checks currently healthy.</div>
          </div>
        </section>

        <section class="card health-strip">
          <div class="health-item">
            <span class="health-label">Last refresh</span>
            <span class="health-value" id="healthLastRefresh">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Active incidents</span>
            <span class="health-value" id="healthActiveIncidents">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Workers online</span>
            <span class="health-value" id="healthWorkersOnline">-</span>
          </div>
          <div class="health-item health-item-wide">
            <span class="health-label">Region health</span>
            <span class="health-value" id="healthRegionHealth">-</span>
          </div>
        </section>

        <section class="card controls-card">
          <div class="row dashboard-controls-row">
            <div class="dashboard-inputs">
              <label>Project: <input id="projectId" value="{project_id}" style="width:80px"/></label>
              <label>API Key: <input id="apiKey" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
              <label>Admin token: <input id="adminToken" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
            </div>
            <div class="dashboard-actions">
              <button id="loadBtn" class="btn">Load</button>
              <button id="saveBtn" class="btn">Save</button>
              <a class="btn btn-secondary" href="/ui/projects/{project_id}/oncall">On-call</a>
              <a class="btn btn-secondary" href="/ui/projects/{project_id}/remediation">Remediation</a>
            </div>
          </div>
        </section>

        <section id="settingsCards" class="kpi-grid"></section>

        <section class="card">
          <div class="section-head">
            <h3>SLO / SLA</h3>
            <div class="muted">Used by reporting and compliance views.</div>
          </div>
          <div class="row">
            <label>SLO target (%): <input id="sloTarget" placeholder="99.9" style="width:120px"/></label>
            <label>SLA target (%): <input id="slaTarget" placeholder="99.5" style="width:120px"/></label>
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>On-call / SMS Defaults</h3>
            <div class="muted">Project-level routing fallbacks for checks using inherit.</div>
          </div>
          <div class="row">
            <label><input type="checkbox" id="smsEnabled" /> SMS enabled</label>
            <label>SMS To: <input id="smsTo" placeholder="+15551234567" style="width:180px"/></label>
            <label><input type="checkbox" id="oncallEnabled" /> On-call email enabled</label>
            <label>On-call Email: <input id="oncallEmail" placeholder="oncall@example.com" style="width:240px"/></label>
          </div>
          <div class="muted">SMS requires Twilio env vars (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM).</div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Jira</h3>
            <div class="muted">Configure Jira issue creation and inbound webhook sync for operator tracking.</div>
          </div>
          <div class="row">
            <label>Base URL:
              <input id="jiraBaseUrl" placeholder="https://your-domain.atlassian.net" style="width:300px"/>
            </label>
            <label>User Email:
              <input id="jiraUserEmail" placeholder="ops@example.com" style="width:220px"/>
            </label>
          </div>
          <div class="row">
            <label>API Token:
              <input id="jiraApiToken" type="password" autocomplete="off" placeholder="(optional)" style="width:220px"/>
            </label>
            <button id="clearJiraApiTokenBtn" class="btn btn-secondary" type="button">Clear Token</button>
            <div class="status-inline"><strong>Stored token:</strong> <span id="jiraApiTokenStatus" class="muted">unknown</span></div>
          </div>
          <div class="muted">Leave the token blank to keep the current secret. Enter a new token to replace it.</div>
          <div class="row">
            <label>Token expires at:
              <input id="jiraTokenExpiresAt" placeholder="YYYY-MM-DDTHH:MM:SSZ" style="width:220px"/>
            </label>
            <label>Rotate every (days):
              <input id="jiraTokenRotationIntervalDays" placeholder="30" style="width:120px"/>
            </label>
            <label>Grace (minutes):
              <input id="jiraTokenGraceMinutes" value="60" style="width:110px"/>
            </label>
          </div>
          <div class="row">
            <div class="status-inline"><strong>Last used:</strong> <span id="jiraTokenLastUsedAt" class="muted">never</span></div>
            <div class="status-inline"><strong>Last rotated:</strong> <span id="jiraTokenLastRotatedAt" class="muted">unknown</span></div>
            <div class="status-inline"><strong>Rotation due:</strong> <span id="jiraTokenRotationDueAt" class="muted">none</span></div>
            <div class="status-inline"><strong>Rollover until:</strong> <span id="jiraTokenRolloverUntil" class="muted">none</span></div>
          </div>
          <div class="row">
            <label>Project Key:
              <input id="jiraProjectKey" placeholder="OPS" style="width:120px"/>
            </label>
            <label>Issue Type:
              <input id="jiraIssueType" placeholder="Task" style="width:160px"/>
            </label>
          </div>
          <div class="row">
            <div class="status-inline"><strong>Inbound webhook:</strong> <code id="jiraWebhookUrl">/integrations/jira/webhook</code></div>
          </div>
          <div class="row">
            <div class="status-inline"><strong>Timestamp header:</strong> <code id="jiraTimestampHeader">X-Jira-Webhook-Timestamp</code></div>
            <div class="status-inline"><strong>Signature header:</strong> <code id="jiraSignatureHeader">X-Jira-Webhook-Signature</code></div>
            <div class="status-inline"><strong>Webhook secret:</strong> <span id="jiraSecretStatus" class="muted">unknown</span></div>
            <div class="status-inline"><strong>Latest sync:</strong> <span id="jiraLastSync" class="muted">none yet</span></div>
          </div>
          <div class="status-inline"><strong>Signing scheme:</strong> <code id="jiraSignatureScheme">HMAC-SHA256 over '&lt;timestamp&gt;.&lt;raw_body&gt;'</code></div>
          <div id="jiraSettingsHint" class="muted">Create Jira issues directly from incident detail pages once project credentials are configured.</div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>PagerDuty</h3>
            <div class="muted">Configure outbound delivery and verify inbound webhook sync readiness.</div>
          </div>
          <div class="row">
            <label>Integration Key:
              <input id="pagerdutyIntegrationKey" type="password" autocomplete="off" placeholder="(optional)" style="width:260px"/>
            </label>
            <button id="clearPagerdutyIntegrationKeyBtn" class="btn btn-secondary" type="button">Clear Key</button>
            <div class="status-inline"><strong>Stored key:</strong> <span id="pagerdutyIntegrationKeyStatus" class="muted">unknown</span></div>
            <button id="sendPagerdutyTestBtn" class="btn btn-secondary">Send Test Delivery</button>
          </div>
          <div class="muted">Leave the integration key blank to keep the current secret. Enter a new key to replace it.</div>
          <div class="row">
            <label>Key expires at:
              <input id="pagerdutyExpiresAt" placeholder="YYYY-MM-DDTHH:MM:SSZ" style="width:220px"/>
            </label>
            <label>Rotate every (days):
              <input id="pagerdutyRotationIntervalDays" placeholder="30" style="width:120px"/>
            </label>
            <label>Grace (minutes):
              <input id="pagerdutyGraceMinutes" value="60" style="width:110px"/>
            </label>
          </div>
          <div class="row">
            <div class="status-inline"><strong>Last used:</strong> <span id="pagerdutyLastUsedAt" class="muted">never</span></div>
            <div class="status-inline"><strong>Last rotated:</strong> <span id="pagerdutyLastRotatedAt" class="muted">unknown</span></div>
            <div class="status-inline"><strong>Rotation due:</strong> <span id="pagerdutyRotationDueAt" class="muted">none</span></div>
            <div class="status-inline"><strong>Rollover until:</strong> <span id="pagerdutyRolloverUntil" class="muted">none</span></div>
          </div>
          <div class="row">
            <div class="status-inline"><strong>Inbound webhook:</strong> <code id="pagerdutyWebhookUrl">/integrations/pagerduty/webhook</code></div>
          </div>
          <div class="row">
            <div class="status-inline"><strong>Timestamp header:</strong> <code id="pagerdutyTimestampHeader">X-PagerDuty-Webhook-Timestamp</code></div>
            <div class="status-inline"><strong>Signature header:</strong> <code id="pagerdutySignatureHeader">X-PagerDuty-Webhook-Signature</code></div>
            <div class="status-inline"><strong>Webhook secret:</strong> <span id="pagerdutySecretStatus" class="muted">unknown</span></div>
            <div class="status-inline"><strong>Latest sync:</strong> <span id="pagerdutyLastSync" class="muted">none yet</span></div>
          </div>
          <div class="status-inline"><strong>Signing scheme:</strong> <code id="pagerdutySignatureScheme">HMAC-SHA256 over '&lt;timestamp&gt;.&lt;raw_body&gt;'</code></div>
          <div id="pagerdutyTestResult" class="muted">Use test delivery to send a trigger and immediate resolve event to PagerDuty.</div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Delivery Queue Ops</h3>
            <div class="muted">Track full queue pressure here, then inspect filtered deliveries to replay, cancel, or poison specific jobs.</div>
          </div>
          <div class="row">
            <label>Status:
              <select id="deliveryStatusFilter" style="width:180px">
                <option value="actionable">actionable</option>
                <option value="all">all</option>
                <option value="queued">queued</option>
                <option value="retry">retrying</option>
                <option value="processing">processing</option>
                <option value="dead">dead</option>
                <option value="delivered">delivered</option>
              </select>
            </label>
            <label>Channel:
              <select id="deliveryChannelFilter" style="width:170px">
                <option value="all">all</option>
                <option value="email">email</option>
                <option value="webhook">webhook</option>
                <option value="slack">slack</option>
                <option value="discord">discord</option>
                <option value="pagerduty">pagerduty</option>
                <option value="jira">jira</option>
              </select>
            </label>
            <label>Rows:
              <select id="deliveryLimit" style="width:120px">
                <option value="20">20</option>
                <option value="40" selected>40</option>
                <option value="80">80</option>
              </select>
            </label>
            <button id="refreshDeliveryQueueBtn" class="btn btn-secondary" type="button">Refresh Queue</button>
          </div>
          <div id="deliveryQueueCards" class="kpi-grid"></div>
          <div id="notificationFailures" class="muted">Loading delivery queue...</div>
          <div id="deliveryInspectPanel" class="queue-inspect-card hidden"></div>
        </section>
      </main>
    </div>
    <script src="/static/js/ui_shell.js"></script>
    <script src="/static/js/project_settings.js"></script>
    </body>
    </html>
    """


@router.get("/projects/{project_id}/oncall", response_class=HTMLResponse)
def oncall_page(project_id: int = Path(..., ge=1)):
    return f"""
    <html>
    <head>
      <title>On-call</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body class="page-oncall">
    <div class="app-shell">
      <aside class="nav-rail">
        <div class="rail-brand">LP</div>
        <nav class="rail-links">
          <a class="rail-link" href="/ui/dashboard">Dashboard</a>
          <a class="rail-link" href="/ui/snapshots">Snapshots</a>
          <a class="rail-link" href="/ui/slo">SLO</a>
          <a class="rail-link" href="/ui/reports">Reports</a>
          <a class="rail-link" href="/ui/incidents">Incidents</a>
          <a class="rail-link" href="/ui/projects/{project_id}/settings">Settings</a>
          <a class="rail-link active" href="/ui/projects/{project_id}/oncall">On-call</a>
        </nav>
      </aside>

      <main class="main-stage">
        <header class="topbar">
          <div>
            <h1>On-call Management</h1>
            <div class="muted">Configure rotations, escalation policies, and per-check routing overrides.</div>
          </div>
        </header>

        <section id="incidentHeroBanner" class="card hero-banner hero-banner-hidden" role="status" aria-live="polite">
          <div class="hero-banner-icon">!</div>
          <div class="hero-banner-content">
            <div class="hero-banner-title" id="incidentHeroTitle">No active outages</div>
            <div class="hero-banner-sub" id="incidentHeroSub">All checks currently healthy.</div>
          </div>
        </section>

        <section class="card health-strip">
          <div class="health-item">
            <span class="health-label">Last refresh</span>
            <span class="health-value" id="healthLastRefresh">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Active incidents</span>
            <span class="health-value" id="healthActiveIncidents">-</span>
          </div>
          <div class="health-item">
            <span class="health-label">Workers online</span>
            <span class="health-value" id="healthWorkersOnline">-</span>
          </div>
          <div class="health-item health-item-wide">
            <span class="health-label">Region health</span>
            <span class="health-value" id="healthRegionHealth">-</span>
          </div>
        </section>

        <section class="card controls-card">
          <div class="row dashboard-controls-row">
            <div class="dashboard-inputs">
              <label>Project: <input id="projectId" value="{project_id}" style="width:80px"/></label>
              <label>API Key: <input id="apiKey" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
              <label>Admin token: <input id="adminToken" type="password" autocomplete="off" placeholder="optional" style="width:220px"/></label>
            </div>
            <div class="dashboard-actions">
              <button id="refreshBtn" class="btn">Refresh</button>
              <a class="btn btn-secondary" href="/ui/projects/{project_id}/settings">Settings</a>
              <a class="btn btn-secondary" href="/ui/projects/{project_id}/remediation">Remediation</a>
            </div>
          </div>
        </section>

        <section id="oncallCards" class="kpi-grid"></section>

        <section class="card">
          <div class="section-head">
            <h3>Rotations</h3>
            <div class="muted">Create schedules for who gets paged first.</div>
          </div>
          <div class="row">
            <input id="rotName" placeholder="Rotation name" />
            <input id="rotInterval" placeholder="Interval (min)" style="width:140px" />
            <button id="addRotationBtn" class="btn">Add Rotation</button>
          </div>
          <div id="rotations"></div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Members</h3>
            <div class="muted">Assign people to rotations and order.</div>
          </div>
          <div class="row">
            <input id="memberRotationId" placeholder="Rotation ID" style="width:140px" />
            <input id="memberName" placeholder="Name" />
            <input id="memberEmail" placeholder="Email" style="width:200px" />
            <input id="memberPhone" placeholder="Phone" style="width:160px" />
            <input id="memberOrder" placeholder="Order" style="width:80px" />
            <button id="addMemberBtn" class="btn">Add Member</button>
          </div>
          <div id="members"></div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Escalations</h3>
            <div class="muted">Define channel and timing at project or check level.</div>
          </div>
          <div class="row">
            <label>Check:
              <select id="escCheckSelect" style="width:200px">
                <option value="">(project-wide)</option>
              </select>
            </label>
            <input id="escCheckId" placeholder="Check ID (optional)" style="width:140px" />
            <input id="escLevel" placeholder="Level" style="width:80px" />
            <input id="escDelay" placeholder="Delay (min)" style="width:120px" />
            <select id="escType">
              <option value="rotation">rotation</option>
              <option value="email">email</option>
              <option value="sms">sms</option>
            </select>
            <input id="escRotationId" placeholder="Rotation ID" style="width:140px" />
            <input id="escTarget" placeholder="Target (email/phone)" style="width:220px" />
            <button id="addEscBtn" class="btn">Add Escalation</button>
          </div>
          <div class="muted">Tip: pick a check from the dropdown to create per-check escalation rules.</div>
          <div class="row">
            <label>Filter:
              <select id="escFilterCheckSelect" style="width:200px">
                <option value="">(all checks)</option>
              </select>
            </label>
            <input id="escFilterCheckId" placeholder="Filter by Check ID" style="width:160px" />
            <button id="escFilterBtn" class="btn btn-secondary">Filter</button>
            <button id="escClearFilterBtn" class="btn btn-secondary">Clear</button>
          </div>
          <div id="escalations"></div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Policy Builder</h3>
            <div class="muted">Build effective escalation chains with event filters.</div>
          </div>
          <div class="row">
            <label>Check:
              <select id="policyCheckSelect" style="width:220px">
                <option value="">(project-wide)</option>
              </select>
            </label>
            <label>Preview event:
              <select id="policyPreviewEvent" style="width:140px">
                <option value="">any</option>
                <option value="down">down</option>
                <option value="degraded">degraded</option>
              </select>
            </label>
            <button id="policyRefreshBtn" class="btn btn-secondary">Refresh Chain</button>
            <button id="policyPreviewBtn" class="btn btn-secondary">Preview</button>
            <button id="policyApplyTemplateBtn" class="btn btn-secondary">Apply Project Template</button>
            <button id="policySaveTemplateBtn" class="btn btn-secondary">Save as Project Template</button>
          </div>
          <div class="muted">Drag steps to reorder. Add multiple channels per step (rotation + email + SMS). Use event filters to target down vs degraded alerts.</div>
          <div id="policyChain" class="muted">Select a check to view the escalation chain.</div>
          <div id="policyPreview" class="card">Preview will show the effective chain used for the selected event.</div>
          <h3>Add Step</h3>
          <div class="row">
            <label>Event filter:
              <select id="policyEventTypes" style="width:160px">
                <option value="">any</option>
                <option value="down">down only</option>
                <option value="degraded">degraded only</option>
                <option value="down,degraded">down+degraded</option>
              </select>
            </label>
            <input id="policyDelay" placeholder="Delay (min)" style="width:120px" />
            <select id="policyType">
              <option value="rotation">rotation</option>
              <option value="email">email</option>
              <option value="sms">sms</option>
            </select>
            <input id="policyRotationId" placeholder="Rotation ID" style="width:140px" />
            <input id="policyTarget" placeholder="Target (email/phone)" style="width:220px" />
            <label><input type="checkbox" id="policyEnabled" checked /> Enabled</label>
            <button id="policyAddBtn" class="btn">Add Step</button>
          </div>
          <div class="muted">Use Add Step to append a new escalation level for the selected scope.</div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Per-check Routing &amp; Channel Overrides</h3>
            <div class="muted">Edit channel enablement and destinations for the selected check.</div>
          </div>
          <div class="muted">
            Select a check in <strong>Policy Builder</strong> above to edit overrides.
            Use <em>inherit</em> (blank) to fall back to project defaults. Blank destination fields clear overrides.
            Saving requires an admin token (or an owner user token).
          </div>
          <div class="row">
            <button id="routingSaveBtn" class="btn">Save Overrides</button>
            <button id="routingResetBtn" class="btn btn-secondary">Reset (inherit)</button>
            <div id="routingScope" class="muted"></div>
          </div>
          <div class="card">
            <div class="row">
              <label>On-call:
                <select id="routingOncallEnabled" style="width:160px">
                  <option value="">inherit</option>
                  <option value="true">enabled</option>
                  <option value="false">disabled</option>
                </select>
              </label>
              <label>On-call Email:
                <input id="routingOncallEmail" placeholder="oncall@example.com" style="width:260px"/>
              </label>
              <label>SMS:
                <select id="routingSmsEnabled" style="width:160px">
                  <option value="">inherit</option>
                  <option value="true">enabled</option>
                  <option value="false">disabled</option>
                </select>
              </label>
              <label>SMS To:
                <input id="routingSmsTo" placeholder="+15551234567" style="width:180px"/>
              </label>
            </div>
            <div class="row">
              <label>Slack:
                <select id="routingSlackEnabled" style="width:160px">
                  <option value="">inherit</option>
                  <option value="true">enabled</option>
                  <option value="false">disabled</option>
                </select>
              </label>
              <label>Slack Webhook:
                <input id="routingSlackWebhook" type="url" placeholder="https://hooks.slack.com/..." style="width:360px"/>
              </label>
            </div>
            <div class="row">
              <label>Discord:
                <select id="routingDiscordEnabled" style="width:160px">
                  <option value="">inherit</option>
                  <option value="true">enabled</option>
                  <option value="false">disabled</option>
                </select>
              </label>
              <label>Discord Webhook:
                <input id="routingDiscordWebhook" type="url" placeholder="https://discord.com/api/webhooks/..." style="width:360px"/>
              </label>
            </div>
            <div class="row">
              <label>PagerDuty:
                <select id="routingPagerdutyEnabled" style="width:160px">
                  <option value="">inherit</option>
                  <option value="true">enabled</option>
                  <option value="false">disabled</option>
                </select>
              </label>
              <label>Integration Key:
                <input id="routingPagerdutyKey" placeholder="(optional)" style="width:260px"/>
              </label>
            </div>
            <div class="row">
              <label>Generic Webhook:
                <select id="routingWebhookEnabled" style="width:160px">
                  <option value="">inherit</option>
                  <option value="true">enabled</option>
                  <option value="false">disabled</option>
                </select>
              </label>
              <label>Webhook URL:
                <input id="routingGenericWebhook" type="url" placeholder="https://example.com/webhook" style="width:360px"/>
              </label>
            </div>
            <div class="row">
              <label>Escalate after (min):
                <input id="routingEscAfter" placeholder="(disabled)" style="width:160px"/>
              </label>
              <label>Escalation cooldown (s):
                <input id="routingEscCooldown" placeholder="3600" style="width:160px"/>
              </label>
            </div>
            <div class="muted">
              Validation hints: SMS To should look like <code>+15551234567</code>. Webhook URLs must be <code>http(s)://</code>.
            </div>
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Open Alerts</h3>
            <div class="muted">Active alerts currently awaiting acknowledgment/resolution.</div>
          </div>
          <div id="alerts"></div>
        </section>
      </main>
    </div>
    <script src="/static/js/ui_shell.js"></script>
    <script src="/static/js/oncall.js"></script>
    </body>
    </html>
    """


@router.get("/projects/{project_id}/remediation", response_class=HTMLResponse)
def remediation_page(project_id: int = Path(..., ge=1)):
    return f"""
    <html>
    <head>
      <title>Remediation Hooks</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body>
    <h1>Remediation Hooks</h1>
    <div class="row">
      <label>Project: <input id="projectId" value="{project_id}" style="width:80px"/></label>
      <label>API Key: <input id="apiKey" type="password" autocomplete="off" placeholder="optional" style="width:240px"/></label>
      <label>Admin token: <input id="adminToken" type="password" autocomplete="off" placeholder="optional" style="width:240px"/></label>
      <button id="refreshBtn" class="btn">Refresh</button>
    </div>
    <h2>Create Hook</h2>
    <div class="row">
      <input id="hookCheckId" placeholder="Check ID (optional)" style="width:140px" />
      <select id="hookEvent">
        <option value="down">down</option>
        <option value="degraded">degraded</option>
      </select>
      <input id="hookUrl" placeholder="URL" style="width:320px" />
      <input id="hookMethod" placeholder="Method" style="width:100px" />
      <input id="hookCooldown" placeholder="Cooldown (s)" style="width:140px" />
      <input id="hookSecret" placeholder="Secret" style="width:160px" />
      <label><input type="checkbox" id="hookEnabled" checked /> Enabled</label>
      <label><input type="checkbox" id="hookRequireApproval" /> Require approval</label>
      <button id="addHookBtn" class="btn">Add Hook</button>
    </div>
    <h2>Hooks</h2>
    <div id="hooks"></div>
    <h2>Approvals</h2>
    <div class="row">
      <select id="approvalStatus">
        <option value="">(all)</option>
        <option value="pending">pending</option>
        <option value="approved">approved</option>
        <option value="denied">denied</option>
        <option value="executed">executed</option>
        <option value="failed">failed</option>
        <option value="expired">expired</option>
      </select>
      <button id="refreshApprovalsBtn" class="btn btn-secondary">Refresh Approvals</button>
    </div>
    <div id="approvals"></div>
    <h2>Logs</h2>
    <div id="logs"></div>
    <script src="/static/js/remediation.js"></script>
    </body>
    </html>
    """


@router.get("/account", response_class=HTMLResponse)
def account_page():
    return """
    <html>
    <head>
      <title>Enterprise Access</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body>
      <main style="max-width:1120px;margin:0 auto;">
        <header style="margin-bottom:18px;">
          <div class="muted" style="text-transform:uppercase;letter-spacing:.12em;">Identity</div>
          <h1>Enterprise Access</h1>
          <div class="muted">Password login, SSO entry points, org-scoped roles, MFA enrollment, and session management in one place.</div>
          <div style="margin-top:12px;"><a class="btn btn-secondary" href="/ui/tenant">Open Tenant Console</a></div>
        </header>

        <section class="card" style="margin-bottom:16px;">
          <div class="section-head">
            <h3>Sign In</h3>
            <div id="accountStatus" class="muted">Use password or SSO to mint a bearer session for the UI.</div>
          </div>
          <div class="row">
            <label>Email:
              <input id="authEmail" type="email" autocomplete="username" placeholder="ops@example.com" style="width:240px"/>
            </label>
            <label>Password:
              <input id="authPassword" type="password" autocomplete="current-password" placeholder="password" style="width:220px"/>
            </label>
            <button id="loginBtn" class="btn">Login</button>
            <button id="logoutBtn" class="btn btn-secondary">Clear Session</button>
          </div>
          <div id="ssoProviders" class="row"></div>
        </section>

        <section id="mfaCard" class="card" style="display:none;">
          <div class="section-head">
            <h3>MFA</h3>
            <div class="muted">Admin-grade local login uses TOTP challenges and enforced enrollment.</div>
          </div>
          <div id="mfaStatus" class="muted" style="margin-bottom:10px;">No pending MFA flow.</div>
          <div id="mfaEnrollBlock" style="display:none;">
            <div class="row">
              <label>Secret:
                <input id="mfaSecret" readonly style="width:240px"/>
              </label>
              <label>OTPAuth URI:
                <input id="mfaUri" readonly style="width:420px"/>
              </label>
            </div>
          </div>
          <div class="row">
            <label>Code:
              <input id="mfaCode" inputmode="numeric" autocomplete="one-time-code" placeholder="123456" style="width:140px"/>
            </label>
            <button id="verifyMfaBtn" class="btn">Complete MFA Step</button>
            <button id="startMfaEnrollBtn" class="btn btn-secondary">Start Enrollment</button>
          </div>
          <div class="row">
            <label>Disable with code:
              <input id="disableMfaCode" inputmode="numeric" autocomplete="one-time-code" placeholder="123456" style="width:140px"/>
            </label>
            <button id="disableMfaBtn" class="btn btn-secondary">Disable MFA</button>
          </div>
        </section>

        <section class="card">
          <div class="section-head">
            <h3>Profile</h3>
            <div class="muted">Org-scoped roles, linked identities, and active sessions.</div>
          </div>
          <div id="accountSummary" class="muted">No active session.</div>
          <div class="row" style="align-items:flex-start;">
            <div class="card" style="flex:1; min-width:280px; margin-bottom:0;">
              <h3>Organization Roles</h3>
              <div id="orgRoles" class="muted">No org memberships loaded.</div>
            </div>
            <div class="card" style="flex:1; min-width:280px; margin-bottom:0;">
              <h3>Linked Identities</h3>
              <div id="linkedIdentities" class="muted">No linked SSO identities loaded.</div>
            </div>
          </div>
          <div class="row" style="justify-content:flex-end; margin-top:12px;">
            <button id="revokeOthersBtn" class="btn btn-secondary">Revoke Other Sessions</button>
          </div>
          <div id="sessionsWrap" style="margin-top:8px;">
            <table>
              <thead>
                <tr>
                  <th>Session</th>
                  <th>Auth</th>
                  <th>Issued</th>
                  <th>Seen</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody id="sessionRows">
                <tr><td colspan="6" class="muted">No sessions loaded.</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </main>
      <script src="/static/js/account.js"></script>
    </body>
    </html>
    """


@router.get("/tenant", response_class=HTMLResponse)
def tenant_console_page():
    return """
    <html>
    <head>
      <title>Tenant Console</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body>
      <main style="max-width:1240px;margin:0 auto;">
        <header style="margin-bottom:18px;">
          <div class="muted" style="text-transform:uppercase;letter-spacing:.12em;">Tenant Ops</div>
          <h1>Tenant Console</h1>
          <div class="muted">Organization switcher, team ownership of projects, inline membership editing, scoped service accounts, token inventory, and membership audit history.</div>
          <div style="margin-top:12px;" class="row">
            <a class="btn btn-secondary" href="/ui/account">Enterprise Access</a>
          </div>
        </header>

        <section class="card" style="margin-bottom:16px;">
          <div class="section-head">
            <h3>Organization Switcher</h3>
            <div id="tenantStatus" class="muted">Use an existing bearer session from Enterprise Access to load tenant management.</div>
          </div>
          <div class="row">
            <label>Organization:
              <select id="tenantOrgSelect" style="width:320px"></select>
            </label>
            <button id="tenantRefreshBtn" class="btn">Refresh</button>
          </div>
          <div id="tenantSummary" class="row"></div>
        </section>

        <section class="card" style="margin-bottom:16px;">
          <div class="section-head">
            <h3>Projects</h3>
            <div class="muted">Set a primary owner team and review service-account coverage per project.</div>
          </div>
          <div id="tenantProjectsWrap">
            <table>
              <thead>
                <tr>
                  <th>Project</th>
                  <th>Owner Team</th>
                  <th>Accessible Teams</th>
                  <th>Service Accounts</th>
                  <th>Active Tokens</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody id="tenantProjectsRows">
                <tr><td colspan="6" class="muted">No org selected.</td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="card" style="margin-bottom:16px;">
          <div class="section-head">
            <h3>Scoped Service Accounts</h3>
            <div class="muted">Mint project-scoped automation credentials with optional team ownership metadata.</div>
          </div>
          <div class="row">
            <label>Project:
              <select id="serviceAccountProject" style="width:220px"></select>
            </label>
            <label>Team:
              <select id="serviceAccountTeam" style="width:220px"></select>
            </label>
            <label>Name:
              <input id="serviceAccountName" placeholder="deploy-bot" style="width:180px"/>
            </label>
            <label>Role:
              <select id="serviceAccountRole" style="width:140px">
                <option value="viewer">viewer</option>
                <option value="editor" selected>editor</option>
                <option value="admin">admin</option>
                <option value="owner">owner</option>
              </select>
            </label>
          </div>
          <div class="row">
            <label>Description:
              <input id="serviceAccountDescription" placeholder="deploy pipeline credential" style="width:320px"/>
            </label>
            <label>Expires At:
              <input id="serviceAccountExpiresAt" placeholder="optional ISO8601" style="width:240px"/>
            </label>
            <label>Rotate Every (days):
              <input id="serviceAccountRotationDays" placeholder="optional" style="width:160px"/>
            </label>
            <button id="createServiceAccountBtn" class="btn">Create Service Account</button>
          </div>
          <div id="serviceAccountStatus" class="muted" style="margin-top:8px;">No service account minted in this session.</div>
          <div class="row" style="margin-top:10px;">
            <label>Plaintext API Key:
              <input id="serviceAccountPlaintext" readonly style="width:420px"/>
            </label>
          </div>
        </section>

        <section class="card" style="margin-bottom:16px;">
          <div class="section-head">
            <h3>Token Inventory</h3>
            <div class="muted">Expiry, last-used, and rotation posture for project tokens and service accounts.</div>
          </div>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Project</th>
                <th>Team</th>
                <th>Last Used</th>
                <th>Expires</th>
                <th>Status</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody id="tenantTokenRows">
              <tr><td colspan="8" class="muted">No org selected.</td></tr>
            </tbody>
          </table>
        </section>

        <div class="row" style="align-items:flex-start;">
          <section class="card" style="flex:1;min-width:320px;margin-bottom:16px;">
            <div class="section-head">
              <h3>Organization Members</h3>
              <div class="muted">Invite members, change org roles, and remove access without leaving the tenant console.</div>
            </div>
            <div class="row">
              <label>Email:
                <input id="orgMemberEmail" placeholder="new-member@example.com" style="width:240px"/>
              </label>
              <label>Role:
                <select id="orgMemberRole" style="width:140px">
                  <option value="member" selected>member</option>
                  <option value="admin">admin</option>
                  <option value="owner">owner</option>
                </select>
              </label>
              <button id="orgMemberAddBtn" class="btn">Add / Update</button>
            </div>
            <div id="orgMemberStatus" class="muted" style="margin-top:8px;">No org membership changes in this session.</div>
            <table style="margin-top:12px;">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody id="orgMemberRows">
                <tr><td colspan="3" class="muted">No org selected.</td></tr>
              </tbody>
            </table>
          </section>

          <section class="card" style="flex:1;min-width:320px;margin-bottom:16px;">
            <div class="section-head">
              <h3>Team Memberships</h3>
              <div class="muted">Manage team-level access inline, including role changes for leads and members.</div>
            </div>
            <div class="row">
              <label>Team:
                <select id="teamMemberTeamSelect" style="width:220px"></select>
              </label>
              <label>Email:
                <input id="teamMemberEmail" placeholder="teammate@example.com" style="width:240px"/>
              </label>
              <label>Role:
                <select id="teamMemberRole" style="width:140px">
                  <option value="member" selected>member</option>
                  <option value="lead">lead</option>
                </select>
              </label>
              <button id="teamMemberAddBtn" class="btn">Add / Update</button>
            </div>
            <div id="teamMemberStatus" class="muted" style="margin-top:8px;">Select a team to edit its membership.</div>
            <table style="margin-top:12px;">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Role</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody id="teamMemberRows">
                <tr><td colspan="3" class="muted">No team selected.</td></tr>
              </tbody>
            </table>
          </section>
        </div>

        <section class="card">
          <div class="section-head">
            <h3>Membership Audit History</h3>
            <div class="muted">Team access, owner-team changes, service account lifecycle, and org membership-related audit events.</div>
          </div>
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Scope</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody id="tenantAuditRows">
              <tr><td colspan="5" class="muted">No org selected.</td></tr>
            </tbody>
          </table>
        </section>
      </main>
      <script src="/static/js/tenant.js"></script>
    </body>
    </html>
    """


@router.get("/status/{project_id}", response_class=HTMLResponse)
def public_status_page(project_id: int = Path(..., ge=1), _scope = Depends(limit_public_status_requests)):
    return f"""
    <html>
    <head>
      <title>System Status</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body class="page-status-public">
    <main class="public-status-shell">
      <header class="public-status-header">
        <div class="public-status-brand">LP</div>
        <div>
          <div class="public-status-kicker">Public status</div>
          <h1>System Status</h1>
          <div class="muted">Live component health, incident history, and subscription updates.</div>
        </div>
      </header>
      <div id="statusRoot" class="status-public-root" data-project-id="{project_id}"></div>
    </main>
    <script src="/static/js/status.js"></script>
    </body>
    </html>
    """


@router.get("/status/{project_id}/data")
def public_status_data(project_id: int = Path(..., ge=1), _scope = Depends(limit_public_status_requests), session: Session = Depends(get_session)):
    return _build_public_status_payload(session, project_id)


@router.post("/status/{project_id}/subscribe")
def public_status_subscribe(
    project_id: int = Path(..., ge=1),
    payload: StatusSubscriptionCreate = Body(...),
    _scope = Depends(limit_public_status_requests),
    session: Session = Depends(get_session),
):
    if payload.channel == "webhook":
        raise HTTPException(
            status_code=403,
            detail="Webhook subscriptions are temporarily disabled pending verification hardening",
        )
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    existing = session.exec(
        select(StatusSubscription).where(
            StatusSubscription.project_id == project_id,
            StatusSubscription.channel == payload.channel,
            StatusSubscription.target == payload.target,
        )
    ).first()

    if existing is None:
        subscription = StatusSubscription(
            project_id=project_id,
            channel=payload.channel,
            target=payload.target,
            active=True,
        )
        session.add(subscription)
        message = "Subscription created."
    else:
        existing.active = True
        subscription = existing
        message = "Subscription already existed; it has been reactivated."

    try:
        session.commit()
    except ValidationError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.refresh(subscription)

    return {
        "message": message,
        "subscription": {
            "id": subscription.id,
            "project_id": subscription.project_id,
            "channel": subscription.channel,
            "target": subscription.target,
            "active": subscription.active,
            "created_at": subscription.created_at.isoformat(),
        },
    }

