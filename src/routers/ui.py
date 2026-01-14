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


@router.get('/checks')
def checks_page():
    return """
    <html>
    <head>
      <title>Checks</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <style>body{font-family:system-ui,Segoe UI,Roboto,Arial;margin:20px;color:#222} .row{display:flex;gap:8px;align-items:center} input,button{padding:8px;border:1px solid #ddd;border-radius:6px} .card{border:1px solid #eee;padding:12px;border-radius:8px;margin-bottom:8px;background:#fafafa} .muted{color:#666}</style>
    </head>
    <body>
    <h1>Checks</h1>
    <div class="row"><label>Project: <input id="projectId" value="1" style="width:80px"/></label>
      <label>Admin token: <input id="adminToken" placeholder="optional" style="width:240px"/></label>
      <label>User token: <input id="userToken" placeholder="optional" style="width:240px"/></label>
      <button onclick="load()">Load</button>
    </div>
    <h2>Create Check</h2>
    <div>
      <input id="name" placeholder="Name" />
      <select id="type"><option value="heartbeat">heartbeat</option><option value="http">http</option></select>
      <input id="url" placeholder="URL (for http)" style="width:320px" />
      <button onclick="create()">Create</button>
    </div>
    <h2>Existing Checks</h2>
    <div id="list">Loading...</div>
    <script>
    function headers(){
      const at = document.getElementById('adminToken').value;
      const ut = document.getElementById('userToken').value;
      const h = {'Content-Type':'application/json'};
      if(at) h['X-ADMIN-TOKEN'] = at;
      if(ut) h['Authorization'] = 'Bearer ' + ut;
      return h;
    }
    async function load(){
      const pid = document.getElementById('projectId').value || '1';
      // check role first
      let isOwner = false;
      try{
        const r = await fetch(`/users/projects/${pid}/role`, {headers: headers()});
        if(r.ok){ const jr = await r.json(); isOwner = jr.role === 'owner'; }
      }catch(e){ /* ignore */ }

      const resp = await fetch(`/projects/${pid}/checks`, {headers: headers()});
      const el = document.getElementById('list');
      if(!resp.ok){ el.innerText='Failed to load'; return }
      const json = await resp.json();
      const adminPresent = !!document.getElementById('adminToken').value;
      el.innerHTML = json.map(c=>`<div class="card"><div><strong>${c.name}</strong> (${c.type}) <span class="muted">status:${c.status}</span></div><div style="margin-top:8px"> <a href="/ui/checks/${c.id}">Manage</a>${(isOwner||adminPresent)? ' <button onclick="del('+pid+','+c.id+')" style="margin-left:8px">Delete</button>':''}</div></div>`).join('');
    }
    async function create(){
      const pid = document.getElementById('projectId').value || '1';
      const body = {name: document.getElementById('name').value, type: document.getElementById('type').value, url: document.getElementById('url').value};
      const resp = await fetch(`/projects/${pid}/checks`, {method:'POST', headers: headers(), body: JSON.stringify(body)});
      if(resp.status==201){ alert('Created'); load(); } else { alert('Create failed'); }
    }
    async function del(pid,id){
      if(!confirm('Delete check '+id+'?')) return;
      const resp = await fetch(`/projects/${pid}/checks/${id}`, {method:'DELETE', headers: headers()});
      if(resp.ok){ alert('Deleted'); load(); } else { alert('Delete failed'); }
    }
    load();
    </script>
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
      <style>body{font-family:system-ui,Segoe UI,Roboto,Arial;margin:20px;color:#222} .muted{color:#666} input,button{padding:8px;border:1px solid #ddd;border-radius:6px}</style>
    </head>
    <body>
    <h1>Manage Check __CHECK_ID__</h1>
    <div class="muted">Project: <input id="projectId" value="1" style="width:80px"/></div>
    <div class="muted">Admin token: <input id="adminToken" placeholder="optional" style="width:240px"/></div>
    <h2>Update</h2>
    <div>
      <input id="name" placeholder="Name"/>
      <input id="url" placeholder="URL" style="width:320px"/>
      <button onclick="update()">Save</button>
      <button id="delBtn" onclick="delCheck()" style="margin-left:8px;display:none">Delete</button>
    </div>
    <h2>Maintenance</h2>
    <div>
      <label>Start: <input id="mstart" placeholder="2026-01-14T12:00:00"/></label>
      <label>End: <input id="mend" placeholder="2026-01-14T13:00:00"/></label>
      <button onclick="setM()">Set Maintenance</button>
    </div>
    <div id="status">Loading...</div>
    <script>
    const CHECK_ID = __CHECK_ID__;
    function headers(){ const at=document.getElementById('adminToken').value; const ut=document.getElementById('userToken')?document.getElementById('userToken').value:null; const h={'Content-Type':'application/json'}; if(at) h['X-ADMIN-TOKEN']=at; if(ut) h['Authorization']='Bearer '+ut; return h; }
    async function load(){ const pid=document.getElementById('projectId').value||'1'; let isOwner=false; try{ const r=await fetch(`/users/projects/${pid}/role`, {headers: headers()}); if(r.ok){ const jr=await r.json(); isOwner = jr.role === 'owner'; }}catch(e){}; const resp=await fetch(`/projects/${pid}/checks/${CHECK_ID}`, {headers: headers()}); if(!resp.ok){document.getElementById('status').innerText='Failed';return} const js=await resp.json(); document.getElementById('name').value=js.name; document.getElementById('url').value=js.url||''; if(isOwner || document.getElementById('adminToken').value) document.getElementById('delBtn').style.display='inline-block'; }
    async function update(){ const pid=document.getElementById('projectId').value||'1'; const body={name:document.getElementById('name').value, url:document.getElementById('url').value}; const resp=await fetch(`/projects/${pid}/checks/${CHECK_ID}`, {method:'PUT', headers: headers(), body: JSON.stringify(body)}); if(resp.ok){ alert('Saved'); } else { alert('Save failed'); }}
    async function delCheck(){ if(!confirm('Delete check '+CHECK_ID+'?')) return; const pid=document.getElementById('projectId').value||'1'; const resp = await fetch(`/projects/${pid}/checks/${CHECK_ID}`, {method:'DELETE', headers: headers()}); if(resp.ok){ alert('Deleted'); location.href = '/ui/checks'; } else { alert('Delete failed'); }}
    async function setM(){ const pid=document.getElementById('projectId').value||'1'; const body={maintenance_starts_at: document.getElementById('mstart').value || null, maintenance_ends_at: document.getElementById('mend').value || null}; const resp=await fetch(`/projects/${pid}/checks/${CHECK_ID}/maintenance`, {method:'POST', headers: headers(), body: JSON.stringify(body)}); if(resp.ok){ alert('Set'); } else { alert('Failed'); }}
    load();
    </script>
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
