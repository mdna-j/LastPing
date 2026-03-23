from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select
from pydantic import AnyHttpUrl, EmailStr, ValidationError, parse_obj_as, validator

from ..db import get_session
from ..models import Project, Check, Event, Incident, CheckLease, StatusSubscription
from ..deps import limit_public_requests
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


def _serialize_public_incident(incident: Incident, *, check_name: str, latest_event: Event | None, now: datetime) -> dict:
    ended_at = incident.resolved_at or now
    duration_seconds = max(0, int((ended_at - incident.started_at).total_seconds()))
    return {
        "id": incident.id,
        "check_id": incident.check_id,
        "check_name": check_name,
        "started_at": incident.started_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "status": incident.status,
        "duration_seconds": duration_seconds,
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
          <a class="rail-link active" href="/ui/incidents">Incidents</a>
          <a class="rail-link" href="/ui/projects/1/settings">Settings</a>
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
      <select id="type"><option value="heartbeat">heartbeat</option><option value="http">http</option><option value="tcp">tcp</option><option value="dns">dns</option></select>
      <input id="url" placeholder="URL (for http)" style="width:320px" />
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
          <a class="rail-link active" href="/ui/incidents">Incidents</a>
          <a class="rail-link" href="/ui/projects/1/settings">Settings</a>
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
              <button id="shareBtn" class="btn btn-secondary">Create Share Link</button>
              <a class="btn btn-secondary" href="/ui/incidents">Back To Incidents</a>
            </div>
          </div>
          <div id="shareInfo" class="muted"></div>
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
          <a class="rail-link" href="/ui/incidents">Incidents</a>
          <a class="rail-link" id="settingsLink" href="/ui/projects/1/settings">Settings</a>
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
          <a class="rail-link" href="/ui/incidents">Incidents</a>
          <a class="rail-link" href="/ui/projects/1/settings">Settings</a>
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
          <a class="rail-link active" href="/ui/reports">Reports</a>
          <a class="rail-link" href="/ui/incidents">Incidents</a>
          <a class="rail-link" href="/ui/projects/1/settings">Settings</a>
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


@router.get("/status/{project_id}", response_class=HTMLResponse)
def public_status_page(project_id: int = Path(..., ge=1)):
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
def public_status_data(project_id: int = Path(..., ge=1), session: Session = Depends(get_session)):
    return _build_public_status_payload(session, project_id)


@router.post("/status/{project_id}/subscribe")
def public_status_subscribe(
    project_id: int = Path(..., ge=1),
    payload: StatusSubscriptionCreate = Body(...),
    session: Session = Depends(get_session),
):
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

