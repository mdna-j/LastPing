// Client-side script for /ui/checks
function headers(){
  const at = document.getElementById('adminToken').value;
  const ut = document.getElementById('userToken').value;
  const h = {'Content-Type':'application/json'};
  if(at) h['X-ADMIN-TOKEN'] = at;
  if(ut) h['Authorization'] = 'Bearer ' + ut;
  return h;
}
async function loadChecks(){
  const pid = document.getElementById('projectId').value || '1';
  let isOwner = false;
  try{
    const r = await fetch(`/users/projects/${pid}/role`, {headers: headers()});
    if(r.ok){ const jr = await r.json(); isOwner = jr.role === 'owner'; }
  }catch(e){ }
  const resp = await fetch(`/projects/${pid}/checks`, {headers: headers()});
  const el = document.getElementById('list');
  if(!resp.ok){ el.innerText='Failed to load'; return }
  const json = await resp.json();
  const adminPresent = !!document.getElementById('adminToken').value;
  const cb = document.getElementById('createBtn');
  if(cb) cb.style.display = (isOwner || adminPresent) ? 'inline-block' : 'none';
  el.innerHTML = json.map(c=>`<div class="card"><div><strong>${c.name}</strong> (${c.type}) <span class="muted">status:${c.status}</span> ${c.region?'<span class="muted">region:'+c.region+'</span>':''}</div><div style="margin-top:8px"> <a href="/ui/checks/${c.id}">Manage</a>${(isOwner||adminPresent)? ' <button onclick="del('+pid+','+c.id+')" style="margin-left:8px">Delete</button>':''}</div></div>`).join('');
}
async function createCheck(){
  const pid = document.getElementById('projectId').value || '1';
  const body = {
    name: document.getElementById('name').value,
    type: document.getElementById('type').value,
    url: document.getElementById('url').value,
    host: document.getElementById('host').value || null,
    port: parseInt(document.getElementById('port').value || '0') || null,
    dns_record_type: document.getElementById('dnsRecordType').value || null,
    interval: parseInt(document.getElementById('interval').value || '0') || null,
    expected_interval: parseInt(document.getElementById('expectedInterval').value || '0') || null,
    grace_period: parseInt(document.getElementById('gracePeriod').value || '0') || null,
    latency_threshold_ms: parseInt(document.getElementById('latencyThreshold').value || '0') || null,
    region: document.getElementById('region').value || null,
    alert_enabled: !!document.getElementById('alertEnabled').checked,
    alert_after: parseInt(document.getElementById('alertAfter').value || '0') || null,
    alert_cooldown: parseInt(document.getElementById('alertCooldown').value || '0') || null
  };
  const resp = await fetch(`/projects/${pid}/checks`, {method:'POST', headers: headers(), body: JSON.stringify(body)});
  if(resp.status==201){ alert('Created'); loadChecks(); } else { alert('Create failed'); }
}
async function del(pid,id){
  if(!confirm('Delete check '+id+'?')) return;
  const resp = await fetch(`/projects/${pid}/checks/${id}`, {method:'DELETE', headers: headers()});
  if(resp.ok){ alert('Deleted'); loadChecks(); } else { alert('Delete failed'); }
}

// expose for inline onclick attributes
window.headers = headers;
window.loadChecks = loadChecks;
window.createCheck = createCheck;
window.del = del;

document.addEventListener('DOMContentLoaded', ()=>{ const btn = document.getElementById('loadChecksBtn'); if(btn) btn.addEventListener('click', loadChecks); const create = document.getElementById('createBtn'); if(create) create.addEventListener('click', createCheck); loadChecks(); });
