function headersSettings(){
  const apiKey = document.getElementById("apiKey").value || "";
  const admin = document.getElementById("adminToken").value || "";
  const headers = {"Content-Type": "application/json"};
  if(apiKey) headers.Authorization = `Bearer ${apiKey}`;
  if(admin) headers["X-ADMIN-TOKEN"] = admin;
  return headers;
}

function renderSettingsCards(checks, health, slo, alerts){
  const root = document.getElementById("settingsCards");
  if(!root) return;

  const counts = window.LastPingShell
    ? window.LastPingShell.checkCounts(checks || [])
    : {total: 0, up: 0, down: 0, degraded: 0, flapping: 0};
  const openIncidents = health && health.active_incidents !== undefined && health.active_incidents !== null
    ? Number(health.active_incidents)
    : 0;
  const sloTarget = slo && slo.slo_target !== undefined && slo.slo_target !== null ? Number(slo.slo_target) : null;
  const slaTarget = slo && slo.sla_target !== undefined && slo.sla_target !== null ? Number(slo.sla_target) : null;
  const smsEnabled = alerts ? !!alerts.sms_enabled : false;
  const oncallEnabled = alerts ? !!alerts.oncall_enabled : false;

  const checksState = counts.down > 0 ? "kpi-critical" : (counts.degraded > 0 ? "kpi-warning" : "kpi-healthy");
  const incidentState = openIncidents > 0 ? "kpi-critical" : "kpi-healthy";
  const sloState = (sloTarget !== null && sloTarget >= 99.5 && slaTarget !== null && slaTarget >= 99.0) ? "kpi-healthy" : "kpi-warning";
  const routeState = (smsEnabled || oncallEnabled) ? "kpi-healthy" : "kpi-warning";

  root.innerHTML = [
    `<article class="card kpi-card ${checksState}"><div class="metric-label">Checks</div><div class="metric-value">${counts.total}</div><div class="metric-sub">${counts.up} up | ${counts.down} down | ${counts.degraded} degraded</div></article>`,
    `<article class="card kpi-card ${incidentState}"><div class="metric-label">Open incidents</div><div class="metric-value">${openIncidents}</div><div class="metric-sub">Current unresolved threads</div></article>`,
    `<article class="card kpi-card ${sloState}"><div class="metric-label">Targets</div><div class="metric-value">${sloTarget !== null ? sloTarget.toFixed(1) : "n/a"} / ${slaTarget !== null ? slaTarget.toFixed(1) : "n/a"}</div><div class="metric-sub">SLO / SLA percentage goals</div></article>`,
    `<article class="card kpi-card ${routeState}"><div class="metric-label">Default routing</div><div class="metric-value">${oncallEnabled ? "On-call on" : "On-call off"}</div><div class="metric-sub">${smsEnabled ? "SMS enabled" : "SMS disabled"}</div></article>`,
  ].join("");
}

async function loadSettings(){
  const pid = document.getElementById("projectId").value || "1";
  const headers = headersSettings();

  try{
    const [checksRes, sloRes, alertRes] = await Promise.all([
      fetch(`/projects/${pid}/checks`),
      fetch(`/projects/${pid}/slo`, {headers}),
      fetch(`/projects/${pid}/alert-settings`, {headers}),
    ]);
    const checks = checksRes.ok ? await checksRes.json() : [];
    const shellPromise = window.LastPingShell
      ? window.LastPingShell.hydratePageShell(pid, checks)
      : Promise.resolve({checks, health: null});

    if(!sloRes.ok || !alertRes.ok){
      alert("Failed to load settings");
      const shellData = await shellPromise;
      renderSettingsCards(shellData.checks, shellData.health, null, null);
      return;
    }

    const slo = await sloRes.json();
    const alerts = await alertRes.json();
    const shellData = await shellPromise;

    document.getElementById("sloTarget").value = slo.slo_target ?? "";
    document.getElementById("slaTarget").value = slo.sla_target ?? "";
    document.getElementById("smsEnabled").checked = !!alerts.sms_enabled;
    document.getElementById("smsTo").value = alerts.sms_to ?? "";
    document.getElementById("oncallEnabled").checked = !!alerts.oncall_enabled;
    document.getElementById("oncallEmail").value = alerts.oncall_email ?? "";

    renderSettingsCards(shellData.checks, shellData.health, slo, alerts);
  }catch(_e){
    if(window.LastPingShell){
      const shellData = await window.LastPingShell.hydratePageShell(pid, null);
      renderSettingsCards(shellData.checks, shellData.health, null, null);
    }
    alert("Failed to load settings");
  }
}

async function saveSettings(){
  const pid = document.getElementById("projectId").value || "1";
  const headers = headersSettings();

  const sloPayload = {
    slo_target: parseFloat(document.getElementById("sloTarget").value || ""),
    sla_target: parseFloat(document.getElementById("slaTarget").value || ""),
  };
  if(Number.isNaN(sloPayload.slo_target)) sloPayload.slo_target = null;
  if(Number.isNaN(sloPayload.sla_target)) sloPayload.sla_target = null;

  const alertPayload = {
    sms_enabled: document.getElementById("smsEnabled").checked,
    sms_to: document.getElementById("smsTo").value || null,
    oncall_enabled: document.getElementById("oncallEnabled").checked,
    oncall_email: document.getElementById("oncallEmail").value || null,
  };

  const [sloRes, alertRes] = await Promise.all([
    fetch(`/projects/${pid}/slo`, {method: "POST", headers, body: JSON.stringify(sloPayload)}),
    fetch(`/projects/${pid}/alert-settings`, {method: "POST", headers, body: JSON.stringify(alertPayload)}),
  ]);
  if(!sloRes.ok || !alertRes.ok){
    alert("Failed to save settings");
    return;
  }
  alert("Saved");
  loadSettings();
}

document.addEventListener("DOMContentLoaded", ()=>{
  const loadBtn = document.getElementById("loadBtn");
  const saveBtn = document.getElementById("saveBtn");
  const pid = document.getElementById("projectId");

  if(loadBtn) loadBtn.addEventListener("click", loadSettings);
  if(saveBtn) saveBtn.addEventListener("click", saveSettings);
  if(pid) pid.addEventListener("change", loadSettings);

  loadSettings();
});
