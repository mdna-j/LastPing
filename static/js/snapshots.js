// /ui/snapshots client script
async function loadSnapshots(){
  const pid = document.getElementById('projectId').value || '1';
  const out = document.getElementById('list');
  out.innerText = 'Loading...';
  try{
    const [uptimeRes, mttrRes, snapsRes] = await Promise.all([
      fetch(`/projects/${pid}/metrics/uptime`),
      fetch(`/projects/${pid}/metrics/mttr`),
      fetch(`/projects/${pid}/metrics/snapshots`),
    ]);
    if(!uptimeRes.ok || !mttrRes.ok || !snapsRes.ok){ out.innerText = 'Failed to load metrics'; return }
    const uptimeJson = await uptimeRes.json();
    const mttrJson = await mttrRes.json();
    const snapsJson = await snapsRes.json();

    // formatted header
    let html = '<div class="row"><strong>Uptime</strong>: ';
    if(uptimeJson.uptime !== undefined) html += uptimeJson.uptime.toFixed(2) + '%';
    else if(uptimeJson.check_id) html += (uptimeJson.uptime || 0).toFixed(2) + '%';
    else html += JSON.stringify(uptimeJson);
    html += ' &nbsp; <strong>MTTR</strong>: ' + (mttrJson.mttr_seconds ? (mttrJson.mttr_seconds.toFixed(1) + 's') : 'N/A') + '</div>';

    // snapshots table
    html += '<h3>Recent Snapshots</h3><table class="table"><thead><tr><th>ID</th><th>Check</th><th>Window End</th><th>Uptime %</th><th>MTTR (s)</th></tr></thead><tbody>';
    for(const s of snapsJson){
      html += `<tr><td>${s.id}</td><td>${s.check_id}</td><td>${s.window_end}</td><td>${s.uptime_percent.toFixed(2)}</td><td>${s.mttr_seconds? s.mttr_seconds.toFixed(1):'N/A'}</td></tr>`;
    }
    html += '</tbody></table>';

    out.innerHTML = html;
  }catch(e){ out.innerText = 'Error loading snapshots'; }
}
window.loadSnapshots = loadSnapshots;
document.addEventListener('DOMContentLoaded', ()=>{ const b = document.getElementById('loadSnapshotsBtn'); if(b) b.addEventListener('click', loadSnapshots); loadSnapshots(); });
