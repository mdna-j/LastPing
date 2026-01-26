function headersSettings(){
  const apiKey = document.getElementById('apiKey').value;
  const admin = document.getElementById('adminToken').value;
  const h = {'Content-Type':'application/json'};
  if(apiKey) h['Authorization'] = 'Bearer ' + apiKey;
  if(admin) h['X-ADMIN-TOKEN'] = admin;
  return h;
}

async function loadSettings(){
  const pid = document.getElementById('projectId').value || '1';
  const h = headersSettings();
  const [sloRes, alertRes] = await Promise.all([
    fetch(`/projects/${pid}/slo`, {headers: h}),
    fetch(`/projects/${pid}/alert-settings`, {headers: h}),
  ]);
  if(!sloRes.ok || !alertRes.ok){ alert('Failed to load settings'); return; }
  const slo = await sloRes.json();
  const alerts = await alertRes.json();
  document.getElementById('sloTarget').value = slo.slo_target ?? '';
  document.getElementById('slaTarget').value = slo.sla_target ?? '';
  document.getElementById('smsEnabled').checked = !!alerts.sms_enabled;
  document.getElementById('smsTo').value = alerts.sms_to ?? '';
  document.getElementById('oncallEnabled').checked = !!alerts.oncall_enabled;
  document.getElementById('oncallEmail').value = alerts.oncall_email ?? '';
}

async function saveSettings(){
  const pid = document.getElementById('projectId').value || '1';
  const h = headersSettings();
  const sloPayload = {
    slo_target: parseFloat(document.getElementById('sloTarget').value || ''),
    sla_target: parseFloat(document.getElementById('slaTarget').value || ''),
  };
  if(Number.isNaN(sloPayload.slo_target)) sloPayload.slo_target = null;
  if(Number.isNaN(sloPayload.sla_target)) sloPayload.sla_target = null;

  const alertPayload = {
    sms_enabled: document.getElementById('smsEnabled').checked,
    sms_to: document.getElementById('smsTo').value || null,
    oncall_enabled: document.getElementById('oncallEnabled').checked,
    oncall_email: document.getElementById('oncallEmail').value || null,
  };

  const [sloRes, alertRes] = await Promise.all([
    fetch(`/projects/${pid}/slo`, {method:'POST', headers: h, body: JSON.stringify(sloPayload)}),
    fetch(`/projects/${pid}/alert-settings`, {method:'POST', headers: h, body: JSON.stringify(alertPayload)}),
  ]);
  if(!sloRes.ok || !alertRes.ok){ alert('Failed to save settings'); return; }
  alert('Saved');
}

document.addEventListener('DOMContentLoaded', ()=>{
  const loadBtn = document.getElementById('loadBtn');
  const saveBtn = document.getElementById('saveBtn');
  if(loadBtn) loadBtn.addEventListener('click', loadSettings);
  if(saveBtn) saveBtn.addEventListener('click', saveSettings);
  loadSettings();
});
