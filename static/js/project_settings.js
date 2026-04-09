function headersSettings(){
  const apiKey = document.getElementById("apiKey").value || "";
  const admin = document.getElementById("adminToken").value || "";
  const headers = {"Content-Type": "application/json"};
  if(apiKey) headers["X-API-KEY"] = apiKey;
  if(admin) headers["X-ADMIN-TOKEN"] = admin;
  return headers;
}

let clearJiraApiTokenRequested = false;
let clearPagerdutyIntegrationKeyRequested = false;

function escapeHtml(value){
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatPagerdutySync(settings){
  if(!settings || !settings.latest_sync_action) return "none yet";
  const ts = settings.latest_sync_at ? new Date(settings.latest_sync_at).toLocaleString() : "unknown time";
  return `${settings.latest_sync_action} at ${ts}`;
}

function syncSecretActionState(){
  const jiraStatus = document.getElementById("jiraApiTokenStatus");
  const jiraBtn = document.getElementById("clearJiraApiTokenBtn");
  const jiraInput = document.getElementById("jiraApiToken");
  if(jiraStatus){
    const base = jiraStatus.dataset.configured === "true" ? "configured" : "not set";
    jiraStatus.textContent = clearJiraApiTokenRequested ? "will be cleared on save" : base;
  }
  if(jiraBtn) jiraBtn.textContent = clearJiraApiTokenRequested ? "Keep Token" : "Clear Token";
  if(jiraInput && clearJiraApiTokenRequested) jiraInput.value = "";

  const pdStatus = document.getElementById("pagerdutyIntegrationKeyStatus");
  const pdBtn = document.getElementById("clearPagerdutyIntegrationKeyBtn");
  const pdInput = document.getElementById("pagerdutyIntegrationKey");
  if(pdStatus){
    const base = pdStatus.dataset.configured === "true" ? "configured" : "not set";
    pdStatus.textContent = clearPagerdutyIntegrationKeyRequested ? "will be cleared on save" : base;
  }
  if(pdBtn) pdBtn.textContent = clearPagerdutyIntegrationKeyRequested ? "Keep Key" : "Clear Key";
  if(pdInput && clearPagerdutyIntegrationKeyRequested) pdInput.value = "";
}

function setJiraTokenConfigured(configured){
  const el = document.getElementById("jiraApiTokenStatus");
  if(!el) return;
  el.dataset.configured = configured ? "true" : "false";
  syncSecretActionState();
}

function setPagerdutyKeyConfigured(configured){
  const el = document.getElementById("pagerdutyIntegrationKeyStatus");
  if(!el) return;
  el.dataset.configured = configured ? "true" : "false";
  syncSecretActionState();
}

function formatJiraState(settings){
  if(!settings || !settings.configured) return "not set";
  return `${settings.project_key || "unknown project"} / ${settings.issue_type || "Task"}`;
}

function formatJiraSync(settings){
  if(!settings || !settings.latest_sync_action) return "none yet";
  const ts = settings.latest_sync_at ? new Date(settings.latest_sync_at).toLocaleString() : "unknown time";
  return `${settings.latest_sync_action} at ${ts}`;
}

function renderNotificationFailures(rows){
  const root = document.getElementById("notificationFailures");
  if(!root) return;
  if(!Array.isArray(rows) || !rows.length){
    root.innerHTML = '<div class="muted">No recent failed deliveries.</div>';
    return;
  }
  root.innerHTML = `
    <div class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>When</th>
            <th>Channel</th>
            <th>Event</th>
            <th>Target</th>
            <th>Status</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((row)=> `
            <tr>
              <td>${escapeHtml(new Date(row.created_at).toLocaleString())}</td>
              <td>${escapeHtml(row.channel)}</td>
              <td>${escapeHtml(row.event)}</td>
              <td title="${escapeHtml(row.target || "")}">${escapeHtml(row.target || "n/a")}</td>
              <td>${escapeHtml(row.last_retry_action ? `${row.last_retry_action} @ ${new Date(row.last_retry_at).toLocaleString()}` : row.detail || "failed")}</td>
              <td>${row.retryable ? `<button class="btn btn-secondary notification-retry-btn" data-failure-id="${row.id}">Retry</button>` : '<span class="muted">Not retryable</span>'}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
  root.querySelectorAll(".notification-retry-btn").forEach((btn)=>{
    btn.addEventListener("click", ()=> retryNotificationFailure(btn.getAttribute("data-failure-id")));
  });
}

function renderSettingsCards(checks, health, slo, alerts, pagerduty, jira){
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
  const pdConfigured = pagerduty ? !!pagerduty.integration_key_configured : false;
  const pdInboundReady = pagerduty ? !!pagerduty.inbound_secret_configured : false;
  const jiraConfigured = jira ? !!jira.configured : false;

  const checksState = counts.down > 0 ? "kpi-critical" : (counts.degraded > 0 ? "kpi-warning" : "kpi-healthy");
  const incidentState = openIncidents > 0 ? "kpi-critical" : "kpi-healthy";
  const sloState = (sloTarget !== null && sloTarget >= 99.5 && slaTarget !== null && slaTarget >= 99.0) ? "kpi-healthy" : "kpi-warning";
  const routeState = (smsEnabled || oncallEnabled) ? "kpi-healthy" : "kpi-warning";
  const pdState = (pdConfigured && pdInboundReady) ? "kpi-healthy" : (pdConfigured ? "kpi-warning" : "kpi-neutral");
  const jiraState = jiraConfigured ? "kpi-healthy" : "kpi-neutral";

  root.innerHTML = [
    `<article class="card kpi-card ${checksState}"><div class="metric-label">Checks</div><div class="metric-value">${counts.total}</div><div class="metric-sub">${counts.up} up | ${counts.down} down | ${counts.degraded} degraded</div></article>`,
    `<article class="card kpi-card ${incidentState}"><div class="metric-label">Open incidents</div><div class="metric-value">${openIncidents}</div><div class="metric-sub">Current unresolved threads</div></article>`,
    `<article class="card kpi-card ${sloState}"><div class="metric-label">Targets</div><div class="metric-value">${sloTarget !== null ? sloTarget.toFixed(1) : "n/a"} / ${slaTarget !== null ? slaTarget.toFixed(1) : "n/a"}</div><div class="metric-sub">SLO / SLA percentage goals</div></article>`,
    `<article class="card kpi-card ${routeState}"><div class="metric-label">Default routing</div><div class="metric-value">${oncallEnabled ? "On-call on" : "On-call off"}</div><div class="metric-sub">${smsEnabled ? "SMS enabled" : "SMS disabled"}</div></article>`,
    `<article class="card kpi-card ${pdState}"><div class="metric-label">PagerDuty</div><div class="metric-value">${pdConfigured ? "configured" : "not set"}</div><div class="metric-sub">${pdInboundReady ? "inbound sync ready" : "webhook secret missing"}</div></article>`,
    `<article class="card kpi-card ${jiraState}"><div class="metric-label">Jira</div><div class="metric-value">${jiraConfigured ? "configured" : "not set"}</div><div class="metric-sub">${formatJiraState(jira)}</div></article>`,
  ].join("");
}

async function loadSettings(){
  const perf = window.LastPingShell ? window.LastPingShell.createPerfTracker("Settings") : null;
  const pid = document.getElementById("projectId").value || "1";
  const headers = headersSettings();

  try{
    const [checksRes, sloRes, alertRes, jiraRes, pagerdutyRes, failureRes] = await Promise.all([
      perf && window.LastPingShell
        ? perf.fetchJson("checks", `/projects/${pid}/checks`)
        : fetch(`/projects/${pid}/checks`).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
      perf && window.LastPingShell
        ? perf.fetchJson("slo", `/projects/${pid}/slo`, {headers})
        : fetch(`/projects/${pid}/slo`, {headers}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
      perf && window.LastPingShell
        ? perf.fetchJson("alert-settings", `/projects/${pid}/alert-settings`, {headers})
        : fetch(`/projects/${pid}/alert-settings`, {headers}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
      perf && window.LastPingShell
        ? perf.fetchJson("jira-settings", `/projects/${pid}/jira-settings`, {headers})
        : fetch(`/projects/${pid}/jira-settings`, {headers}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
      perf && window.LastPingShell
        ? perf.fetchJson("pagerduty-settings", `/projects/${pid}/pagerduty-settings`, {headers})
        : fetch(`/projects/${pid}/pagerduty-settings`, {headers}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
      perf && window.LastPingShell
        ? perf.fetchJson("notification-failures", `/projects/${pid}/notification-failures`, {headers})
        : fetch(`/projects/${pid}/notification-failures`, {headers}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
    ]);
    const checks = checksRes.ok ? (checksRes.data || []) : [];
    const shellPromise = window.LastPingShell
      ? window.LastPingShell.hydratePageShell(pid, checks, {perf})
      : Promise.resolve({checks, health: null});

    if(!sloRes.ok || !alertRes.ok || !jiraRes.ok || !pagerdutyRes.ok || !failureRes.ok){
      alert("Failed to load settings");
      const shellData = await shellPromise;
      renderSettingsCards(shellData.checks, shellData.health, null, null, null, null);
      renderNotificationFailures([]);
      return;
    }

    const slo = sloRes.data || {};
    const alerts = alertRes.data || {};
    const jira = jiraRes.data || {};
    const pagerduty = pagerdutyRes.data || {};
    const failures = failureRes.data || [];
    const shellData = await shellPromise;

    const render = ()=>{
      document.getElementById("sloTarget").value = slo.slo_target ?? "";
      document.getElementById("slaTarget").value = slo.sla_target ?? "";
      document.getElementById("smsEnabled").checked = !!alerts.sms_enabled;
      document.getElementById("smsTo").value = alerts.sms_to ?? "";
      document.getElementById("oncallEnabled").checked = !!alerts.oncall_enabled;
      document.getElementById("oncallEmail").value = alerts.oncall_email ?? "";
      const jiraBaseUrl = document.getElementById("jiraBaseUrl");
      const jiraUserEmail = document.getElementById("jiraUserEmail");
      const jiraApiToken = document.getElementById("jiraApiToken");
      const jiraProjectKey = document.getElementById("jiraProjectKey");
      const jiraIssueType = document.getElementById("jiraIssueType");
      const jiraSettingsHint = document.getElementById("jiraSettingsHint");
      const jiraWebhookUrl = document.getElementById("jiraWebhookUrl");
      const jiraSecretHeader = document.getElementById("jiraSecretHeader");
      const jiraSecretStatus = document.getElementById("jiraSecretStatus");
      const jiraLastSync = document.getElementById("jiraLastSync");
      if(jiraBaseUrl) jiraBaseUrl.value = jira.base_url || "";
      if(jiraUserEmail) jiraUserEmail.value = jira.user_email || "";
      if(jiraApiToken) jiraApiToken.value = "";
      if(jiraProjectKey) jiraProjectKey.value = jira.project_key || "";
      if(jiraIssueType) jiraIssueType.value = jira.issue_type || "Task";
      if(jiraWebhookUrl) jiraWebhookUrl.textContent = jira.inbound_webhook_url || "/integrations/jira/webhook";
      if(jiraSecretHeader) jiraSecretHeader.textContent = jira.inbound_secret_header || "X-Jira-Webhook-Secret";
      if(jiraSecretStatus) jiraSecretStatus.textContent = jira.inbound_secret_configured ? "configured" : "missing";
      if(jiraLastSync) jiraLastSync.textContent = formatJiraSync(jira);
      clearJiraApiTokenRequested = false;
      setJiraTokenConfigured(!!jira.api_token_configured);
      if(jiraSettingsHint) jiraSettingsHint.textContent = jira.configured
        ? `Jira ready for ${jira.project_key || "project"} ticket creation.`
        : "Create Jira issues directly from incident detail pages once project credentials are configured.";

      const pdKey = document.getElementById("pagerdutyIntegrationKey");
      const pdUrl = document.getElementById("pagerdutyWebhookUrl");
      const pdHeader = document.getElementById("pagerdutySecretHeader");
      const pdSecret = document.getElementById("pagerdutySecretStatus");
      const pdLastSync = document.getElementById("pagerdutyLastSync");
      const pdTestResult = document.getElementById("pagerdutyTestResult");
      if(pdKey) pdKey.value = "";
      if(pdUrl) pdUrl.textContent = pagerduty.inbound_webhook_url || "/integrations/pagerduty/webhook";
      if(pdHeader) pdHeader.textContent = pagerduty.inbound_secret_header || "X-PagerDuty-Webhook-Secret";
      if(pdSecret) pdSecret.textContent = pagerduty.inbound_secret_configured ? "configured" : "missing";
      if(pdLastSync) pdLastSync.textContent = formatPagerdutySync(pagerduty);
      if(pdTestResult) pdTestResult.textContent = "Use test delivery to send a trigger and immediate resolve event to PagerDuty.";
      clearPagerdutyIntegrationKeyRequested = false;
      setPagerdutyKeyConfigured(!!pagerduty.integration_key_configured);

      renderSettingsCards(shellData.checks, shellData.health, slo, alerts, pagerduty, jira);
      renderNotificationFailures(failures);
    };
    if(perf) perf.measureRender("settings-render", render);
    else render();
  }catch(_e){
    if(window.LastPingShell){
      const shellData = await window.LastPingShell.hydratePageShell(pid, null, {perf});
      renderSettingsCards(shellData.checks, shellData.health, null, null, null, null);
    }
    renderNotificationFailures([]);
    alert("Failed to load settings");
  }finally{
    if(perf) perf.finish();
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
  const jiraPayload = {
    base_url: document.getElementById("jiraBaseUrl").value || null,
    user_email: document.getElementById("jiraUserEmail").value || null,
    project_key: document.getElementById("jiraProjectKey").value || null,
    issue_type: document.getElementById("jiraIssueType").value || null,
    clear_api_token: clearJiraApiTokenRequested,
  };
  const jiraTokenValue = document.getElementById("jiraApiToken").value || "";
  if(jiraTokenValue) jiraPayload.api_token = jiraTokenValue;
  const pagerdutyPayload = {
    clear_integration_key: clearPagerdutyIntegrationKeyRequested,
  };
  const pagerdutyKeyValue = document.getElementById("pagerdutyIntegrationKey").value || "";
  if(pagerdutyKeyValue) pagerdutyPayload.integration_key = pagerdutyKeyValue;

  const [sloRes, alertRes, jiraRes, pagerdutyRes] = await Promise.all([
    fetch(`/projects/${pid}/slo`, {method: "POST", headers, body: JSON.stringify(sloPayload)}),
    fetch(`/projects/${pid}/alert-settings`, {method: "POST", headers, body: JSON.stringify(alertPayload)}),
    fetch(`/projects/${pid}/jira-settings`, {method: "POST", headers, body: JSON.stringify(jiraPayload)}),
    fetch(`/projects/${pid}/pagerduty-settings`, {method: "POST", headers, body: JSON.stringify(pagerdutyPayload)}),
  ]);
  if(!sloRes.ok || !alertRes.ok || !jiraRes.ok || !pagerdutyRes.ok){
    alert("Failed to save settings");
    return;
  }
  alert("Saved");
  loadSettings();
}

async function sendPagerdutyTest(){
  const pid = document.getElementById("projectId").value || "1";
  const headers = headersSettings();
  const resultEl = document.getElementById("pagerdutyTestResult");
  if(resultEl) resultEl.textContent = "Sending PagerDuty test delivery...";
  try{
    const res = await fetch(`/projects/${pid}/pagerduty-test`, {method: "POST", headers});
    const body = await res.json().catch(()=> ({}));
    if(!res.ok){
      const detail = body && body.detail ? body.detail : "Failed to send PagerDuty test";
      if(resultEl) resultEl.textContent = detail;
      alert(detail);
      return;
    }
    if(resultEl){
      resultEl.textContent = `${body.message} Dedup key: ${body.dedup_key}`;
    }
    alert("PagerDuty test delivery sent");
    loadSettings();
  }catch(_e){
    if(resultEl) resultEl.textContent = "Failed to send PagerDuty test";
    alert("Failed to send PagerDuty test");
  }
}

async function retryNotificationFailure(failureId){
  const pid = document.getElementById("projectId").value || "1";
  const headers = headersSettings();
  const root = document.getElementById("notificationFailures");
  try{
    const res = await fetch(`/projects/${pid}/notification-failures/${failureId}/retry`, {method: "POST", headers});
    const body = await res.json().catch(()=> ({}));
    if(!res.ok){
      const detail = body && body.detail ? body.detail : "Retry failed";
      alert(detail);
      return;
    }
    if(root){
      const msg = document.createElement("div");
      msg.className = "muted";
      msg.textContent = `${body.message}${body.target ? ` (${body.target})` : ""}`;
      root.prepend(msg);
    }
    await loadSettings();
  }catch(_e){
    alert("Retry failed");
  }
}

document.addEventListener("DOMContentLoaded", ()=>{
  const loadBtn = document.getElementById("loadBtn");
  const saveBtn = document.getElementById("saveBtn");
  const testBtn = document.getElementById("sendPagerdutyTestBtn");
  const clearJiraBtn = document.getElementById("clearJiraApiTokenBtn");
  const clearPdBtn = document.getElementById("clearPagerdutyIntegrationKeyBtn");
  const pid = document.getElementById("projectId");

  if(loadBtn) loadBtn.addEventListener("click", loadSettings);
  if(saveBtn) saveBtn.addEventListener("click", saveSettings);
  if(testBtn) testBtn.addEventListener("click", sendPagerdutyTest);
  if(clearJiraBtn) clearJiraBtn.addEventListener("click", ()=>{
    clearJiraApiTokenRequested = !clearJiraApiTokenRequested;
    syncSecretActionState();
  });
  if(clearPdBtn) clearPdBtn.addEventListener("click", ()=>{
    clearPagerdutyIntegrationKeyRequested = !clearPagerdutyIntegrationKeyRequested;
    syncSecretActionState();
  });
  if(pid) pid.addEventListener("change", loadSettings);

  loadSettings();
});
