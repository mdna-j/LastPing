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
  const statusFilter = document.getElementById('approvalStatus').value;
  const approvalsUrl = statusFilter ? `/projects/${pid}/remediation/approvals?status_filter=${encodeURIComponent(statusFilter)}` : `/projects/${pid}/remediation/approvals`;
  const [hooksRes, logsRes, approvalsRes] = await Promise.all([
    fetch(`/projects/${pid}/remediation/hooks`, {headers: h}),
    fetch(`/projects/${pid}/remediation/logs`, {headers: h}),
    fetch(approvalsUrl, {headers: h}),
  ]);
  if(!hooksRes.ok || !logsRes.ok || !approvalsRes.ok){ alert('Failed to load remediation data'); return; }
  const hooks = await hooksRes.json();
  const logs = await logsRes.json();
  const approvals = await approvalsRes.json();
  const hooksDiv = document.getElementById('hooks');
  hooksDiv.innerHTML = hooks.map(hk => `<div class="card"><div><strong>${hk.event_type}</strong> ${hk.url} ${hk.method} enabled:${hk.enabled} approval:${hk.require_approval} check:${hk.check_id||'all'}</div><button class="btn" onclick="deleteHook(${pid}, ${hk.id})">Delete</button></div>`).join('');
  const approvalsDiv = document.getElementById('approvals');
  approvalsDiv.innerHTML = approvals.map(a => {
    const actions = a.status === 'pending'
      ? `<button class="btn" onclick="approveApproval(${pid}, ${a.id})">Approve</button> <button class="btn btn-secondary" onclick="denyApproval(${pid}, ${a.id})">Deny</button>`
      : '';
    return `<div class="card"><div><strong>${a.status}</strong> hook:${a.hook_id} check:${a.check_id} ${a.event_type} ${a.reason||''}</div><div class="muted">${a.requested_at||''} ${a.decided_by? 'by '+a.decided_by:''} ${a.execution_status? 'exec:'+a.execution_status:''}</div>${actions}</div>`;
  }).join('');
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
    enabled: document.getElementById('hookEnabled').checked,
    require_approval: document.getElementById('hookRequireApproval').checked
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

async function approveApproval(pid, approvalId){
  const res = await fetch(`/projects/${pid}/remediation/approvals/${approvalId}/approve`, {method:'POST', headers: headersRemediation()});
  if(!res.ok){ alert('Approve failed'); return; }
  refreshRemediation();
}

async function denyApproval(pid, approvalId){
  const res = await fetch(`/projects/${pid}/remediation/approvals/${approvalId}/deny`, {method:'POST', headers: headersRemediation()});
  if(!res.ok){ alert('Deny failed'); return; }
  refreshRemediation();
}

document.addEventListener('DOMContentLoaded', ()=>{
  document.getElementById('refreshBtn').addEventListener('click', refreshRemediation);
  document.getElementById('addHookBtn').addEventListener('click', addHook);
  document.getElementById('refreshApprovalsBtn').addEventListener('click', refreshRemediation);
  refreshRemediation();
});
