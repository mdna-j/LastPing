// /ui/incidents/{id} client script
function apiHeaders(){
  const apiKey = document.getElementById('apiKey') ? document.getElementById('apiKey').value : '';
  const headers = {};
  if(apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
  return headers;
}

async function loadIncidentDetail(){
  const parts = location.pathname.split('/');
  const iid = parts[parts.length-1];
  const pid = document.getElementById('projectId').value || '1';
  const el = document.getElementById('detail');
  if(!document.getElementById('apiKey') || !document.getElementById('apiKey').value){
    el.innerText = 'Provide API key to load incident details.';
    return;
  }
  const resp = await fetch(`/projects/${pid}/incidents/${iid}`, {headers: apiHeaders()});
  if(!resp.ok){ el.innerText = 'Failed to load'; return }
  const json = await resp.json();
  const events = json.events.map(e => `<div class="card"><label><input type="checkbox" data-eid="${e.id}"/> <strong>${e.type}</strong> <span class="muted">${e.ts}</span></label><div>${e.message||''}</div></div>`).join('');
  el.innerHTML = `<div><strong>Incident</strong> ${json.incident.id} (check ${json.incident.check_id})</div><div class="muted">started ${json.incident.started_at} ${json.incident.resolved_at? ' | resolved '+json.incident.resolved_at : ''} ${json.incident.group_id? ' | group:'+json.incident.group_id : ''} ${json.incident.merged_into? ' | merged_into:'+json.incident.merged_into : ''}</div>${events}<div style="margin-top:8px"><button id="splitBtn" class="btn btn-secondary">Split Selected</button></div>`;
  if(json.incident.share_token) document.getElementById('shareInfo').innerText = 'Public: ' + location.origin + '/incidents/public/' + json.incident.share_token;
}

async function loadSimilarIncidents(){
  const el = document.getElementById('similarIncidents');
  if(!el){ return; }
  const parts = location.pathname.split('/');
  const iid = parts[parts.length-1];
  const pid = document.getElementById('projectId').value || '1';
  if(!document.getElementById('apiKey') || !document.getElementById('apiKey').value){
    el.innerText = 'Provide API key to load similar incidents.';
    return;
  }
  el.innerText = 'Loading...';
  try{
    const resp = await fetch(`/projects/${pid}/analytics/incident-similarity?incident_id=${iid}`, {headers: apiHeaders()});
    if(!resp.ok){ el.innerText = 'Failed to load similar incidents'; return }
    const json = await resp.json();
    const matches = json.matches || [];
    if(!matches.length){
      el.innerHTML = '<div class="muted">No similar incidents found.</div>';
      return;
    }
    const rows = matches.map(m => {
      return `<div class="card"><div><strong>Incident ${m.incident_id}</strong> (check ${m.check_id})</div><div class="muted">score ${m.score} • started ${m.started_at}</div><div style="margin-top:6px"><a class="btn btn-secondary" href="/ui/incidents/${m.incident_id}">Open</a></div></div>`;
    }).join('');
    el.innerHTML = rows;
  }catch(e){
    el.innerText = 'Failed to load similar incidents';
  }
}
function headersManage(){ const at=document.getElementById('adminToken')?document.getElementById('adminToken').value:''; const ut=document.getElementById('userToken')?document.getElementById('userToken').value:null; const h={'Content-Type':'application/json'}; if(at) h['X-ADMIN-TOKEN']=at; if(ut) h['Authorization']='Bearer '+ut; return h; }
document.addEventListener('DOMContentLoaded', ()=>{
  const btn = document.getElementById('shareBtn');
  if(btn) btn.addEventListener('click', async ()=>{
    const parts = location.pathname.split('/');
    const iid = parts[parts.length-1];
    const pid = document.getElementById('projectId').value || '1';
    const resp = await fetch(`/projects/${pid}/incidents/${iid}/share`, {method:'POST'});
    const j = await resp.json();
    document.getElementById('shareInfo').innerText = 'Public: ' + location.origin + '/incidents/public/' + j.share_token;
  });
  loadIncidentDetail();
  loadSimilarIncidents();
  const apiKeyEl = document.getElementById('apiKey');
  if(apiKeyEl){
    apiKeyEl.addEventListener('change', ()=>{
      loadIncidentDetail();
      loadSimilarIncidents();
    });
  }
  document.addEventListener('click', async (ev)=>{
    if(ev.target && ev.target.id === 'splitBtn'){
      const parts = location.pathname.split('/');
      const iid = parts[parts.length-1];
      const pid = document.getElementById('projectId').value || '1';
      const checks = Array.from(document.querySelectorAll('#detail input[type=checkbox]:checked')).map(i => parseInt(i.getAttribute('data-eid')));
      if(!checks.length){ alert('No events selected'); return }
      if(!confirm('Split selected events into a new incident?')) return;
      const btn = ev.target;
      btn.disabled = true;
      try{
        const resp = await fetch(`/projects/${pid}/incidents/${iid}/split`, {method:'POST', headers: headersManage(), body: JSON.stringify({event_ids: checks})});
        if(!resp.ok){ alert('Split failed'); return }
        const j = await resp.json();
        alert('Split into incident '+j.split_into);
        loadIncidentDetail();
        loadSimilarIncidents();
      }finally{ btn.disabled = false }
    }
  });
});
