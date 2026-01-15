// /ui/incidents client script
async function createShare(pid, iid){
  const resp = await fetch(`/projects/${pid}/incidents/${iid}/share`, {method:'POST'});
  if(!resp.ok){ alert('Failed to create share'); return }
  const j = await resp.json();
  alert('Share token: ' + j.share_token + '\nPublic URL: ' + location.origin + '/incidents/public/' + j.share_token);
}
async function loadIncidents(){
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
window.createShare = createShare;
window.loadIncidents = loadIncidents;
document.addEventListener('DOMContentLoaded', ()=>{ const b = document.getElementById('loadIncidentsBtn'); if(b) b.addEventListener('click', loadIncidents); loadIncidents(); });
