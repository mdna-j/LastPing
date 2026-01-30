let oncallChecks = [];
let oncallEscalations = [];

function headersOncall(){
  const apiKey = document.getElementById('apiKey').value;
  const admin = document.getElementById('adminToken').value;
  const h = {'Content-Type':'application/json'};
  if(apiKey) h['Authorization'] = 'Bearer ' + apiKey;
  if(admin) h['X-ADMIN-TOKEN'] = admin;
  return h;
}

async function refreshOncall(){
  const pid = document.getElementById('projectId').value || '1';
  const h = headersOncall();
  const escFilter = document.getElementById('escFilterCheckId').value;
  const escUrl = escFilter ? `/projects/${pid}/oncall/escalations?check_id=${encodeURIComponent(escFilter)}` : `/projects/${pid}/oncall/escalations`;
  const [rots, escs, alerts] = await Promise.all([
    fetch(`/projects/${pid}/oncall/rotations`, {headers: h}),
    fetch(escUrl, {headers: h}),
    fetch(`/projects/${pid}/oncall/alerts?status_filter=open`, {headers: h}),
  ]);
  if(!rots.ok || !escs.ok || !alerts.ok){ alert('Failed to load on-call data'); return; }
  const rotJson = await rots.json();
  const escJson = await escs.json();
  const alertJson = await alerts.json();
  oncallEscalations = escJson || [];

  const rotDiv = document.getElementById('rotations');
  const memberDiv = document.getElementById('members');
  memberDiv.innerHTML = '';
  rotDiv.innerHTML = rotJson.map(r => `<div class="card"><div><strong>${r.name}</strong> (id:${r.id}) interval:${r.interval_minutes} enabled:${r.enabled}</div><button class="btn" onclick="deleteRotation(${pid}, ${r.id})">Delete</button></div>`).join('');
  for(const r of rotJson){
    const mres = await fetch(`/projects/${pid}/oncall/rotations/${r.id}/members`, {headers: h});
    if(!mres.ok) continue;
    const members = await mres.json();
    const html = members.map(m => `<div class="card"><div>Rotation ${r.id} - ${m.name} ${m.email||''} ${m.phone||''} order:${m.order}</div></div>`).join('');
    memberDiv.innerHTML += html;
  }

  const escDiv = document.getElementById('escalations');
  escDiv.innerHTML = escJson.map(e => `<div class="card"><div>${e.check_id? 'check:'+e.check_id:'project-wide'} level:${e.level} ${e.target_type} delay:${e.delay_minutes} ${e.rotation_id? 'rotation:'+e.rotation_id:''} ${e.target_value? 'target:'+e.target_value:''}</div><button class="btn" onclick="deleteEscalation(${pid}, ${e.id})">Delete</button></div>`).join('');

  const alertDiv = document.getElementById('alerts');
  alertDiv.innerHTML = alertJson.map(a => `<div class="card"><div>Alert ${a.id} check:${a.check_id} event:${a.event_type} level:${a.escalation_level}</div><div class="muted">${a.message||''}</div><button class="btn" onclick="closeAlert(${pid}, ${a.id})">Close</button></div>`).join('');

  renderPolicyChain();
}

async function loadChecksForEscalations(){
  const pid = document.getElementById('projectId').value || '1';
  const h = headersOncall();
  const select = document.getElementById('escCheckSelect');
  const filterSelect = document.getElementById('escFilterCheckSelect');
  const policySelect = document.getElementById('policyCheckSelect');
  if(!select || !filterSelect) return;
  select.innerHTML = '<option value="">(project-wide)</option>';
  filterSelect.innerHTML = '<option value="">(all checks)</option>';
  if(policySelect) policySelect.innerHTML = '<option value="">(project-wide)</option>';
  if(!h.Authorization) return;
  try{
    const res = await fetch(`/projects/${pid}/checks`, {headers: h});
    if(!res.ok) return;
    const arr = await res.json();
    oncallChecks = arr || [];
    for(const c of arr){
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.text = `${c.name || 'check'} (#${c.id})`;
      select.appendChild(opt);
      const opt2 = opt.cloneNode(true);
      filterSelect.appendChild(opt2);
      if(policySelect){
        const opt3 = opt.cloneNode(true);
        policySelect.appendChild(opt3);
      }
    }
  }catch(e){ }
}

function renderPolicyChain(){
  const container = document.getElementById('policyChain');
  if(!container) return;
  const select = document.getElementById('policyCheckSelect');
  const selected = select ? select.value : '';
  const checkId = selected ? parseInt(selected) : null;
  const chain = oncallEscalations.filter(e => (checkId ? e.check_id === checkId : e.check_id === null));
  chain.sort((a, b) => (a.level || 0) - (b.level || 0));
  if(!chain.length){
    container.innerHTML = '<div class="muted">No escalation steps for this selection.</div>';
    return;
  }
  const rows = chain.map(e => {
    const target = e.target_type === 'rotation' ? `rotation:${e.rotation_id}` : (e.target_value || '');
    const enabled = e.enabled === false ? 'disabled' : 'enabled';
    return `<div class="card"><div>level ${e.level} · ${e.target_type} · ${target} · delay ${e.delay_minutes}m · ${enabled}</div><button class="btn btn-secondary" onclick="deleteEscalation(${document.getElementById('projectId').value || '1'}, ${e.id})">Delete</button></div>`;
  }).join('');
  container.innerHTML = rows;
}

async function addPolicyStep(){
  const pid = document.getElementById('projectId').value || '1';
  const checkSel = document.getElementById('policyCheckSelect');
  const checkId = checkSel && checkSel.value ? parseInt(checkSel.value) : null;
  const type = document.getElementById('policyType').value;
  const delay = parseInt(document.getElementById('policyDelay').value || '0') || 0;
  const rotationId = parseInt(document.getElementById('policyRotationId').value || '0') || null;
  const target = document.getElementById('policyTarget').value || null;
  const enabled = document.getElementById('policyEnabled').checked;
  const chain = oncallEscalations.filter(e => (checkId ? e.check_id === checkId : e.check_id === null));
  const maxLevel = chain.reduce((m, e) => Math.max(m, e.level || 0), -1);
  const level = maxLevel + 1;
  if(type === 'rotation' && !rotationId){ alert('Rotation ID required'); return; }
  if((type === 'email' || type === 'sms') && !target){ alert('Target required'); return; }
  const payload = {check_id: checkId, level, delay_minutes: delay, target_type: type, rotation_id: rotationId, target_value: target, enabled};
  const res = await fetch(`/projects/${pid}/oncall/escalations`, {method:'POST', headers: headersOncall(), body: JSON.stringify(payload)});
  if(!res.ok){ alert('Failed to add escalation'); return; }
  refreshOncall();
}

async function addRotation(){
  const pid = document.getElementById('projectId').value || '1';
  const payload = {
    name: document.getElementById('rotName').value,
    interval_minutes: parseInt(document.getElementById('rotInterval').value || '0') || 1440
  };
  const res = await fetch(`/projects/${pid}/oncall/rotations`, {method:'POST', headers: headersOncall(), body: JSON.stringify(payload)});
  if(!res.ok){ alert('Failed to add rotation'); return; }
  refreshOncall();
}

async function addMember(){
  const pid = document.getElementById('projectId').value || '1';
  const rotId = parseInt(document.getElementById('memberRotationId').value || '0');
  if(!rotId){ alert('Rotation ID required'); return; }
  const payload = {
    rotation_id: rotId,
    name: document.getElementById('memberName').value,
    email: document.getElementById('memberEmail').value || null,
    phone: document.getElementById('memberPhone').value || null,
    order: parseInt(document.getElementById('memberOrder').value || '0') || 0
  };
  const res = await fetch(`/projects/${pid}/oncall/rotations/${rotId}/members`, {method:'POST', headers: headersOncall(), body: JSON.stringify(payload)});
  if(!res.ok){ alert('Failed to add member'); return; }
  refreshOncall();
}

async function addEscalation(){
  const pid = document.getElementById('projectId').value || '1';
  const payload = {
    check_id: parseInt(document.getElementById('escCheckId').value || '0') || null,
    level: parseInt(document.getElementById('escLevel').value || '0') || 0,
    delay_minutes: parseInt(document.getElementById('escDelay').value || '0') || 15,
    target_type: document.getElementById('escType').value,
    rotation_id: parseInt(document.getElementById('escRotationId').value || '0') || null,
    target_value: document.getElementById('escTarget').value || null
  };
  const res = await fetch(`/projects/${pid}/oncall/escalations`, {method:'POST', headers: headersOncall(), body: JSON.stringify(payload)});
  if(!res.ok){ alert('Failed to add escalation'); return; }
  refreshOncall();
}

async function deleteRotation(pid, rid){
  if(!confirm('Delete rotation '+rid+'?')) return;
  const res = await fetch(`/projects/${pid}/oncall/rotations/${rid}`, {method:'DELETE', headers: headersOncall()});
  if(!res.ok){ alert('Delete failed'); return; }
  refreshOncall();
}

async function deleteEscalation(pid, eid){
  if(!confirm('Delete escalation '+eid+'?')) return;
  const res = await fetch(`/projects/${pid}/oncall/escalations/${eid}`, {method:'DELETE', headers: headersOncall()});
  if(!res.ok){ alert('Delete failed'); return; }
  refreshOncall();
}

async function closeAlert(pid, aid){
  const res = await fetch(`/projects/${pid}/oncall/alerts/${aid}/close`, {method:'POST', headers: headersOncall()});
  if(!res.ok){ alert('Close failed'); return; }
  refreshOncall();
}

document.addEventListener('DOMContentLoaded', ()=>{
  document.getElementById('refreshBtn').addEventListener('click', refreshOncall);
  document.getElementById('addRotationBtn').addEventListener('click', addRotation);
  document.getElementById('addMemberBtn').addEventListener('click', addMember);
  document.getElementById('addEscBtn').addEventListener('click', addEscalation);
  document.getElementById('policyAddBtn').addEventListener('click', addPolicyStep);
  const policyRefresh = document.getElementById('policyRefreshBtn');
  if(policyRefresh) policyRefresh.addEventListener('click', renderPolicyChain);
  document.getElementById('escFilterBtn').addEventListener('click', refreshOncall);
  document.getElementById('escClearFilterBtn').addEventListener('click', ()=>{
    document.getElementById('escFilterCheckId').value = '';
    const sel = document.getElementById('escFilterCheckSelect');
    if(sel) sel.value = '';
    refreshOncall();
  });
  const escSelect = document.getElementById('escCheckSelect');
  if(escSelect){
    escSelect.addEventListener('change', ()=>{
      document.getElementById('escCheckId').value = escSelect.value || '';
    });
  }
  const escFilterSelect = document.getElementById('escFilterCheckSelect');
  if(escFilterSelect){
    escFilterSelect.addEventListener('change', ()=>{
      document.getElementById('escFilterCheckId').value = escFilterSelect.value || '';
    });
  }
  const policySelect = document.getElementById('policyCheckSelect');
  if(policySelect){
    policySelect.addEventListener('change', renderPolicyChain);
  }
  const apiKey = document.getElementById('apiKey');
  if(apiKey){
    apiKey.addEventListener('change', loadChecksForEscalations);
  }
  const pid = document.getElementById('projectId');
  if(pid){
    pid.addEventListener('change', loadChecksForEscalations);
  }
  loadChecksForEscalations();
  refreshOncall();
});
