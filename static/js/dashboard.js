// /ui/dashboard client script
function dashHeaders(){
  const apiKey = document.getElementById('apiKey').value || null;
  const headers = {};
  if(apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
  return headers;
}

function toIso(dt){
  return dt.toISOString().slice(0,19);
}

function getRange(){
  const start = document.getElementById('start').value || null;
  const end = document.getElementById('end').value || null;
  return {start, end};
}

function applyDefaultRange(){
  const startEl = document.getElementById('start');
  const endEl = document.getElementById('end');
  if(!startEl.value && !endEl.value){
    const end = new Date();
    const start = new Date(end.getTime() - 24*7*3600*1000);
    startEl.value = toIso(start);
    endEl.value = toIso(end);
  }
}

function statusBadge(status){
  if(!status) return '';
  const up = status.toLowerCase() === 'up';
  const down = status.toLowerCase() === 'down';
  const degraded = status.toLowerCase() === 'degraded';
  const cls = up ? 'status-up' : (down || degraded ? 'status-down' : '');
  return `<span class="badge ${cls}">${status}</span>`;
}

async function loadDashboard(){
  const pid = document.getElementById('projectId').value || '1';
  const headers = dashHeaders();
  const {start, end} = getRange();
  const params = new URLSearchParams();
  if(start) params.set('start', start);
  if(end) params.set('end', end);
  const q = (url)=> params.toString() ? `${url}?${params.toString()}` : url;

  // load checks (public)
  const checksRes = await fetch(`/projects/${pid}/checks`);
  const checks = checksRes.ok ? await checksRes.json() : [];
  const total = checks.length;
  const upCount = checks.filter(c => (c.status || '').toLowerCase() === 'up').length;
  const downCount = checks.filter(c => (c.status || '').toLowerCase() === 'down').length;
  const degradedCount = checks.filter(c => (c.status || '').toLowerCase() === 'degraded').length;

  // metrics (requires API key)
  let uptime = null;
  let mttr = null;
  let snaps = [];
  let trends = null;
  try{
    const [uptimeRes, mttrRes, snapsRes, trendRes] = await Promise.all([
      fetch(q(`/projects/${pid}/metrics/uptime`), {headers}),
      fetch(q(`/projects/${pid}/metrics/mttr`), {headers}),
      fetch(`/projects/${pid}/metrics/snapshots?limit=30`, {headers}),
      fetch(`/projects/${pid}/analytics/trends?days=7&interval=day`, {headers}),
    ]);
    if(uptimeRes.ok){ uptime = await uptimeRes.json(); }
    if(mttrRes.ok){ mttr = await mttrRes.json(); }
    if(snapsRes.ok){ snaps = await snapsRes.json(); }
    if(trendRes.ok){ trends = await trendRes.json(); }
  }catch(e){ /* ignore */ }

  // incidents (requires API key)
  const incidentsEl = document.getElementById('incidentsList');
  const incCountEl = document.getElementById('openIncidentsCount');
  if(headers.Authorization){
    const incRes = await fetch(`/projects/${pid}/incidents?status=open`, {headers});
    if(incRes.ok){
      const incs = await incRes.json();
      if(incCountEl) incCountEl.innerText = String(incs.length);
      if(!incs.length){ incidentsEl.innerHTML = '<div class="muted">No open incidents.</div>'; }
      else{
        const rows = incs.slice(0, 6).map(i => `<div class="card"><div><strong>#${i.id}</strong> check ${i.check_id} — ${i.status}</div><div class="muted">Started: ${i.started_at}</div></div>`).join('');
        incidentsEl.innerHTML = rows;
      }
    }else{
      incidentsEl.innerText = 'Failed to load incidents (check API key).';
      if(incCountEl) incCountEl.innerText = 'n/a';
    }
  }else{
    incidentsEl.innerText = 'Provide API key to load incidents.';
    if(incCountEl) incCountEl.innerText = 'locked';
  }

  // cards
  const cards = document.getElementById('cards');
  const uptimePct = uptime && uptime.uptime !== undefined ? uptime.uptime : (uptime && uptime.project_uptime_percent !== undefined ? uptime.project_uptime_percent : null);
  const mttrVal = mttr && mttr.mttr_seconds !== undefined ? mttr.mttr_seconds : null;
  let html = '';
  html += `<div class="card" style="min-width:180px"><div class="muted">Checks</div><div><strong>${total}</strong> total</div><div class="muted">${upCount} up · ${downCount} down · ${degradedCount} degraded</div></div>`;
  html += `<div class="card" style="min-width:180px"><div class="muted">Uptime</div><div><strong>${uptimePct !== null ? uptimePct.toFixed(2) + '%' : 'n/a'}</strong></div><div class="muted">range</div></div>`;
  html += `<div class="card" style="min-width:180px"><div class="muted">MTTR</div><div><strong>${mttrVal !== null ? mttrVal.toFixed(1) + 's' : 'n/a'}</strong></div><div class="muted">range</div></div>`;
  html += `<div class="card" style="min-width:180px"><div class="muted">Open incidents</div><div><strong id="openIncidentsCount">${headers.Authorization ? '…' : 'locked'}</strong></div><div class="muted">API key required</div></div>`;
  cards.innerHTML = html;

  // checks table
  const tbody = document.querySelector('#checksTable tbody');
  if(!checks.length){
    tbody.innerHTML = '<tr><td colspan="6" class="muted">No checks found.</td></tr>';
  }else{
    tbody.innerHTML = checks.map(c => {
      const lat = (c.last_latency_ms !== null && c.last_latency_ms !== undefined) ? `${Number(c.last_latency_ms).toFixed(1)}ms` : 'n/a';
      return `<tr><td>${c.name}</td><td>${c.type}</td><td>${statusBadge(c.status)}</td><td>${c.last_ping || 'n/a'}</td><td>${lat}</td><td>${c.region || ''}</td></tr>`;
    }).join('');
  }

  // charts
  try{
    if(typeof Chart !== 'undefined'){
      if(window._uptimeChart){ try{ window._uptimeChart.destroy(); }catch(e){} }
      if(window._trendChart){ try{ window._trendChart.destroy(); }catch(e){} }

      const uptimeLabels = (snaps || []).map(s => s.window_end).reverse();
      const uptimeData = (snaps || []).map(s => s.uptime_percent).reverse();
      const uctx = document.getElementById('uptimeChart').getContext('2d');
      window._uptimeChart = new Chart(uctx, {
        type: 'line',
        data: { labels: uptimeLabels, datasets: [{ label: 'Uptime %', data: uptimeData, borderColor: 'rgba(51,122,183,1)', backgroundColor: 'rgba(51,122,183,0.1)', tension: 0.2, fill: true }] },
        options: { scales: { y: { beginAtZero: true, suggestedMax: 100 } }, plugins: { legend: { display: true } } }
      });

      const tlabels = (trends && trends.series) ? trends.series.map(s => s.bucket_start) : [];
      const tdata = (trends && trends.series) ? trends.series.map(s => s.down_events) : [];
      const tctx = document.getElementById('trendChart').getContext('2d');
      window._trendChart = new Chart(tctx, {
        type: 'bar',
        data: { labels: tlabels, datasets: [{ label: 'Down events', data: tdata, backgroundColor: 'rgba(217,83,79,0.6)' }] },
        options: { plugins: { legend: { display: true } } }
      });
    }
  }catch(e){ /* ignore */ }
}

document.addEventListener('DOMContentLoaded', ()=>{
  applyDefaultRange();
  const btn = document.getElementById('loadDashboardBtn');
  if(btn) btn.addEventListener('click', loadDashboard);
  loadDashboard();
});
