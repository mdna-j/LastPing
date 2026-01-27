// /ui/reports client script
function reportHeaders(){
  const apiKey = document.getElementById('apiKey').value || null;
  const headers = {};
  if(apiKey) headers['Authorization'] = `Bearer ${apiKey}`;
  return headers;
}

function toIso(dt){
  return dt.toISOString().slice(0,19);
}

function applyPreset(days){
  const end = new Date();
  const start = new Date(end.getTime() - days*24*3600*1000);
  document.getElementById('start').value = toIso(start);
  document.getElementById('end').value = toIso(end);
}

function getRange(){
  const start = document.getElementById('start').value || null;
  const end = document.getElementById('end').value || null;
  const checkId = document.getElementById('checkId').value || null;
  return {start, end, checkId};
}

async function loadChecks(){
  const pid = document.getElementById('projectId').value || '1';
  const apiKey = document.getElementById('apiKey').value || null;
  const sel = document.getElementById('checkId');
  sel.innerHTML = '<option value="">(all)</option>';
  if(!apiKey) return;
  try{
    const res = await fetch(`/projects/${pid}/checks`, {headers: {'Authorization': `Bearer ${apiKey}`}});
    if(!res.ok) return;
    const arr = await res.json();
    for(const c of arr){
      const opt = document.createElement('option'); opt.value = c.id; opt.text = c.name; sel.appendChild(opt);
    }
  }catch(e){ }
}

async function loadReport(){
  const pid = document.getElementById('projectId').value || '1';
  const headers = reportHeaders();
  const {start, end, checkId} = getRange();
  const params = new URLSearchParams();
  if(start) params.set('start', start);
  if(end) params.set('end', end);
  if(checkId) params.set('check_id', checkId);
  const url = `/projects/${pid}/metrics/availability/history?${params.toString()}`;
  const res = await fetch(url, {headers});
  if(!res.ok){ alert('Failed to load report'); return; }
  const data = await res.json();

  const series = data.series || [];
  const tbody = document.querySelector('#reportTable tbody');
  if(!series.length){
    tbody.innerHTML = '<tr><td colspan="4" class="muted">No data in range.</td></tr>';
  }else{
    tbody.innerHTML = series.map(r => {
      const sloClass = r.slo_met ? 'status-up' : 'status-down';
      const slaClass = r.sla_met ? 'status-up' : 'status-down';
      const sloText = (r.slo_met === null || r.slo_met === undefined) ? 'n/a' : (r.slo_met ? 'met' : 'missed');
      const slaText = (r.sla_met === null || r.sla_met === undefined) ? 'n/a' : (r.sla_met ? 'met' : 'missed');
      return `<tr><td>${r.day}</td><td>${Number(r.uptime_percent).toFixed(2)}%</td><td><span class="badge ${sloClass}">${sloText}</span></td><td><span class="badge ${slaClass}">${slaText}</span></td></tr>`;
    }).join('');
  }

  try{
    if(typeof Chart !== 'undefined'){
      if(window._reportChart){ try{ window._reportChart.destroy(); }catch(e){} }
      const labels = series.map(r => r.day);
      const vals = series.map(r => r.uptime_percent);
      const ctx = document.getElementById('reportChart').getContext('2d');
      window._reportChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets: [{ label: 'Uptime %', data: vals, borderColor: 'rgba(51,122,183,1)', backgroundColor: 'rgba(51,122,183,0.1)', tension: 0.2, fill: true }] },
        options: { scales: { y: { beginAtZero: true, suggestedMax: 100 } } }
      });
    }
  }catch(e){ }
}

async function exportCsv(){
  const pid = document.getElementById('projectId').value || '1';
  const headers = reportHeaders();
  const {start, end, checkId} = getRange();
  const params = new URLSearchParams();
  if(start) params.set('start', start);
  if(end) params.set('end', end);
  if(checkId) params.set('check_id', checkId);
  const url = `/projects/${pid}/metrics/availability/report.csv?${params.toString()}`;
  const res = await fetch(url, {headers});
  if(!res.ok){ alert('Failed to export CSV'); return; }
  const text = await res.text();
  const blob = new Blob([text], {type: 'text/csv'});
  const dl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = dl;
  a.download = `availability_${pid}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(dl);
}

document.addEventListener('DOMContentLoaded', ()=>{
  const now = new Date();
  const start = new Date(now.getTime() - 30*24*3600*1000);
  document.getElementById('start').value = toIso(start);
  document.getElementById('end').value = toIso(now);

  document.getElementById('p7d').addEventListener('click', ()=>applyPreset(7));
  document.getElementById('p30d').addEventListener('click', ()=>applyPreset(30));
  document.getElementById('p90d').addEventListener('click', ()=>applyPreset(90));
  document.getElementById('p180d').addEventListener('click', ()=>applyPreset(180));
  document.getElementById('loadBtn').addEventListener('click', loadReport);
  document.getElementById('exportBtn').addEventListener('click', exportCsv);
  const api = document.getElementById('apiKey');
  if(api) api.addEventListener('change', loadChecks);
  const pid = document.getElementById('projectId');
  if(pid) pid.addEventListener('change', loadChecks);
  loadChecks();
  loadReport();
});
