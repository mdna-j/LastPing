function getIncidentPageProjectId(){
  return document.getElementById("projectId").value || "1";
}

function incidentFilterCheckId(){
  const params = new URLSearchParams(window.location.search);
  const raw = params.get("check_id");
  const parsed = raw ? parseInt(raw, 10) : null;
  return Number.isNaN(parsed) ? null : parsed;
}

function headersManage(){
  const adminToken = document.getElementById("adminToken") ? document.getElementById("adminToken").value : "";
  const userToken = document.getElementById("userToken") ? document.getElementById("userToken").value : "";
  const headers = {"Content-Type": "application/json"};
  if(adminToken) headers["X-ADMIN-TOKEN"] = adminToken;
  if(userToken) headers.Authorization = `Bearer ${userToken}`;
  return headers;
}

async function createShare(pid, incidentId){
  const resp = await fetch(`/projects/${pid}/incidents/${incidentId}/share`, {method: "POST"});
  if(!resp.ok){
    alert("Failed to create share");
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
      headers: headersManage(),
      body: JSON.stringify({into: parseInt(into, 10)}),
    });
    if(!resp.ok){
      alert("Merge failed");
      return;
    }
    alert("Merged");
    loadIncidents();
  }finally{
    if(btn) btn.disabled = false;
  }
}

function renderIncidentCards(checks, health, incidents){
  const root = document.getElementById("incidentCards");
  if(!root) return;

  const counts = window.LastPingShell
    ? window.LastPingShell.checkCounts(checks || [])
    : {total: 0, up: 0, down: 0, degraded: 0, flapping: 0};
  const openIncidents = (incidents || []).filter((i)=> (i.status || "").toLowerCase() === "open").length;
  const mergedIncidents = (incidents || []).filter((i)=> i.merged_into).length;
  const activeFromHealth = health && health.active_incidents !== undefined && health.active_incidents !== null
    ? Number(health.active_incidents)
    : openIncidents;

  const checksState = counts.down > 0 ? "kpi-critical" : (counts.degraded > 0 ? "kpi-warning" : "kpi-healthy");
  const openState = activeFromHealth > 0 ? "kpi-critical" : "kpi-healthy";
  const mergedState = mergedIncidents > 0 ? "kpi-warning" : "kpi-neutral";

  root.innerHTML = [
    `<article class="card kpi-card ${checksState}"><div class="metric-label">Checks</div><div class="metric-value">${counts.total}</div><div class="metric-sub">${counts.up} up | ${counts.down} down | ${counts.degraded} degraded</div></article>`,
    `<article class="card kpi-card ${openState}"><div class="metric-label">Open incidents</div><div class="metric-value">${activeFromHealth}</div><div class="metric-sub">Current unresolved threads</div></article>`,
    `<article class="card kpi-card ${mergedState}"><div class="metric-label">Merged incidents</div><div class="metric-value">${mergedIncidents}</div><div class="metric-sub">Nested under a primary incident</div></article>`,
    `<article class="card kpi-card kpi-neutral"><div class="metric-label">Incident records</div><div class="metric-value">${(incidents || []).length}</div><div class="metric-sub">Returned in current query</div></article>`,
  ].join("");
}

function incidentCardHtml(pid, incident, mergedHtml){
  const resolved = incident.resolved_at ? ` | Resolved: ${incident.resolved_at}` : "";
  const group = incident.group_id ? ` | group:${incident.group_id}` : "";
  return `
    <div class="card">
      <div><strong>Incident ${incident.id}</strong> - check ${incident.check_id} <span class="muted">(${incident.status})</span></div>
      <div class="muted">Started: ${incident.started_at}${resolved}${group}</div>
      <div class="row">
        <a class="btn" href="/ui/incidents/${incident.id}">Details</a>
        <button class="btn" onclick="createShare(${pid}, ${incident.id})">Create Share Link</button>
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
        ? perf.fetchJson("checks", `/projects/${pid}/checks`)
        : fetch(`/projects/${pid}/checks`).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
      perf && window.LastPingShell
        ? perf.fetchJson("incidents", `/projects/${pid}/incidents`)
        : fetch(`/projects/${pid}/incidents`).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
    ]);
    const checks = checksRes.ok ? (checksRes.data || []) : [];
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
      incidents = incidents.filter((i)=> i.check_id === checkFilter);
    }

    const byId = {};
    const children = {};
    for(const item of incidents){
      byId[item.id] = item;
      if(item.merged_into){
        if(!children[item.merged_into]) children[item.merged_into] = [];
        children[item.merged_into].push(item);
      }
    }
    const topLevel = incidents.filter((item)=> !item.merged_into);

    const shellData = await shellPromise;
    const render = ()=>{
      if(listEl){
        if(!incidents.length){
          listEl.innerHTML = `<div class="muted">${checkFilter ? `No incidents for check ${checkFilter}` : "No incidents"}</div>`;
        }else{
          listEl.innerHTML = topLevel.map((item)=>{
            const merged = children[item.id] || [];
            const mergedHtml = merged.length
              ? `<div style="margin-top:8px;padding-left:12px"><strong>Merged:</strong>${merged.map((m)=> `<div class="card" style="margin-top:6px"><div><strong>Incident ${m.id}</strong> - check ${m.check_id} <span class="muted">(${m.status})</span></div><div class="muted">Started: ${m.started_at}</div></div>`).join("")}</div>`
              : "";
            return incidentCardHtml(pid, item, mergedHtml);
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
window.loadIncidents = loadIncidents;

document.addEventListener("DOMContentLoaded", ()=>{
  applyIncidentQueryParams();
  const loadBtn = document.getElementById("loadIncidentsBtn");
  if(loadBtn) loadBtn.addEventListener("click", loadIncidents);
  loadIncidents();
});
