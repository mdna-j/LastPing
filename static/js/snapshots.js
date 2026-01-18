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

    // snapshots chart
    html += '<h3>Recent Snapshots</h3>';
    html += '<div style="display:flex;flex-direction:column">';
    html += '<div style="width:100%"><canvas id="uptimeChartCanvas" height="160"></canvas></div>';
    html += '<div style="margin-top:8px"><table class="table"><thead><tr><th>ID</th><th>Check</th><th>Window End</th><th>Uptime %</th><th>MTTR (s)</th></tr></thead><tbody>';
    for(const s of snapsJson){
      html += `<tr><td>${s.id}</td><td>${s.check_id}</td><td>${s.window_end}</td><td>${s.uptime_percent.toFixed(2)}</td><td>${s.mttr_seconds? s.mttr_seconds.toFixed(1):'N/A'}</td></tr>`;
    }
    html += '</tbody></table></div></div>';

    // render chart using Chart.js if available
    try{
      const labels = snapsJson.map(s => s.window_end).reverse();
      const data = snapsJson.map(s => s.uptime_percent).reverse();
      const canvas = document.getElementById('uptimeChartCanvas');
      if(canvas && typeof Chart !== 'undefined'){
        // destroy existing chart instance if present
        if(window._uptimeChart){ try{ window._uptimeChart.destroy(); }catch(e){} window._uptimeChart = null }
        const ctx = canvas.getContext('2d');
        window._uptimeChart = new Chart(ctx, {
          type: 'line',
          data: {
            labels: labels,
            datasets: [{
              label: 'Uptime %',
              data: data,
              borderColor: 'rgba(51,122,183,1)',
              backgroundColor: 'rgba(51,122,183,0.1)',
              tension: 0.2,
              fill: true,
            }]
          },
          options: {
            scales: {
              y: { beginAtZero: true, suggestedMax: 100 }
            },
            plugins: { legend: { display: true } }
          }
        });
      }
    }catch(e){ /* non-fatal */ }

    out.innerHTML = html;
  }catch(e){ out.innerText = 'Error loading snapshots'; }
}
window.loadSnapshots = loadSnapshots;
document.addEventListener('DOMContentLoaded', ()=>{ const b = document.getElementById('loadSnapshotsBtn'); if(b) b.addEventListener('click', loadSnapshots); loadSnapshots(); });
