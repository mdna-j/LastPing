// /ui/incidents/{id} client script
async function loadIncidentDetail(){
  const parts = location.pathname.split('/');
  const iid = parts[parts.length-1];
  const pid = document.getElementById('projectId').value || '1';
  const resp = await fetch(`/projects/${pid}/incidents/${iid}`);
  const el = document.getElementById('detail');
  if(!resp.ok){ el.innerText = 'Failed to load'; return }
  const json = await resp.json();
  const events = json.events.map(e => `<div class="card"><div><strong>${e.type}</strong> <span class="muted">${e.ts}</span></div><div>${e.message||''}</div></div>`).join('');
  el.innerHTML = `<div><strong>Incident</strong> ${json.incident.id} (check ${json.incident.check_id})</div><div class="muted">started ${json.incident.started_at} ${json.incident.resolved_at? ' | resolved '+json.incident.resolved_at : ''}</div>${events}`;
  if(json.incident.share_token) document.getElementById('shareInfo').innerText = 'Public: ' + location.origin + '/incidents/public/' + json.incident.share_token;
}
document.addEventListener('DOMContentLoaded', ()=>{ const btn = document.getElementById('shareBtn'); if(btn) btn.addEventListener('click', async ()=>{ const parts = location.pathname.split('/'); const iid = parts[parts.length-1]; const pid = document.getElementById('projectId').value || '1'; const resp = await fetch(`/projects/${pid}/incidents/${iid}/share`, {method:'POST'}); const j = await resp.json(); document.getElementById('shareInfo').innerText = 'Public: ' + location.origin + '/incidents/public/' + j.share_token; }); loadIncidentDetail(); });
