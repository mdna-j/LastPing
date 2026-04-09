let oncallChecks = [];
let oncallEscalations = [];
let oncallRotations = [];
let policyDragLevel = null;

function renderOncallCards(checks, health, alerts){
  const root = document.getElementById("oncallCards");
  if(!root) return;

  const counts = window.LastPingShell
    ? window.LastPingShell.checkCounts(checks || [])
    : {total: 0, up: 0, down: 0, degraded: 0, flapping: 0};
  const openIncidents = health && health.active_incidents !== undefined && health.active_incidents !== null
    ? Number(health.active_incidents)
    : 0;
  const openAlerts = Array.isArray(alerts) ? alerts.length : 0;
  const stepCount = (oncallEscalations || []).length;

  const checksState = counts.down > 0 ? "kpi-critical" : (counts.degraded > 0 ? "kpi-warning" : "kpi-healthy");
  const incidentState = openIncidents > 0 ? "kpi-critical" : "kpi-healthy";
  const alertState = openAlerts > 0 ? "kpi-warning" : "kpi-healthy";
  const escalationState = stepCount > 0 ? "kpi-healthy" : "kpi-warning";

  root.innerHTML = [
    `<article class="card kpi-card ${checksState}"><div class="metric-label">Checks</div><div class="metric-value">${counts.total}</div><div class="metric-sub">${counts.up} up | ${counts.down} down | ${counts.degraded} degraded</div></article>`,
    `<article class="card kpi-card ${incidentState}"><div class="metric-label">Open incidents</div><div class="metric-value">${openIncidents}</div><div class="metric-sub">Current unresolved threads</div></article>`,
    `<article class="card kpi-card ${alertState}"><div class="metric-label">Open alerts</div><div class="metric-value">${openAlerts}</div><div class="metric-sub">Awaiting close/ack</div></article>`,
    `<article class="card kpi-card ${escalationState}"><div class="metric-label">Escalation steps</div><div class="metric-value">${stepCount}</div><div class="metric-sub">${oncallRotations.length} rotations configured</div></article>`,
  ].join("");
}

function headersOncall(){
  const apiKey = document.getElementById('apiKey').value;
  const admin = document.getElementById('adminToken').value;
  const h = {'Content-Type':'application/json'};
  if(apiKey) h['X-API-KEY'] = apiKey;
  if(admin) h['X-ADMIN-TOKEN'] = admin;
  return h;
}

function getProjectId(){
  return document.getElementById('projectId').value || '1';
}

function getSelectedPolicyCheck(){
  const select = document.getElementById('policyCheckSelect');
  return select && select.value ? parseInt(select.value) : null;
}

function setTriSelect(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  if (value === true) el.value = 'true';
  else if (value === false) el.value = 'false';
  else el.value = '';
}

function getTriSelect(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  if (el.value === 'true') return true;
  if (el.value === 'false') return false;
  return null;
}

function valOrNull(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  const v = (el.value || '').trim();
  return v ? v : null;
}

function numOrNull(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  const raw = (el.value || '').trim();
  if (raw === '') return null;
  const n = parseInt(raw, 10);
  return isNaN(n) ? null : n;
}

function numOrDefault(id, defaultValue) {
  const el = document.getElementById(id);
  if (!el) return defaultValue;
  const raw = (el.value || '').trim();
  if (raw === '') return defaultValue;
  const n = parseInt(raw, 10);
  return isNaN(n) ? defaultValue : n;
}

function _getCheckById(checkId) {
  if (!checkId) return null;
  const id = parseInt(checkId, 10);
  if (!id) return null;
  return (oncallChecks || []).find(c => c && c.id === id) || null;
}

function _setRoutingDisabled(disabled) {
  const ids = [
    'routingOncallEnabled',
    'routingOncallEmail',
    'routingSmsEnabled',
    'routingSmsTo',
    'routingSlackEnabled',
    'routingSlackWebhook',
    'routingDiscordEnabled',
    'routingDiscordWebhook',
    'routingPagerdutyEnabled',
    'routingPagerdutyKey',
    'routingWebhookEnabled',
    'routingGenericWebhook',
    'routingEscAfter',
    'routingEscCooldown',
  ];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (el) el.disabled = !!disabled;
  }
  const saveBtn = document.getElementById('routingSaveBtn');
  if (saveBtn) saveBtn.disabled = !!disabled;
  const resetBtn = document.getElementById('routingResetBtn');
  if (resetBtn) resetBtn.disabled = !!disabled;
}

function renderRoutingOverrides() {
  const scope = document.getElementById('routingScope');
  if (!scope) return;

  const checkId = getSelectedPolicyCheck();
  if (!checkId) {
    scope.textContent = 'No check selected (routing overrides are check-scoped).';
    _setRoutingDisabled(true);
    return;
  }
  const chk = _getCheckById(checkId);
  if (!chk) {
    scope.textContent = `Selected check #${checkId} (loading details...)`;
    _setRoutingDisabled(true);
    return;
  }

  scope.textContent = `Editing: ${chk.name || 'check'} (#${chk.id})`;
  _setRoutingDisabled(false);

  setTriSelect('routingOncallEnabled', chk.alert_oncall_enabled);
  setTriSelect('routingSmsEnabled', chk.alert_sms_enabled);
  setTriSelect('routingSlackEnabled', chk.alert_slack_enabled);
  setTriSelect('routingDiscordEnabled', chk.alert_discord_enabled);
  setTriSelect('routingPagerdutyEnabled', chk.alert_pagerduty_enabled);
  setTriSelect('routingWebhookEnabled', chk.alert_webhook_enabled);

  const oncallEmail = document.getElementById('routingOncallEmail');
  if (oncallEmail) oncallEmail.value = chk.alert_oncall_email || '';
  const smsTo = document.getElementById('routingSmsTo');
  if (smsTo) smsTo.value = chk.alert_sms_to || '';
  const slack = document.getElementById('routingSlackWebhook');
  if (slack) slack.value = chk.alert_slack_webhook_url || '';
  const discord = document.getElementById('routingDiscordWebhook');
  if (discord) discord.value = chk.alert_discord_webhook_url || '';
  const pdKey = document.getElementById('routingPagerdutyKey');
  if (pdKey) pdKey.value = chk.alert_pagerduty_integration_key || '';
  const gen = document.getElementById('routingGenericWebhook');
  if (gen) gen.value = chk.alert_generic_webhook_url || '';

  const escAfter = document.getElementById('routingEscAfter');
  if (escAfter) escAfter.value = chk.escalation_after_minutes || '';
  const escCooldown = document.getElementById('routingEscCooldown');
  if (escCooldown) {
    const cd = (chk.escalation_cooldown_seconds === null || chk.escalation_cooldown_seconds === undefined) ? 3600 : chk.escalation_cooldown_seconds;
    escCooldown.value = String(cd);
  }
}

function resetRoutingOverrides() {
  const checkId = getSelectedPolicyCheck();
  if (!checkId) return;
  setTriSelect('routingOncallEnabled', null);
  setTriSelect('routingSmsEnabled', null);
  setTriSelect('routingSlackEnabled', null);
  setTriSelect('routingDiscordEnabled', null);
  setTriSelect('routingPagerdutyEnabled', null);
  setTriSelect('routingWebhookEnabled', null);

  const ids = [
    'routingOncallEmail',
    'routingSmsTo',
    'routingSlackWebhook',
    'routingDiscordWebhook',
    'routingPagerdutyKey',
    'routingGenericWebhook',
    'routingEscAfter',
  ];
  for (const id of ids) {
    const el = document.getElementById(id);
    if (el) el.value = '';
  }
  const escCooldown = document.getElementById('routingEscCooldown');
  if (escCooldown) escCooldown.value = '3600';
}

async function saveRoutingOverrides() {
  const pid = getProjectId();
  const checkId = getSelectedPolicyCheck();
  if (!checkId) {
    alert('Select a check in Policy Builder first.');
    return;
  }
  const payload = {
    alert_oncall_enabled: getTriSelect('routingOncallEnabled'),
    alert_sms_enabled: getTriSelect('routingSmsEnabled'),
    alert_slack_enabled: getTriSelect('routingSlackEnabled'),
    alert_discord_enabled: getTriSelect('routingDiscordEnabled'),
    alert_pagerduty_enabled: getTriSelect('routingPagerdutyEnabled'),
    alert_webhook_enabled: getTriSelect('routingWebhookEnabled'),
    alert_oncall_email: valOrNull('routingOncallEmail'),
    alert_sms_to: valOrNull('routingSmsTo'),
    alert_slack_webhook_url: valOrNull('routingSlackWebhook'),
    alert_discord_webhook_url: valOrNull('routingDiscordWebhook'),
    alert_pagerduty_integration_key: valOrNull('routingPagerdutyKey'),
    alert_generic_webhook_url: valOrNull('routingGenericWebhook'),
    escalation_after_minutes: numOrNull('routingEscAfter'),
    escalation_cooldown_seconds: numOrDefault('routingEscCooldown', 3600),
  };
  const res = await fetch(`/projects/${pid}/oncall/checks/${checkId}/routing`, {method:'PATCH', headers: headersOncall(), body: JSON.stringify(payload)});
  if (!res.ok) {
    const msg = await res.text();
    alert(`Save failed: ${msg}`);
    return;
  }
  await loadChecksForEscalations();
  renderRoutingOverrides();
  alert('Saved overrides');
}

function getPolicyChain(checkId){
  if(checkId){
    return oncallEscalations.filter(e => e.check_id === checkId);
  }
  return oncallEscalations.filter(e => e.check_id == null);
}

function groupByLevel(chain){
  const map = {};
  for(const e of chain){
    const lvl = e.level || 0;
    if(!map[lvl]) map[lvl] = [];
    map[lvl].push(e);
  }
  return Object.keys(map).map(k => ({level: parseInt(k), items: map[k]})).sort((a,b)=>a.level-b.level);
}

async function refreshOncall(){
  const perf = window.LastPingShell ? window.LastPingShell.createPerfTracker("On-call") : null;
  const pid = getProjectId();
  const h = headersOncall();
  const escFilter = document.getElementById('escFilterCheckId').value;
  const escUrl = escFilter ? `/projects/${pid}/oncall/escalations?check_id=${encodeURIComponent(escFilter)}` : `/projects/${pid}/oncall/escalations`;
  try{
    const [rots, escs, alerts] = await Promise.all([
      perf && window.LastPingShell
        ? perf.fetchJson('rotations', `/projects/${pid}/oncall/rotations`, {headers: h})
        : fetch(`/projects/${pid}/oncall/rotations`, {headers: h}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
      perf && window.LastPingShell
        ? perf.fetchJson('escalations', escUrl, {headers: h})
        : fetch(escUrl, {headers: h}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
      perf && window.LastPingShell
        ? perf.fetchJson('alerts', `/projects/${pid}/oncall/alerts?status_filter=open`, {headers: h})
        : fetch(`/projects/${pid}/oncall/alerts?status_filter=open`, {headers: h}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
    ]);
    if(!rots.ok || !escs.ok || !alerts.ok){
      const shellData = window.LastPingShell
        ? await window.LastPingShell.hydratePageShell(pid, oncallChecks && oncallChecks.length ? oncallChecks : null, {perf})
        : {checks: oncallChecks || [], health: null};
      renderOncallCards(shellData.checks, shellData.health, []);
      alert('Failed to load on-call data');
      return;
    }
    const rotJson = rots.data || [];
    const escJson = escs.data || [];
    const alertJson = alerts.data || [];
    oncallRotations = rotJson || [];
    oncallEscalations = escJson || [];

    const rotDiv = document.getElementById('rotations');
    const memberDiv = document.getElementById('members');
    memberDiv.innerHTML = '';
    if(perf){
      perf.measureRender('oncall-rotations', ()=>{
        rotDiv.innerHTML = rotJson.map(r => `<div class="card"><div><strong>${r.name}</strong> (id:${r.id}) interval:${r.interval_minutes} enabled:${r.enabled}</div><button class="btn" onclick="deleteRotation(${pid}, ${r.id})">Delete</button></div>`).join('');
      });
    }else{
      rotDiv.innerHTML = rotJson.map(r => `<div class="card"><div><strong>${r.name}</strong> (id:${r.id}) interval:${r.interval_minutes} enabled:${r.enabled}</div><button class="btn" onclick="deleteRotation(${pid}, ${r.id})">Delete</button></div>`).join('');
    }
    for(const r of rotJson){
      const mres = perf && window.LastPingShell
        ? await perf.fetchJson(`rotation-members:${r.id}`, `/projects/${pid}/oncall/rotations/${r.id}/members`, {headers: h})
        : await fetch(`/projects/${pid}/oncall/rotations/${r.id}/members`, {headers: h}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null}));
      if(!mres.ok) continue;
      const members = mres.data || [];
      const html = members.map(m => `<div class="card"><div>Rotation ${r.id} - ${m.name} ${m.email||''} ${m.phone||''} order:${m.order}</div></div>`).join('');
      if(perf) perf.measureRender(`oncall-members:${r.id}`, ()=>{ memberDiv.innerHTML += html; });
      else memberDiv.innerHTML += html;
    }

    const escDiv = document.getElementById('escalations');
    const renderEscalations = ()=> {
      escDiv.innerHTML = escJson.map(e => {
        const scope = e.check_id ? `check:${e.check_id}` : 'project-wide';
        const target = e.target_type === 'rotation' ? `rotation:${e.rotation_id}` : (e.target_value || '');
        const filt = e.event_types ? ` events:${e.event_types}` : '';
        return `<div class="card"><div>${scope} level:${e.level} ${e.target_type} delay:${e.delay_minutes} ${target}${filt} ${e.enabled === false ? '(disabled)' : ''}</div><button class="btn" onclick="deleteEscalation(${pid}, ${e.id})">Delete</button></div>`;
      }).join('');
    };
    if(perf) perf.measureRender('oncall-escalations', renderEscalations);
    else renderEscalations();

    const alertDiv = document.getElementById('alerts');
    const renderAlerts = ()=> {
      alertDiv.innerHTML = alertJson.map(a => `<div class="card"><div>Alert ${a.id} check:${a.check_id} event:${a.event_type} level:${a.escalation_level}</div><div class="muted">${a.message||''}</div><button class="btn" onclick="closeAlert(${pid}, ${a.id})">Close</button></div>`).join('');
    };
    if(perf) perf.measureRender('oncall-alerts', renderAlerts);
    else renderAlerts();

    const shellData = window.LastPingShell
      ? await window.LastPingShell.hydratePageShell(pid, oncallChecks && oncallChecks.length ? oncallChecks : null, {perf})
      : {checks: oncallChecks || [], health: null};
    const render = ()=>{
      renderOncallCards(shellData.checks, shellData.health, alertJson);
      renderPolicyChain();
      renderPolicyPreview();
    };
    if(perf) perf.measureRender('oncall-summary', render);
    else render();
  }finally{
    if(perf) perf.finish();
  }
}

async function loadChecksForEscalations(){
  const pid = getProjectId();
  const h = headersOncall();
  const select = document.getElementById('escCheckSelect');
  const filterSelect = document.getElementById('escFilterCheckSelect');
  const policySelect = document.getElementById('policyCheckSelect');
  if(!select || !filterSelect) return;
  select.innerHTML = '<option value="">(project-wide)</option>';
  filterSelect.innerHTML = '<option value="">(all checks)</option>';
  if(policySelect) policySelect.innerHTML = '<option value="">(project-wide)</option>';
  if(!h["X-API-KEY"]) { renderRoutingOverrides(); return; }
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
    renderRoutingOverrides();
  }catch(e){ }
}

function renderPolicyChain(){
  const container = document.getElementById('policyChain');
  if(!container) return;
  const checkId = getSelectedPolicyCheck();
  const chain = getPolicyChain(checkId);
  const steps = groupByLevel(chain);
  if(!steps.length){
    container.innerHTML = '<div class="muted">No escalation steps for this selection. Use Add Step or apply the project template.</div>';
    return;
  }
  container.innerHTML = steps.map(step => renderPolicyStep(step, checkId)).join('');
  attachDragHandlers();
}

function renderPolicyStep(step, checkId){
  const level = step.level;
  const items = step.items;
  const delay = items[0]?.delay_minutes ?? 0;
  const eventTypes = items[0]?.event_types ?? '';
  const scope = checkId ? `check ${checkId}` : 'project-wide';
  const channels = items.map(e => {
    const target = e.target_type === 'rotation' ? `rotation:${e.rotation_id}` : (e.target_value || '');
    const enabled = e.enabled === false ? 'disabled' : 'enabled';
    const toggleLabel = e.enabled === false ? 'Enable' : 'Disable';
    return `<div class="row" style="margin-bottom:6px">
      <div style="flex:1">${e.target_type} · ${target} · ${enabled}</div>
      <button class="btn btn-secondary" onclick="toggleEscalation(${e.id}, ${e.enabled === false ? 'true' : 'false'})">${toggleLabel}</button>
      <button class="btn btn-secondary" onclick="deleteEscalation(${getProjectId()}, ${e.id})">Delete</button>
    </div>`;
  }).join('');
  return `<div class="card policy-step" draggable="true" data-level="${level}">
    <div class="row">
      <div class="drag-handle" title="Drag to reorder">::</div>
      <div style="flex:1"><strong>Step ${level + 1}</strong> (${scope})</div>
    </div>
    <div class="row">
      <label>Delay (min): <input id="stepDelay-${level}" value="${delay}" style="width:120px"/></label>
      <label>Event filter:
        <select id="stepEvent-${level}" style="width:160px">
          <option value="" ${eventTypes ? '' : 'selected'}>any</option>
          <option value="down" ${eventTypes === 'down' ? 'selected' : ''}>down only</option>
          <option value="degraded" ${eventTypes === 'degraded' ? 'selected' : ''}>degraded only</option>
          <option value="down,degraded" ${eventTypes === 'down,degraded' ? 'selected' : ''}>down+degraded</option>
        </select>
      </label>
      <button class="btn btn-secondary" onclick="saveStep(${level})">Save Step</button>
      <button class="btn btn-secondary" onclick="deleteStep(${level})">Delete Step</button>
    </div>
    <div>${channels}</div>
    <div class="row">
      <select id="stepType-${level}">
        <option value="rotation">rotation</option>
        <option value="email">email</option>
        <option value="sms">sms</option>
      </select>
      <input id="stepRotation-${level}" placeholder="Rotation ID" style="width:140px" />
      <input id="stepTarget-${level}" placeholder="Target (email/phone)" style="width:220px" />
      <button class="btn" onclick="addChannelToStep(${level})">Add Channel</button>
    </div>
  </div>`;
}

function attachDragHandlers(){
  const steps = document.querySelectorAll('.policy-step');
  steps.forEach(step => {
    step.addEventListener('dragstart', (ev)=>{
      policyDragLevel = parseInt(step.dataset.level);
      ev.dataTransfer.effectAllowed = 'move';
    });
    step.addEventListener('dragover', (ev)=>{
      ev.preventDefault();
      ev.dataTransfer.dropEffect = 'move';
    });
    step.addEventListener('drop', (ev)=>{
      ev.preventDefault();
      const targetLevel = parseInt(step.dataset.level);
      if(policyDragLevel === null || policyDragLevel === targetLevel) return;
      reorderSteps(policyDragLevel, targetLevel);
    });
  });
}

async function reorderSteps(fromLevel, toLevel){
  const pid = getProjectId();
  const checkId = getSelectedPolicyCheck();
  const chain = getPolicyChain(checkId);
  const groups = groupByLevel(chain);
  const levels = groups.map(g => g.level);
  const fromIdx = levels.indexOf(fromLevel);
  const toIdx = levels.indexOf(toLevel);
  if(fromIdx < 0 || toIdx < 0) return;
  const [moved] = levels.splice(fromIdx, 1);
  levels.splice(toIdx, 0, moved);
  const levelMap = {};
  levels.forEach((lvl, idx) => { levelMap[lvl] = idx; });
  for(const esc of chain){
    const newLevel = levelMap[esc.level || 0];
    if(newLevel !== (esc.level || 0)){
      await updateEscalation(pid, esc.id, {level: newLevel});
    }
  }
  await refreshOncall();
}

async function addPolicyStep(){
  const pid = getProjectId();
  const checkId = getSelectedPolicyCheck();
  const type = document.getElementById('policyType').value;
  const delay = parseInt(document.getElementById('policyDelay').value || '0') || 0;
  const rotationId = parseInt(document.getElementById('policyRotationId').value || '0') || null;
  const target = document.getElementById('policyTarget').value || null;
  const enabled = document.getElementById('policyEnabled').checked;
  const eventTypes = document.getElementById('policyEventTypes') ? document.getElementById('policyEventTypes').value : null;
  const chain = getPolicyChain(checkId);
  const maxLevel = chain.reduce((m, e) => Math.max(m, e.level || 0), -1);
  const level = maxLevel + 1;
  if(type === 'rotation' && !rotationId){ alert('Rotation ID required'); return; }
  if((type === 'email' || type === 'sms') && !target){ alert('Target required'); return; }
  const payload = {check_id: checkId, level, delay_minutes: delay, target_type: type, rotation_id: rotationId, target_value: target, enabled, event_types: eventTypes || null};
  const res = await fetch(`/projects/${pid}/oncall/escalations`, {method:'POST', headers: headersOncall(), body: JSON.stringify(payload)});
  if(!res.ok){ alert('Failed to add escalation'); return; }
  refreshOncall();
}

async function addChannelToStep(level){
  const pid = getProjectId();
  const checkId = getSelectedPolicyCheck();
  const type = document.getElementById(`stepType-${level}`).value;
  const rotationId = parseInt(document.getElementById(`stepRotation-${level}`).value || '0') || null;
  const target = document.getElementById(`stepTarget-${level}`).value || null;
  const delay = parseInt(document.getElementById(`stepDelay-${level}`).value || '0') || 0;
  const eventTypes = document.getElementById(`stepEvent-${level}`).value || null;
  if(type === 'rotation' && !rotationId){ alert('Rotation ID required'); return; }
  if((type === 'email' || type === 'sms') && !target){ alert('Target required'); return; }
  const payload = {check_id: checkId, level, delay_minutes: delay, target_type: type, rotation_id: rotationId, target_value: target, enabled: true, event_types: eventTypes || null};
  const res = await fetch(`/projects/${pid}/oncall/escalations`, {method:'POST', headers: headersOncall(), body: JSON.stringify(payload)});
  if(!res.ok){ alert('Failed to add channel'); return; }
  refreshOncall();
}

async function saveStep(level){
  const pid = getProjectId();
  const delay = parseInt(document.getElementById(`stepDelay-${level}`).value || '0') || 0;
  const eventTypes = document.getElementById(`stepEvent-${level}`).value || null;
  const checkId = getSelectedPolicyCheck();
  const chain = getPolicyChain(checkId).filter(e => (e.level || 0) === level);
  for(const esc of chain){
    await updateEscalation(pid, esc.id, {delay_minutes: delay, event_types: eventTypes || null});
  }
  refreshOncall();
}

async function deleteStep(level){
  const pid = getProjectId();
  const checkId = getSelectedPolicyCheck();
  const chain = getPolicyChain(checkId).filter(e => (e.level || 0) === level);
  if(!chain.length) return;
  if(!confirm(`Delete step ${level + 1} and all channels?`)) return;
  for(const esc of chain){
    await deleteEscalation(pid, esc.id, false);
  }
  refreshOncall();
}

async function addRotation(){
  const pid = getProjectId();
  const payload = {
    name: document.getElementById('rotName').value,
    interval_minutes: parseInt(document.getElementById('rotInterval').value || '0') || 1440
  };
  const res = await fetch(`/projects/${pid}/oncall/rotations`, {method:'POST', headers: headersOncall(), body: JSON.stringify(payload)});
  if(!res.ok){ alert('Failed to add rotation'); return; }
  refreshOncall();
}

async function addMember(){
  const pid = getProjectId();
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
  const pid = getProjectId();
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

async function deleteEscalation(pid, eid, confirmDelete=true){
  if(confirmDelete && !confirm('Delete escalation '+eid+'?')) return;
  const res = await fetch(`/projects/${pid}/oncall/escalations/${eid}`, {method:'DELETE', headers: headersOncall()});
  if(!res.ok){ alert('Delete failed'); return; }
  if(confirmDelete) refreshOncall();
}

async function closeAlert(pid, aid){
  const res = await fetch(`/projects/${pid}/oncall/alerts/${aid}/close`, {method:'POST', headers: headersOncall()});
  if(!res.ok){ alert('Close failed'); return; }
  refreshOncall();
}

async function updateEscalation(pid, eid, payload){
  const res = await fetch(`/projects/${pid}/oncall/escalations/${eid}`, {method:'PATCH', headers: headersOncall(), body: JSON.stringify(payload)});
  if(!res.ok){
    const msg = await res.text();
    alert(`Update failed: ${msg}`);
    return false;
  }
  return true;
}

async function toggleEscalation(eid, enabled){
  const pid = getProjectId();
  await updateEscalation(pid, eid, {enabled});
  refreshOncall();
}

async function renderPolicyPreview(){
  const preview = document.getElementById('policyPreview');
  if(!preview) return;
  const pid = getProjectId();
  const checkId = getSelectedPolicyCheck();
  const eventType = document.getElementById('policyPreviewEvent')?.value || '';
  const params = new URLSearchParams();
  if(checkId) params.set('check_id', checkId);
  if(eventType) params.set('event_type', eventType);
  const res = await fetch(`/projects/${pid}/oncall/escalations/preview?${params.toString()}`, {headers: headersOncall()});
  if(!res.ok){
    preview.textContent = 'Failed to load preview.';
    return;
  }
  const data = await res.json();
  if(!data.steps || !data.steps.length){
    preview.textContent = 'No steps found for this selection.';
    return;
  }
  const html = data.steps.map(step => {
    const channels = step.channels.map(c => {
      const target = c.target_type === 'rotation' ? `rotation:${c.rotation_id}` : (c.target_value || '');
      return `<div class="muted">${c.target_type} · ${target}</div>`;
    }).join('');
    const filt = step.event_types ? `events:${step.event_types}` : 'events:any';
    return `<div class="card"><div>level ${step.level} · delay ${step.delay_minutes}m · ${filt}</div>${channels}</div>`;
  }).join('');
  preview.innerHTML = html;
}

async function applyProjectTemplate(){
  const pid = getProjectId();
  const checkId = getSelectedPolicyCheck();
  if(!checkId){ alert('Select a check to apply the project template.'); return; }
  const payload = {source_check_id: null, target_check_id: checkId, overwrite: true};
  const res = await fetch(`/projects/${pid}/oncall/escalations/apply-template`, {method:'POST', headers: headersOncall(), body: JSON.stringify(payload)});
  if(!res.ok){ alert('Failed to apply template'); return; }
  refreshOncall();
}

async function saveProjectTemplate(){
  const pid = getProjectId();
  const checkId = getSelectedPolicyCheck();
  if(!checkId){ alert('Select a check to save as the project template.'); return; }
  const payload = {source_check_id: checkId, target_check_id: null, overwrite: true};
  const res = await fetch(`/projects/${pid}/oncall/escalations/apply-template`, {method:'POST', headers: headersOncall(), body: JSON.stringify(payload)});
  if(!res.ok){ alert('Failed to save template'); return; }
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
  const policyPreviewBtn = document.getElementById('policyPreviewBtn');
  if(policyPreviewBtn) policyPreviewBtn.addEventListener('click', renderPolicyPreview);
  const policyPreviewEvent = document.getElementById('policyPreviewEvent');
  if(policyPreviewEvent) policyPreviewEvent.addEventListener('change', renderPolicyPreview);
  const policyApply = document.getElementById('policyApplyTemplateBtn');
  if(policyApply) policyApply.addEventListener('click', applyProjectTemplate);
  const policySave = document.getElementById('policySaveTemplateBtn');
  if(policySave) policySave.addEventListener('click', saveProjectTemplate);
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
    policySelect.addEventListener('change', ()=>{
      renderPolicyChain();
      renderPolicyPreview();
      renderRoutingOverrides();
    });
  }
  const routingSave = document.getElementById('routingSaveBtn');
  if(routingSave) routingSave.addEventListener('click', saveRoutingOverrides);
  const routingReset = document.getElementById('routingResetBtn');
  if(routingReset) routingReset.addEventListener('click', resetRoutingOverrides);
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
  renderRoutingOverrides();
});
