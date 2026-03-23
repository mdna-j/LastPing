function getIncidentPageProjectId(){
  return document.getElementById("projectId").value || "1";
}

function incidentFilterCheckId(){
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("check_id");
  const parsed = raw ? parseInt(raw, 10) : null;
  return Number.isNaN(parsed) ? null : parsed;
}

function escapeHtml(value){
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function readHeaders(){
  const headers = {};
  const apiKey = document.getElementById("apiKey") ? document.getElementById("apiKey").value.trim() : "";
  const userToken = document.getElementById("userToken") ? document.getElementById("userToken").value.trim() : "";
  const adminToken = document.getElementById("adminToken") ? document.getElementById("adminToken").value.trim() : "";
  if(apiKey) headers["X-API-KEY"] = apiKey;
  if(userToken) headers.Authorization = `Bearer ${userToken}`;
  if(adminToken) headers["X-ADMIN-TOKEN"] = adminToken;
  return headers;
}

function manageHeaders(){
  return {...readHeaders(), "Content-Type": "application/json"};
}

function formatTimestamp(value){
  if(!value) return "n/a";
  try{
    return new Date(value).toLocaleString();
  }catch(_e){
    return value;
  }
}

function formatDuration(seconds){
  if(seconds === null || seconds === undefined || Number.isNaN(seconds)) return "n/a";
  const total = Math.max(0, Math.floor(Number(seconds)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if(hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function incidentStateBadges(incident){
  const badges = [];
  const status = (incident.status || "").toLowerCase();
  if(status === "open") badges.push(`<span class="badge status-down">open</span>`);
  else if(status) badges.push(`<span class="badge status-up">${escapeHtml(status)}</span>`);

  if(incident.acknowledged_at){
    badges.push(`<span class="badge status-up">acknowledged</span>`);
  }
  if(incident.silenced_until){
    const silenceUntil = new Date(incident.silenced_until);
    const silenceActive = !Number.isNaN(silenceUntil.getTime()) && silenceUntil.getTime() > Date.now();
    badges.push(`<span class="badge ${silenceActive ? "status-degraded" : "status-up"}">${silenceActive ? "silenced" : "silence expired"}</span>`);
  }
  return badges.join("");
}

function lifecycleSummary(incident){
  const owner = incident.owner ? escapeHtml(incident.owner) : "unassigned";
  const acknowledged = incident.acknowledged_at
    ? `${formatTimestamp(incident.acknowledged_at)} by ${escapeHtml(incident.acknowledged_by || "unknown")}`
    : "not acknowledged";
  const silenced = incident.silenced_until
    ? `${formatTimestamp(incident.silenced_until)} by ${escapeHtml(incident.silenced_by || "unknown")}`
    : "not silenced";
  const notes = `${incident.note_count || 0} notes`;
  return `
    <div class="incident-meta-grid">
      <div><span class="muted">Owner</span><div>${owner}</div></div>
      <div><span class="muted">Acknowledged</span><div>${acknowledged}</div></div>
      <div><span class="muted">Silenced</span><div>${silenced}</div></div>
      <div><span class="muted">Notes</span><div>${notes}</div></div>
    </div>
  `;
}

async function createShare(pid, incidentId){
  const resp = await fetch(`/projects/${pid}/incidents/${incidentId}/share`, {
    method: "POST",
    headers: readHeaders(),
  });
  if(!resp.ok){
    alert("Failed to create share link.");
    return;
  }
  const json = await resp.json();
  alert(`Share token: ${json.share_token}\nPublic URL: ${location.origin}/incidents/public/${json.share_token}`);
}

async function mergePrompt(pid, incidentId){
  const into = prompt(`Merge incident ${incidentId} into (target incident id):`);
  if(!into) return;
  if(!confirm(`Merge incident ${incidentId} into ${into}?`)) return;

  const btn = event && event.target ? event.target : null;
  if(btn) btn.disabled = true;
  try{
    const resp = await fetch(`/projects/${pid}/incidents/${incidentId}/merge`, {
      method: "POST",
      headers: manageHeaders(),
      body: JSON.stringify({into: parseInt(into, 10)}),
    });
    if(!resp.ok){
      alert("Merge failed.");
      return;
    }
    loadIncidents();
  }finally{
    if(btn) btn.disabled = false;
  }
}

async function assignPrompt(pid, incidentId){
  const owner = prompt(`Assign owner for incident ${incidentId} (leave blank to clear):`, "");
  if(owner === null) return;
  const payload = {owner: owner.trim() ? owner.trim() : null};
  const resp = await fetch(`/projects/${pid}/incidents/${incidentId}/assign`, {
    method: "POST",
    headers: manageHeaders(),
    body: JSON.stringify(payload),
  });
  if(!resp.ok){
    alert("Failed to update incident owner.");
    return;
  }
  loadIncidents();
}

async function acknowledgeIncident(pid, incidentId, acknowledged){
  const resp = await fetch(`/projects/${pid}/incidents/${incidentId}/ack`, {
    method: "POST",
    headers: manageHeaders(),
    body: JSON.stringify({acknowledged}),
  });
  if(!resp.ok){
    alert(`Failed to ${acknowledged ? "acknowledge" : "clear acknowledgement"} incident.`);
    return;
  }
  loadIncidents();
}

async function silencePrompt(pid, incidentId){
  const minutesRaw = prompt(`Silence incident ${incidentId} for how many minutes?`, "60");
  if(!minutesRaw) return;
  const minutes = parseInt(minutesRaw, 10);
  if(Number.isNaN(minutes) || minutes <= 0){
    alert("Enter a positive number of minutes.");
    return;
  }
  const until = new Date(Date.now() + minutes * 60 * 1000).toISOString();
  const resp = await fetch(`/projects/${pid}/incidents/${incidentId}/silence`, {
    method: "POST",
    headers: manageHeaders(),
    body: JSON.stringify({until}),
  });
  if(!resp.ok){
    alert("Failed to silence incident.");
    return;
  }
  loadIncidents();
}

async function clearSilence(pid, incidentId){
  const resp = await fetch(`/projects/${pid}/incidents/${incidentId}/silence`, {
    method: "POST",
    headers: manageHeaders(),
    body: JSON.stringify({clear: true}),
  });
  if(!resp.ok){
    alert("Failed to clear silence.");
    return;
  }
  loadIncidents();
}

async function addIncidentNote(pid, incidentId){
  const body = prompt(`Add a note for incident ${incidentId}:`);
  if(body === null || !body.trim()) return;
  const resp = await fetch(`/projects/${pid}/incidents/${incidentId}/notes`, {
    method: "POST",
    headers: manageHeaders(),
    body: JSON.stringify({body: body.trim()}),
  });
  if(!resp.ok){
    alert("Failed to add note.");
    return;
  }
  loadIncidents();
}

function renderIncidentCards(checks, health, incidents){
  const root = document.getElementById("incidentCards");
  if(!root) return;

  const counts = window.LastPingShell
    ? window.LastPingShell.checkCounts(checks || [])
    : {total: 0, up: 0, down: 0, degraded: 0, flapping: 0};
  const openIncidents = (incidents || []).filter((incident)=> (incident.status || "").toLowerCase() === "open").length;
  const mergedIncidents = (incidents || []).filter((incident)=> incident.merged_into).length;
  const activeFromHealth = health && health.active_incidents !== undefined && health.active_incidents !== null
    ? Number(health.active_incidents)
    : openIncidents;
  const acknowledgedCount = (incidents || []).filter((incident)=> !!incident.acknowledged_at).length;

  const checksState = counts.down > 0 ? "kpi-critical" : (counts.degraded > 0 ? "kpi-warning" : "kpi-healthy");
  const openState = activeFromHealth > 0 ? "kpi-critical" : "kpi-healthy";
  const mergedState = mergedIncidents > 0 ? "kpi-warning" : "kpi-neutral";
  const ackState = acknowledgedCount > 0 ? "kpi-healthy" : "kpi-neutral";

  root.innerHTML = [
    `<article class="card kpi-card ${checksState}"><div class="metric-label">Checks</div><div class="metric-value">${counts.total}</div><div class="metric-sub">${counts.up} up | ${counts.down} down | ${counts.degraded} degraded</div></article>`,
    `<article class="card kpi-card ${openState}"><div class="metric-label">Open incidents</div><div class="metric-value">${activeFromHealth}</div><div class="metric-sub">Current unresolved threads</div></article>`,
    `<article class="card kpi-card ${ackState}"><div class="metric-label">Acknowledged</div><div class="metric-value">${acknowledgedCount}</div><div class="metric-sub">Active incidents with an owner response</div></article>`,
    `<article class="card kpi-card ${mergedState}"><div class="metric-label">Merged incidents</div><div class="metric-value">${mergedIncidents}</div><div class="metric-sub">Nested under a primary incident</div></article>`,
  ].join("");
}

function renderMergedIncident(item, checkName){
  return `
    <div class="card incident-child-card">
      <div class="incident-card-head">
        <div>
          <strong>Incident ${item.id}</strong> - ${escapeHtml(checkName || `check ${item.check_id}`)}
          <span class="muted">(${escapeHtml(item.status || "unknown")})</span>
        </div>
        <div>${incidentStateBadges(item)}</div>
      </div>
      <div class="muted">Started ${formatTimestamp(item.started_at)}</div>
      ${lifecycleSummary(item)}
    </div>
  `;
}

function incidentCardHtml(pid, incident, mergedHtml, checkName){
  const resolved = incident.resolved_at ? `Resolved ${formatTimestamp(incident.resolved_at)}` : "Still open";
  const group = incident.group_id ? `Group ${incident.group_id}` : "Standalone";
  const ackLabel = incident.acknowledged_at ? "Clear Ack" : "Acknowledge";
  const silenceLabel = incident.silenced_until ? "Extend Snooze" : "Snooze";
  return `
    <div class="card incident-feed-card">
      <div class="incident-card-head">
        <div>
          <div><strong>Incident ${incident.id}</strong> - ${escapeHtml(checkName || `check ${incident.check_id}`)}</div>
          <div class="muted">Started ${formatTimestamp(incident.started_at)} | ${resolved} | ${group}</div>
        </div>
        <div>${incidentStateBadges(incident)}</div>
      </div>
      ${lifecycleSummary(incident)}
      <div class="row incident-action-row">
        <a class="btn" href="/ui/incidents/${incident.id}?project=${pid}">Details</a>
        <button class="btn btn-secondary" onclick="assignPrompt(${pid}, ${incident.id})">Assign</button>
        <button class="btn btn-secondary" onclick="acknowledgeIncident(${pid}, ${incident.id}, ${incident.acknowledged_at ? "false" : "true"})">${ackLabel}</button>
        <button class="btn btn-secondary" onclick="silencePrompt(${pid}, ${incident.id})">${silenceLabel}</button>
        <button class="btn btn-secondary" onclick="clearSilence(${pid}, ${incident.id})">Clear Silence</button>
        <button class="btn btn-secondary" onclick="addIncidentNote(${pid}, ${incident.id})">Add Note</button>
        <button class="btn btn-secondary" onclick="createShare(${pid}, ${incident.id})">Share</button>
        <button class="btn btn-secondary" onclick="mergePrompt(${pid}, ${incident.id})">Merge</button>
      </div>
      ${mergedHtml}
    </div>
  `;
}

async function loadIncidents(){
  const perf = window.LastPingShell ? window.LastPingShell.createPerfTracker("Incidents") : null;
  const pid = getIncidentPageProjectId();
  const checkFilter = incidentFilterCheckId();
  const listEl = document.getElementById("list");
  if(listEl) listEl.innerText = "Loading...";

  try{
    const [checksRes, incidentsRes] = await Promise.all([
      perf && window.LastPingShell
        ? perf.fetchJson("checks", `/projects/${pid}/checks`, {headers: readHeaders()})
        : fetch(`/projects/${pid}/checks`, {headers: readHeaders()}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
      perf && window.LastPingShell
        ? perf.fetchJson("incidents", `/projects/${pid}/incidents`, {headers: readHeaders()})
        : fetch(`/projects/${pid}/incidents`, {headers: readHeaders()}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
    ]);
    const checks = checksRes.ok ? (checksRes.data || []) : [];
    const checkMap = {};
    for(const check of checks){ checkMap[check.id] = check; }
    const shellPromise = window.LastPingShell
      ? window.LastPingShell.hydratePageShell(pid, checks, {perf})
      : Promise.resolve({checks, health: null});

    if(!incidentsRes.ok){
      if(listEl) listEl.innerText = "Failed to load incidents";
      const shellData = await shellPromise;
      renderIncidentCards(shellData.checks, shellData.health, []);
      return;
    }

    let incidents = incidentsRes.data || [];
    if(checkFilter){
      incidents = incidents.filter((incident)=> incident.check_id === checkFilter);
    }

    const children = {};
    for(const incident of incidents){
      if(incident.merged_into){
        if(!children[incident.merged_into]) children[incident.merged_into] = [];
        children[incident.merged_into].push(incident);
      }
    }
    const topLevel = incidents.filter((incident)=> !incident.merged_into);

    const shellData = await shellPromise;
    const render = ()=>{
      if(listEl){
        if(!incidents.length){
          listEl.innerHTML = `<div class="muted">${checkFilter ? `No incidents for check ${checkFilter}` : "No incidents"}</div>`;
        }else{
          listEl.innerHTML = topLevel.map((incident)=>{
            const merged = children[incident.id] || [];
            const mergedHtml = merged.length
              ? `<div class="incident-child-stack"><strong>Merged incidents</strong>${merged.map((child)=> renderMergedIncident(child, checkMap[child.check_id] ? checkMap[child.check_id].name : "")).join("")}</div>`
              : "";
            const checkName = checkMap[incident.check_id] ? checkMap[incident.check_id].name : "";
            return incidentCardHtml(pid, incident, mergedHtml, checkName);
          }).join("");
        }
      }
      renderIncidentCards(shellData.checks, shellData.health, incidents);
    };
    if(perf) perf.measureRender("incidents-render", render);
    else render();
  }catch(_e){
    if(listEl) listEl.innerText = "Failed to load incidents";
  }finally{
    if(perf) perf.finish();
  }
}

function applyIncidentQueryParams(){
  const params = new URLSearchParams(window.location.search);
  const project = params.get("project");
  if(project && document.getElementById("projectId")){
    document.getElementById("projectId").value = project;
  }
}

window.createShare = createShare;
window.mergePrompt = mergePrompt;
window.assignPrompt = assignPrompt;
window.acknowledgeIncident = acknowledgeIncident;
window.silencePrompt = silencePrompt;
window.clearSilence = clearSilence;
window.addIncidentNote = addIncidentNote;
window.loadIncidents = loadIncidents;

document.addEventListener("DOMContentLoaded", ()=>{
  applyIncidentQueryParams();
  const loadBtn = document.getElementById("loadIncidentsBtn");
  if(loadBtn) loadBtn.addEventListener("click", loadIncidents);
  loadIncidents();
});
