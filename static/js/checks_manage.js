// Client-side script for /ui/checks/{id}
function getCheckId() {
  const el = document.getElementById('checkIdHolder');
  return el ? el.dataset.checkId : null;
}

function headersManage() {
  const at = document.getElementById('adminToken').value;
  const utEl = document.getElementById('userToken');
  const ut = utEl ? utEl.value : null;
  const h = {'Content-Type': 'application/json'};
  if (at) h['X-ADMIN-TOKEN'] = at;
  if (ut) h['Authorization'] = 'Bearer ' + ut;
  return h;
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
  const raw = el.value;
  if (raw === '' || raw === null || raw === undefined) return null;
  const n = parseInt(raw, 10);
  return isNaN(n) ? null : n;
}

async function loadManage() {
  const CHECK_ID = getCheckId();
  const pid = document.getElementById('projectId').value || '1';
  let isOwner = false;
  try {
    const r = await fetch(`/users/projects/${pid}/role`, {headers: headersManage()});
    if (r.ok) {
      const jr = await r.json();
      isOwner = jr.role === 'owner';
    }
  } catch (e) {}

  const resp = await fetch(`/projects/${pid}/checks/${CHECK_ID}`, {headers: headersManage()});
  if (!resp.ok) {
    document.getElementById('status').innerText = 'Failed';
    return;
  }
  const js = await resp.json();
  document.getElementById('name').value = js.name;
  document.getElementById('url').value = js.url || '';
  document.getElementById('host').value = js.host || '';
  document.getElementById('port').value = js.port || '';
  document.getElementById('dnsRecordType').value = js.dns_record_type || '';
  document.getElementById('interval').value = js.interval || '';
  document.getElementById('expectedInterval').value = js.expected_interval || '';
  document.getElementById('gracePeriod').value = js.grace_period || '';
  document.getElementById('latencyThreshold').value = js.latency_threshold_ms || '';
  document.getElementById('region').value = js.region || '';
  document.getElementById('alertEnabled').checked = js.alert_enabled !== false;
  document.getElementById('alertAfter').value = js.alert_after || '';
  document.getElementById('alertCooldown').value = js.alert_cooldown || '';

  setTriSelect('alertSmsEnabled', js.alert_sms_enabled);
  setTriSelect('alertOncallEnabled', js.alert_oncall_enabled);
  setTriSelect('alertSlackEnabled', js.alert_slack_enabled);
  setTriSelect('alertDiscordEnabled', js.alert_discord_enabled);
  setTriSelect('alertPagerdutyEnabled', js.alert_pagerduty_enabled);
  setTriSelect('alertWebhookEnabled', js.alert_webhook_enabled);

  document.getElementById('alertSmsTo').value = js.alert_sms_to || '';
  document.getElementById('alertOncallEmail').value = js.alert_oncall_email || '';
  document.getElementById('alertSlackWebhook').value = js.alert_slack_webhook_url || '';
  document.getElementById('alertDiscordWebhook').value = js.alert_discord_webhook_url || '';
  document.getElementById('alertPagerdutyKey').value = js.alert_pagerduty_integration_key || '';
  document.getElementById('alertGenericWebhook').value = js.alert_generic_webhook_url || '';

  if (isOwner || document.getElementById('adminToken').value) {
    document.getElementById('delBtn').style.display = 'inline-block';
  }
}

async function updateManage() {
  const CHECK_ID = getCheckId();
  const pid = document.getElementById('projectId').value || '1';
  const body = {
    name: document.getElementById('name').value,
    url: document.getElementById('url').value,
    host: valOrNull('host'),
    port: numOrNull('port'),
    dns_record_type: valOrNull('dnsRecordType'),
    interval: numOrNull('interval'),
    expected_interval: numOrNull('expectedInterval'),
    grace_period: numOrNull('gracePeriod'),
    latency_threshold_ms: numOrNull('latencyThreshold'),
    region: valOrNull('region'),
    alert_enabled: !!document.getElementById('alertEnabled').checked,
    alert_after: numOrNull('alertAfter'),
    alert_cooldown: numOrNull('alertCooldown'),
    alert_sms_enabled: getTriSelect('alertSmsEnabled'),
    alert_oncall_enabled: getTriSelect('alertOncallEnabled'),
    alert_slack_enabled: getTriSelect('alertSlackEnabled'),
    alert_discord_enabled: getTriSelect('alertDiscordEnabled'),
    alert_pagerduty_enabled: getTriSelect('alertPagerdutyEnabled'),
    alert_webhook_enabled: getTriSelect('alertWebhookEnabled'),
    alert_sms_to: valOrNull('alertSmsTo'),
    alert_oncall_email: valOrNull('alertOncallEmail'),
    alert_slack_webhook_url: valOrNull('alertSlackWebhook'),
    alert_discord_webhook_url: valOrNull('alertDiscordWebhook'),
    alert_pagerduty_integration_key: valOrNull('alertPagerdutyKey'),
    alert_generic_webhook_url: valOrNull('alertGenericWebhook'),
  };
  const resp = await fetch(`/projects/${pid}/checks/${CHECK_ID}`, {method: 'PUT', headers: headersManage(), body: JSON.stringify(body)});
  if (resp.ok) {
    alert('Saved');
  } else {
    alert('Save failed');
  }
}

async function delCheck() {
  const CHECK_ID = getCheckId();
  if (!confirm('Delete check ' + CHECK_ID + '?')) return;
  const pid = document.getElementById('projectId').value || '1';
  const resp = await fetch(`/projects/${pid}/checks/${CHECK_ID}`, {method: 'DELETE', headers: headersManage()});
  if (resp.ok) {
    alert('Deleted');
    location.href = '/ui/checks';
  } else {
    alert('Delete failed');
  }
}

async function setM() {
  const CHECK_ID = getCheckId();
  const pid = document.getElementById('projectId').value || '1';
  const body = {
    maintenance_starts_at: document.getElementById('mstart').value || null,
    maintenance_ends_at: document.getElementById('mend').value || null,
  };
  const resp = await fetch(`/projects/${pid}/checks/${CHECK_ID}/maintenance`, {method: 'POST', headers: headersManage(), body: JSON.stringify(body)});
  if (resp.ok) {
    alert('Set');
  } else {
    alert('Failed');
  }
}

window.updateManage = updateManage;
window.delCheck = delCheck;
window.setM = setM;
window.loadManage = loadManage;

document.addEventListener('DOMContentLoaded', () => {
  loadManage();
  const saveBtn = document.getElementById('saveBtn');
  if (saveBtn) saveBtn.addEventListener('click', updateManage);
  const delBtn = document.getElementById('delBtn');
  if (delBtn) delBtn.addEventListener('click', delCheck);
  const mBtn = document.getElementById('setMBtn');
  if (mBtn) mBtn.addEventListener('click', setM);
});
