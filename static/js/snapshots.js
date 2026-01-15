// /ui/snapshots client script
async function loadSnapshots(){
  const pid = document.getElementById('projectId').value || '1';
  const resp = await fetch(`/projects/${pid}/metrics/uptime`);
  const el = document.getElementById('list');
  if(!resp.ok){ el.innerText = 'Failed to load'; return }
  const json = await resp.json();
  el.innerHTML = '<pre>' + JSON.stringify(json, null, 2) + '</pre>';
}
window.loadSnapshots = loadSnapshots;
document.addEventListener('DOMContentLoaded', ()=>{ const b = document.getElementById('loadSnapshotsBtn'); if(b) b.addEventListener('click', loadSnapshots); loadSnapshots(); });
