from fastapi import APIRouter

router = APIRouter(prefix="/ui", tags=["ui"])


@router.get("/incidents")
def incidents_page():
    return """
    <html>
    <head>
      <title>Incidents</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <style>
        body{font-family:system-ui,Segoe UI,Roboto,Arial;margin:20px;color:#222}
        .row{display:flex;gap:12px;align-items:center;margin-bottom:12px}
        input,button{padding:8px;border:1px solid #ddd;border-radius:6px}
        .card{border:1px solid #eee;padding:12px;border-radius:8px;margin-bottom:8px;background:#fafafa}
        .muted{color:#666;font-size:0.9em}
        .btn{background:#0366d6;color:#fff;padding:6px 10px;border-radius:6px;text-decoration:none;border:none}
      </style>
    </head>
    <body>
    <h1>Incidents</h1>
    <div class="row">
      <label>Project: <input id="projectId" value="1" style="width:80px"/></label>
      <button class="btn" onclick="load()">Load</button>
      <a href="/ui/snapshots" style="margin-left:12px">Snapshots</a>
    </div>
    <div id="list">Loading...</div>
    <script>
    async function createShare(pid, iid){
      const resp = await fetch(`/projects/${pid}/incidents/${iid}/share`, {method:'POST'});
      if(!resp.ok){ alert('Failed to create share'); return }
      const j = await resp.json();
      alert('Share token: ' + j.share_token + '\nPublic URL: ' + location.origin + '/incidents/public/' + j.share_token);
    }

    async function load(){
      const pid = document.getElementById('projectId').value || '1';
      const resp = await fetch(`/projects/${pid}/incidents`);
      const el = document.getElementById('list');
      if(!resp.ok){ el.innerText = 'Failed to load incidents'; return }
      const json = await resp.json();
      if(!json.length){ el.innerHTML = '<div class="muted">No incidents</div>'; return }
      el.innerHTML = json.map(i => `
        <div class="card">
          <div><strong>Incident ${i.id}</strong> — check ${i.check_id} <span class="muted">(${i.status})</span></div>
          <div class="muted">Started: ${i.started_at} ${i.resolved_at? ' | Resolved: '+i.resolved_at : ''}</div>
          <div style="margin-top:8px">
            <a class="btn" href="/ui/incidents/${i.id}">Details</a>
            <button class="btn" onclick="createShare(${pid}, ${i.id})" style="margin-left:8px">Create Share Link</button>
          </div>
        </div>
      `).join('');
    }
    load();
    </script>
    </body>
    </html>
    """


@router.get("/incidents/{incident_id}")
def incident_detail_page(incident_id: int):
    return """
    <html>
    <head>
      <title>Incident</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <style>
        body{font-family:system-ui,Segoe UI,Roboto,Arial;margin:20px;color:#222}
        .muted{color:#666}
        .card{border:1px solid #eee;padding:8px;border-radius:6px;margin-bottom:8px;background:#fff}
        .btn{background:#0366d6;color:#fff;padding:6px 10px;border-radius:6px;text-decoration:none;border:none}
      </style>
    </head>
    <body>
    <h1>Incident <span id="iid"></span></h1>
    <div class="muted">Project: <input id="projectId" value="1" style="width:80px"/></div>
    <div style="margin-top:12px"><button id="shareBtn" class="btn">Create Share Link</button> <span id="shareInfo" class="muted"></span></div>
    <h2>Events</h2>
    <div id="detail">Loading...</div>
    <script>
    async function load(){
      const parts = location.pathname.split('/');
      const iid = parts[parts.length-1];
      document.getElementById('iid').innerText = iid;
      const pid = document.getElementById('projectId').value || '1';
      const resp = await fetch(`/projects/${pid}/incidents/${iid}`);
      const el = document.getElementById('detail');
      if(!resp.ok){ el.innerText = 'Failed to load'; return }
      const json = await resp.json();
      const events = json.events.map(e => `<div class="card"><div><strong>${e.type}</strong> <span class="muted">${e.ts}</span></div><div>${e.message||''}</div></div>`).join('');
      el.innerHTML = `<div><strong>Incident</strong> ${json.incident.id} (check ${json.incident.check_id})</div><div class="muted">started ${json.incident.started_at} ${json.incident.resolved_at? ' | resolved '+json.incident.resolved_at : ''}</div>${events}`;
      if(json.incident.share_token) document.getElementById('shareInfo').innerText = 'Public: ' + location.origin + '/incidents/public/' + json.incident.share_token;
    }
    document.getElementById('shareBtn').addEventListener('click', async ()=>{
      const parts = location.pathname.split('/');
      const iid = parts[parts.length-1];
      const pid = document.getElementById('projectId').value || '1';
      const resp = await fetch(`/projects/${pid}/incidents/${iid}/share`, {method:'POST'});
      const j = await resp.json();
      document.getElementById('shareInfo').innerText = 'Public: ' + location.origin + '/incidents/public/' + j.share_token;
    });
    load();
    </script>
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
      <style>body{font-family:system-ui,Segoe UI,Roboto,Arial;margin:20px;color:#222} table{width:100%;border-collapse:collapse} th,td{padding:8px;border:1px solid #eee}</style>
    </head>
    <body>
    <h1>Snapshots (last 24h)</h1>
    <div class="row"><label>Project: <input id="projectId" value="1" style="width:80px"/></label> <button onclick="load()" class="btn">Load</button></div>
    <div id="list">Loading...</div>
    <script>
    async function load(){
      const pid = document.getElementById('projectId').value || '1';
      const resp = await fetch(`/projects/${pid}/metrics/uptime`);
      const el = document.getElementById('list');
      if(!resp.ok){ el.innerText = 'Failed to load'; return }
      const json = await resp.json();
      el.innerHTML = '<pre>' + JSON.stringify(json, null, 2) + '</pre>';
    }
    load();
    </script>
    </body>
    </html>
    """
