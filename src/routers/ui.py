from fastapi import APIRouter

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
      <select id="type"><option value="heartbeat">heartbeat</option><option value="http">http</option></select>
      <input id="url" placeholder="URL (for http)" style="width:320px" />
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
    <div class="row"><label>Project: <input id="projectId" value="1" style="width:80px"/></label> <button id="loadSnapshotsBtn" class="btn">Load</button></div>
    <div id="list">Loading...</div>
    <script src="/static/js/snapshots.js"></script>
    </body>
    </html>
    """
