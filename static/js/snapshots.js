// /ui/snapshots client script
async function loadSnapshots(){
  const pid = document.getElementById('projectId').value || '1';
  const checkId = document.getElementById('checkId').value || null;
  const rawApiInput = document.getElementById('apiKey').value || null;
  // If input is masked (e.g., '******') prefer saved key
  const apiKey = (rawApiInput === '******' || rawApiInput === '••••••') ? window._savedApiKey || null : rawApiInput;
  const start = document.getElementById('start').value || null;
  const end = document.getElementById('end').value || null;
  const out = document.getElementById('list');
  out.innerText = 'Loading...';
  try{
    const q = (url)=>{
      const params = new URLSearchParams();
      if(checkId) params.set('check_id', checkId);
      if(start) params.set('start', start);
      if(end) params.set('end', end);
      return `${url}?${params.toString()}`;
    };
    const headers = {};
    if(apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
    const fetchWith = (url)=> fetch(url, {headers});
    const [uptimeRes, mttrRes, snapsRes] = await Promise.all([
      fetchWith(q(`/projects/${pid}/metrics/uptime`)),
      fetchWith(q(`/projects/${pid}/metrics/mttr`)),
      fetchWith(q(`/projects/${pid}/metrics/snapshots`)),
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
    // persist last used inputs locally (apiKey is stored only if remember checked)
    try{
      const remember = document.getElementById('rememberApiKey') && document.getElementById('rememberApiKey').checked;
      const payload = {projectId: pid, checkId: checkId, start: start, end: end, rememberApiKey: !!remember};
      if(remember && apiKey) payload.apiKey = apiKey;
      localStorage.setItem('lastSnapshotsPrefs', JSON.stringify(payload));
      // if remembered, keep saved copy in memory for masked input
      if(remember && apiKey) window._savedApiKey = apiKey;
    }catch(e){}
  }catch(e){ out.innerText = 'Error loading snapshots'; }
}

function loadPrefs(){
  try{
    const raw = localStorage.getItem('lastSnapshotsPrefs');
    if(!raw) return;
    const p = JSON.parse(raw);
    if(p.projectId) document.getElementById('projectId').value = p.projectId;
    if(p.checkId) document.getElementById('checkId').value = p.checkId;
    if(p.apiKey){ window._savedApiKey = p.apiKey; document.getElementById('apiKey').value = '******'; }
    if(p.start) document.getElementById('start').value = p.start;
    if(p.end) document.getElementById('end').value = p.end;
    if(p.rememberApiKey) document.getElementById('rememberApiKey').checked = true;
  }catch(e){}
}

function savePrefs(){
  try{
    const pid = document.getElementById('projectId').value || '1';
    const checkId = document.getElementById('checkId').value || null;
    const apiKey = document.getElementById('apiKey').value || null;
    const start = document.getElementById('start').value || null;
    const end = document.getElementById('end').value || null;
    const remember = document.getElementById('rememberApiKey') && document.getElementById('rememberApiKey').checked;
    const payload = {projectId: pid, checkId: checkId, start: start, end: end, rememberApiKey: !!remember};
    if(remember && apiKey) payload.apiKey = apiKey;
    if(!remember) localStorage.removeItem('lastSnapshotsPrefs');
    localStorage.setItem('lastSnapshotsPrefs', JSON.stringify(payload));
    alert('Preferences saved');
  }catch(e){ alert('Failed to save prefs'); }
}

async function exportCsv(){
  const pid = document.getElementById('projectId').value || '1';
  const checkId = document.getElementById('checkId').value || null;
  const apiKey = document.getElementById('apiKey').value || null;
  const start = document.getElementById('start').value || null;
  const end = document.getElementById('end').value || null;
  const params = new URLSearchParams();
  if(checkId) params.set('check_id', checkId);
  if(start) params.set('start', start);
  if(end) params.set('end', end);
  const headers = {};
  // if input is masked, use saved key
  const apiUsed = (apiKey === '******' || apiKey === '••••••') ? window._savedApiKey || null : apiKey;
  if(apiUsed) headers['Authorization'] = `Bearer ${apiUsed}`;
  const res = await fetch(`/projects/${pid}/metrics/snapshots?${params.toString()}`, {headers});
  if(!res.ok){ alert('Failed to fetch snapshots for CSV'); return }
  const data = await res.json();
  if(!data || !data.length){ alert('No snapshot data'); return }
  // build CSV
  const hdr = ['id','project_id','check_id','window_start','window_end','uptime_percent','mttr_seconds'];
  const rows = [hdr.join(',')];
  for(const r of data){
    rows.push([r.id,r.project_id,r.check_id,`"${r.window_start}"`,`"${r.window_end}"`,r.uptime_percent,r.mttr_seconds].join(','));
  }
  const csv = rows.join('\n');
  const blob = new Blob([csv], {type: 'text/csv'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `snapshots_${pid}.csv`; document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}

async function loadChecks(){
  const pid = document.getElementById('projectId').value || '1';
  const rawApi = document.getElementById('apiKey').value || null;
  const apiKey = (rawApi === '******' || rawApi === '••••••') ? window._savedApiKey || null : rawApi;
  const sel = document.getElementById('checkId');
  // clear existing
  sel.innerHTML = '<option value="">(all)</option>';
  if(!apiKey) return; // cannot fetch checks without API key
  try{
    const res = await fetch(`/projects/${pid}/checks`, {headers: {'Authorization': `Bearer ${apiKey}`}});
    if(!res.ok) return;
    const arr = await res.json();
    for(const c of arr){
      const opt = document.createElement('option'); opt.value = c.id; opt.text = c.name; sel.appendChild(opt);
    }
  }catch(e){ }
}

function isIsoTimestamp(s){
  if(!s) return false;
  const t = Date.parse(s);
  return !Number.isNaN(t);
}

function applyPreset(hours){
  const end = new Date();
  const start = new Date(end.getTime() - hours*3600*1000);
  document.getElementById('start').value = start.toISOString().slice(0,19);
  document.getElementById('end').value = end.toISOString().slice(0,19);
}

function showApiKey(){
  const raw = window._savedApiKey || '';
  if(!raw){ alert('No saved API key'); return }
  document.getElementById('apiKey').value = raw;
}
window.loadSnapshots = loadSnapshots;
document.addEventListener('DOMContentLoaded', ()=>{ const b = document.getElementById('loadSnapshotsBtn'); if(b) b.addEventListener('click', ()=>{ if(!isIsoTimestamp(document.getElementById('start').value) || !isIsoTimestamp(document.getElementById('end').value)){ if(document.getElementById('start').value || document.getElementById('end').value){ alert('Start/end must be valid ISO timestamps (YYYY-MM-DDTHH:MM:SS)'); return } } loadSnapshots(); }); loadSnapshots(); });
document.addEventListener('DOMContentLoaded', ()=>{ loadPrefs(); const s = document.getElementById('savePrefsBtn'); if(s) s.addEventListener('click', savePrefs); const e = document.getElementById('exportCsvBtn'); if(e) e.addEventListener('click', exportCsv); const api = document.getElementById('apiKey'); if(api) api.addEventListener('change', loadChecks); const pid = document.getElementById('projectId'); if(pid) pid.addEventListener('change', loadChecks); const presets = document.createElement('div'); presets.style.marginTop='8px'; presets.innerHTML = '<button class="btn" id="p1h">Last 1h</button> <button class="btn" id="p6h">Last 6h</button> <button class="btn" id="p24h">Last 24h</button> <button class="btn" id="p7d">Last 7d</button>'; document.body.insertBefore(presets, document.getElementById('list'));
  document.getElementById('p1h').addEventListener('click', ()=>applyPreset(1));
  document.getElementById('p6h').addEventListener('click', ()=>applyPreset(6));
  document.getElementById('p24h').addEventListener('click', ()=>applyPreset(24));
  document.getElementById('p7d').addEventListener('click', ()=>applyPreset(24*7));
});
