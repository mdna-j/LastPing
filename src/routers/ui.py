from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_session
from ..models import Project, Check, Event, Incident

router = APIRouter(prefix="/ui", tags=["ui"])


@router.get("/incidents")
def incidents_page():
    return """
    <html>
    <head>
      <title>Incidents</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body>
    <h1>Incidents</h1>
      <div class="row">
      <label>Project: <input id="projectId" value="1" style="width:80px"/></label>
      <button id="loadIncidentsBtn" class="btn">Load</button>
      <a href="/ui/snapshots" style="margin-left:12px">Snapshots</a>
    </div>
    <div id="list">Loading...</div>
    <script src="/static/js/incidents.js"></script>
    </body>
    </html>
    """


@router.get('/checks')
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
      <label>Admin token: <input id="adminToken" placeholder="optional" style="width:240px"/></label>
      <label>User token: <input id="userToken" placeholder="optional" style="width:240px"/></label>
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
    <h2>Existing Checks</h2>
    <div id="list">Loading...</div>
    <script src="/static/js/checks.js"></script>
    </body>
    </html>
    """


@router.get('/checks/{check_id}')
def checks_manage_page(check_id: int):
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
    <div class="muted">Admin token: <input id="adminToken" placeholder="optional" style="width:240px"/></div>
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


@router.get("/incidents/{incident_id}")
def incident_detail_page(incident_id: int):
    return """
    <html>
    <head>
      <title>Incident</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body>
    <h1>Incident <span id="iid"></span></h1>
    <div class="muted">Project: <input id="projectId" value="1" style="width:80px"/></div>
    <div style="margin-top:12px"><button id="shareBtn" class="btn">Create Share Link</button> <span id="shareInfo" class="muted"></span></div>
    <h2>Events</h2>
    <div id="detail">Loading...</div>
    <script src="/static/js/incident_detail.js"></script>
    </body>
    </html>
    """


@router.get('/snapshots')
def snapshots_page():
    return """
    <html>
    <head>
      <title>Snapshots</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body>
    <h1>Snapshots (last 24h)</h1>
    <div class="row">
      <label>Project: <input id="projectId" value="1" style="width:80px"/></label>
      <label>API Key: <input id="apiKey" placeholder="optional" style="width:240px"/></label>
      <label><input type="checkbox" id="rememberApiKey" /> Remember API Key</label>
      <button id="showApiKeyBtn" class="btn">Show</button>
      <label>Check: <select id="checkId" style="width:120px"><option value="">(all)</option></select></label>
      <label>Start: <input id="start" placeholder="YYYY-MM-DDTHH:MM:SS" style="width:200px"/></label>
      <label>End: <input id="end" placeholder="YYYY-MM-DDTHH:MM:SS" style="width:200px"/></label>
      <button id="loadSnapshotsBtn" class="btn">Load</button>
      <button id="savePrefsBtn" class="btn">Save Prefs</button>
      <button id="exportCsvBtn" class="btn">Export CSV</button>
    </div>
    <div id="list">Loading...</div>
    <div style="margin-top:12px"><canvas id="uptimeChart" width="800" height="240"></canvas></div>
    <script src="/static/js/vendor/chart.min.js"></script>
    <script src="/static/js/snapshots.js"></script>
    </body>
    </html>
    """


@router.get("/status/{project_id}")
def public_status_page(project_id: int):
    return f"""
    <html>
    <head>
      <title>Project Status</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body>
    <h1>Project Status</h1>
    <div id="statusRoot" data-project-id="{project_id}"></div>
    <script src="/static/js/status.js"></script>
    </body>
    </html>
    """


@router.get("/status/{project_id}/data")
def public_status_data(project_id: int, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    checks = session.exec(select(Check).where(Check.project_id == project_id)).all()
    open_incidents = session.exec(select(Incident).where(Incident.project_id == project_id, Incident.resolved_at == None)).all()

    out_checks = []
    for c in checks:
        last_event = session.exec(
            select(Event).where(Event.project_id == project_id, Event.check_id == c.id).order_by(Event.created_at.desc())
        ).first()
        out_checks.append({
            "id": c.id,
            "name": c.name,
            "type": c.type,
            "status": c.status,
            "last_ping": c.last_ping.isoformat() if c.last_ping else None,
            "last_event": {
                "type": last_event.event_type,
                "message": last_event.message,
                "created_at": last_event.created_at.isoformat(),
            } if last_event else None,
        })

    incidents_out = []
    for inc in open_incidents:
        incidents_out.append({
            "id": inc.id,
            "check_id": inc.check_id,
            "started_at": inc.started_at.isoformat(),
            "status": inc.status,
        })

    return {
        "project": {"id": project.id, "name": project.name},
        "checks": out_checks,
        "open_incidents": incidents_out,
    }
