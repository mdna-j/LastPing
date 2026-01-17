// /ui/incidents client script
async function createShare(pid, iid){
  const resp = await fetch(`/projects/${pid}/incidents/${iid}/share`, {method:'POST'});
  if(!resp.ok){ alert('Failed to create share'); return }
  const j = await resp.json();
  alert('Share token: ' + j.share_token + '\nPublic URL: ' + location.origin + '/incidents/public/' + j.share_token);
}
function headersManage(){ const at=document.getElementById('adminToken')?document.getElementById('adminToken').value:''; const ut=document.getElementById('userToken')?document.getElementById('userToken').value:null; const h={'Content-Type':'application/json'}; if(at) h['X-ADMIN-TOKEN']=at; if(ut) h['Authorization']='Bearer '+ut; return h; }
async function loadIncidents(){
  const pid = document.getElementById('projectId').value || '1';
  const resp = await fetch(`/projects/${pid}/incidents`);
  const el = document.getElementById('list');
  if(!resp.ok){ el.innerText = 'Failed to load incidents'; return }
  const json = await resp.json();
  if(!json.length){ el.innerHTML = '<div class="muted">No incidents</div>'; return }

  // group merged incidents under their target for a cleaner view
  const byId = {};
  const children = {};
  json.forEach(i => { byId[i.id] = i; if(i.merged_into){ children[i.merged_into] = children[i.merged_into] || []; children[i.merged_into].push(i); } });

  const top = json.filter(i => !i.merged_into);
  el.innerHTML = top.map(i => {
    const subs = children[i.id] || [];
    const subsHtml = subs.length ? `<div style="margin-top:8px;padding-left:12px"><strong>Merged:</strong>` + subs.map(s => `<div class="card" style="margin-top:6px"><div><strong>Incident ${s.id}</strong> — check ${s.check_id} <span class="muted">(${s.status})</span></div><div class="muted">Started: ${s.started_at}</div></div>`).join('') + `</div>` : '';
    return `
    <div class="card">
      <div><strong>Incident ${i.id}</strong> — check ${i.check_id} <span class="muted">(${i.status})</span></div>
      <div class="muted">Started: ${i.started_at} ${i.resolved_at? ' | Resolved: '+i.resolved_at : ''} ${i.group_id? ' | group:'+i.group_id : ''}</div>
      <div style="margin-top:8px">
        <a class="btn" href="/ui/incidents/${i.id}">Details</a>
        <button class="btn" onclick="createShare(${pid}, ${i.id})" style="margin-left:8px">Create Share Link</button>
        <button class="btn btn-secondary" onclick="mergePrompt(${pid}, ${i.id})" style="margin-left:8px">Merge</button>
      </div>
      ${subsHtml}
    </div>
  `}).join('');
}
async function mergePrompt(pid, iid){
  const into = prompt('Merge incident '+iid+' into (target incident id):');
  if(!into) return;
  if(!confirm('Are you sure you want to merge incident '+iid+' into '+into+'?')) return;
  const btn = event && event.target ? event.target : null;
  if(btn) btn.disabled = true;
  try{
    const resp = await fetch(`/projects/${pid}/incidents/${iid}/merge`, {method:'POST', headers: headersManage(), body: JSON.stringify({into: parseInt(into)})});
    if(!resp.ok){ alert('Merge failed'); return }
    alert('Merged');
    loadIncidents();
  }finally{ if(btn) btn.disabled = false }
}
window.createShare = createShare;
window.loadIncidents = loadIncidents;
window.mergePrompt = mergePrompt;
document.addEventListener('DOMContentLoaded', ()=>{ const b = document.getElementById('loadIncidentsBtn'); if(b) b.addEventListener('click', loadIncidents); loadIncidents(); });
