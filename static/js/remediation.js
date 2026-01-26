function headersRemediation(){
  const apiKey = document.getElementById('apiKey').value;
  const admin = document.getElementById('adminToken').value;
  const h = {'Content-Type':'application/json'};
  if(apiKey) h['Authorization'] = 'Bearer ' + apiKey;
  if(admin) h['X-ADMIN-TOKEN'] = admin;
  return h;
}

async function refreshRemediation(){
  const pid = document.getElementById('projectId').value || '1';
  const h = headersRemediation();
  const [hooksRes, logsRes] = await Promise.all([
    fetch(`/projects/${pid}/remediation/hooks`, {headers: h}),
    fetch(`/projects/${pid}/remediation/logs`, {headers: h}),
  ]);
  if(!hooksRes.ok || !logsRes.ok){ alert('Failed to load remediation data'); return; }
  const hooks = await hooksRes.json();
  const logs = await logsRes.json();
  const hooksDiv = document.getElementById('hooks');
  hooksDiv.innerHTML = hooks.map(hk => `<div class="card"><div><strong>${hk.event_type}</strong> ${hk.url} ${hk.method} enabled:${hk.enabled} check:${hk.check_id||'all'}</div><button class="btn" onclick="deleteHook(${pid}, ${hk.id})">Delete</button></div>`).join('');
  const logsDiv = document.getElementById('logs');
  logsDiv.innerHTML = logs.map(l => `<div class="card"><div>${l.created_at} ${l.event_type} status:${l.status} code:${l.response_code||''}</div><div class="muted">${l.message||''}</div></div>`).join('');
}

async function addHook(){
  const pid = document.getElementById('projectId').value || '1';
  const payload = {
    check_id: parseInt(document.getElementById('hookCheckId').value || '0') || null,
    event_type: document.getElementById('hookEvent').value,
    url: document.getElementById('hookUrl').value,
    method: document.getElementById('hookMethod').value || 'POST',
    cooldown_seconds: parseInt(document.getElementById('hookCooldown').value || '0') || 900,
    secret: document.getElementById('hookSecret').value || null,
    enabled: document.getElementById('hookEnabled').checked
  };
  const res = await fetch(`/projects/${pid}/remediation/hooks`, {method:'POST', headers: headersRemediation(), body: JSON.stringify(payload)});
  if(!res.ok){ alert('Failed to add hook'); return; }
  refreshRemediation();
}

async function deleteHook(pid, hookId){
  if(!confirm('Delete hook '+hookId+'?')) return;
  const res = await fetch(`/projects/${pid}/remediation/hooks/${hookId}`, {method:'DELETE', headers: headersRemediation()});
  if(!res.ok){ alert('Delete failed'); return; }
  refreshRemediation();
}

document.addEventListener('DOMContentLoaded', ()=>{
  document.getElementById('refreshBtn').addEventListener('click', refreshRemediation);
  document.getElementById('addHookBtn').addEventListener('click', addHook);
  refreshRemediation();
});
