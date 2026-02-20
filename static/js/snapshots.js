function snapshotHeaders(){
  const apiKey = document.getElementById("apiKey").value || null;
  const headers = {};
  if(apiKey) headers.Authorization = `Bearer ${apiKey}`;
  return headers;
}

function toIso(dt){
  return dt.toISOString().slice(0, 19);
}

function isIsoTimestamp(value){
  if(!value) return false;
  return !Number.isNaN(Date.parse(value));
}

function applyPreset(hours){
  const end = new Date();
  const start = new Date(end.getTime() - hours * 3600 * 1000);
  document.getElementById("start").value = toIso(start);
  document.getElementById("end").value = toIso(end);
}

function updateSettingsLink(){
  const pid = document.getElementById("projectId").value || "1";
  const link = document.getElementById("settingsLink");
  if(link) link.href = `/ui/projects/${pid}/settings`;
}

function loadPrefs(){
  try{
    const raw = localStorage.getItem("lastSnapshotsPrefs");
    if(!raw) return;
    const prefs = JSON.parse(raw);
    if(prefs.projectId) document.getElementById("projectId").value = prefs.projectId;
    if(prefs.checkId) document.getElementById("checkId").value = prefs.checkId;
    if(prefs.start) document.getElementById("start").value = prefs.start;
    if(prefs.end) document.getElementById("end").value = prefs.end;
  }catch(_e){
    // ignore local cache failures
  }
}

function savePrefs(){
  try{
    const payload = {
      projectId: document.getElementById("projectId").value || "1",
      checkId: document.getElementById("checkId").value || null,
      start: document.getElementById("start").value || null,
      end: document.getElementById("end").value || null,
    };
    localStorage.setItem("lastSnapshotsPrefs", JSON.stringify(payload));
    alert("Preferences saved");
  }catch(_e){
    alert("Failed to save prefs");
  }
}

async function loadChecks(){
  const pid = document.getElementById("projectId").value || "1";
  const apiKey = document.getElementById("apiKey").value || null;
  const sel = document.getElementById("checkId");
  if(!sel) return;
  sel.innerHTML = '<option value="">(all)</option>';
  if(!apiKey) return;

  try{
    const res = await fetch(`/projects/${pid}/checks`, {headers: {Authorization: `Bearer ${apiKey}`}});
    if(!res.ok) return;
    const checks = await res.json();
    for(const c of checks){
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.text = c.name;
      sel.appendChild(opt);
    }
  }catch(_e){
    // ignore dropdown failures
  }
}

function uptimeKpiState(value){
  if(value === null || value === undefined) return "kpi-neutral";
  if(value < 97) return "kpi-critical";
  if(value < 99.5) return "kpi-warning";
  return "kpi-healthy";
}

function mttrKpiState(value){
  if(value === null || value === undefined) return "kpi-neutral";
  if(value >= 600) return "kpi-critical";
  if(value >= 180) return "kpi-warning";
  return "kpi-healthy";
}

function renderSnapshotCards(checks, health, uptimeJson, mttrJson, snaps){
  const root = document.getElementById("snapshotCards");
  if(!root) return;

  const counts = window.LastPingShell
    ? window.LastPingShell.checkCounts(checks || [])
    : {total: 0, up: 0, down: 0, degraded: 0, flapping: 0};
  const uptimePct = uptimeJson && uptimeJson.uptime !== undefined
    ? uptimeJson.uptime
    : (uptimeJson && uptimeJson.project_uptime_percent !== undefined ? uptimeJson.project_uptime_percent : null);
  const mttr = mttrJson && mttrJson.mttr_seconds !== undefined ? mttrJson.mttr_seconds : null;
  const snapshotCount = (snaps || []).length;
  const openIncidents = health && health.active_incidents !== undefined && health.active_incidents !== null
    ? Number(health.active_incidents)
    : 0;

  const checksState = counts.down > 0
    ? "kpi-critical"
    : (counts.degraded > 0 ? "kpi-warning" : "kpi-healthy");
  const openIncidentState = openIncidents > 0 ? "kpi-critical" : "kpi-healthy";
  const uptimeState = uptimeKpiState(uptimePct);
  const mttrState = mttrKpiState(mttr);

  root.innerHTML = [
    `<article class="card kpi-card ${checksState}"><div class="metric-label">Checks</div><div class="metric-value">${counts.total}</div><div class="metric-sub">${counts.up} up | ${counts.down} down | ${counts.degraded} degraded</div></article>`,
    `<article class="card kpi-card ${uptimeState}"><div class="metric-label">Uptime</div><div class="metric-value">${uptimePct !== null && uptimePct !== undefined ? Number(uptimePct).toFixed(2) + "%" : "n/a"}</div><div class="metric-sub">Selected range</div></article>`,
    `<article class="card kpi-card ${mttrState}"><div class="metric-label">MTTR</div><div class="metric-value">${mttr !== null && mttr !== undefined ? Number(mttr).toFixed(1) + "s" : "n/a"}</div><div class="metric-sub">Selected range</div></article>`,
    `<article class="card kpi-card ${openIncidentState}"><div class="metric-label">Open incidents</div><div class="metric-value">${openIncidents}</div><div class="metric-sub">Health feed</div></article>`,
    `<article class="card kpi-card kpi-neutral"><div class="metric-label">Snapshot rows</div><div class="metric-value">${snapshotCount}</div><div class="metric-sub">Returned in current query</div></article>`,
  ].join("");
}

function renderSnapshotRows(uptimeJson, mttrJson, snaps){
  const out = document.getElementById("list");
  if(!out) return;

  const uptimeValue = uptimeJson && uptimeJson.uptime !== undefined
    ? Number(uptimeJson.uptime).toFixed(2) + "%"
    : "n/a";
  const mttrValue = mttrJson && mttrJson.mttr_seconds !== undefined && mttrJson.mttr_seconds !== null
    ? Number(mttrJson.mttr_seconds).toFixed(1) + "s"
    : "n/a";

  if(!snaps || !snaps.length){
    out.innerHTML = `<div class="muted">No snapshots in range. Uptime ${uptimeValue} | MTTR ${mttrValue}</div>`;
    return;
  }

  const rows = snaps.map((s)=>{
    const mttr = s.mttr_seconds === null || s.mttr_seconds === undefined ? "n/a" : Number(s.mttr_seconds).toFixed(1);
    return `<tr><td>${s.id}</td><td>${s.check_id}</td><td>${s.window_end}</td><td>${Number(s.uptime_percent).toFixed(2)}</td><td>${mttr}</td></tr>`;
  }).join("");

  out.innerHTML = `
    <div class="row"><strong>Uptime:</strong> ${uptimeValue} <strong>MTTR:</strong> ${mttrValue}</div>
    <table>
      <thead><tr><th>ID</th><th>Check</th><th>Window End</th><th>Uptime %</th><th>MTTR (s)</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function renderSnapshotChart(snaps){
  const canvas = document.getElementById("snapshotUptimeChart");
  if(!canvas || typeof Chart === "undefined"){
    if(window.LastPingShell){
      window.LastPingShell.setChartEmpty("snapshotChartEmpty", true, "Chart library not available.");
    }
    return;
  }

  const labels = (snaps || []).map((s)=> s.window_end).reverse();
  const data = (snaps || []).map((s)=> s.uptime_percent).reverse();
  const hasData = labels.length > 0 && data.some((v)=> v !== null && v !== undefined);

  if(window._snapshotUptimeChart){
    try{ window._snapshotUptimeChart.destroy(); }catch(_e){}
    window._snapshotUptimeChart = null;
  }

  const ctx = canvas.getContext("2d");
  window._snapshotUptimeChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "Uptime %",
        data,
        borderColor: "rgba(96, 157, 255, 0.95)",
        backgroundColor: "rgba(71, 131, 229, 0.16)",
        tension: 0.22,
        fill: true,
        pointRadius: 2.5,
      }]
    },
    options: {
      maintainAspectRatio: false,
      scales: {
        x: {
          grid: {color: "rgba(71, 98, 134, 0.34)"},
          ticks: {color: "#9cb6d7", maxTicksLimit: 6},
          title: {display: true, text: "Time", color: "#a9c0df"}
        },
        y: {
          beginAtZero: true,
          suggestedMax: 100,
          grid: {color: "rgba(71, 98, 134, 0.34)"},
          ticks: {color: "#9cb6d7"},
          title: {display: true, text: "Uptime %", color: "#a9c0df"}
        }
      },
      plugins: {
        legend: {display: true, labels: {color: "#d9e8ff"}}
      }
    }
  });

  if(window.LastPingShell){
    window.LastPingShell.setChartEmpty("snapshotChartEmpty", !hasData, "No recent data for selected range.");
  }
}

async function openAvailability(){
  const pid = document.getElementById("projectId").value || "1";
  const {Authorization} = snapshotHeaders();
  const start = document.getElementById("start").value || null;
  const end = document.getElementById("end").value || null;
  const root = document.getElementById("availability");
  if(root) root.innerText = "Loading availability...";

  const params = new URLSearchParams();
  if(start) params.set("start", start);
  if(end) params.set("end", end);
  const headers = {};
  if(Authorization) headers.Authorization = Authorization;
  const res = await fetch(`/projects/${pid}/metrics/availability?${params.toString()}`, {headers});
  if(!res.ok){
    if(root) root.innerText = "Failed to load availability";
    return;
  }

  const data = await res.json();
  const pct = (v)=> (v === null || v === undefined) ? "n/a" : `${Number(v).toFixed(2)}%`;
  let html = "";
  html += `<div><strong>Project uptime:</strong> ${pct(data.project_uptime_percent)}</div>`;
  html += `<div class="muted">Range: ${data.start} -> ${data.end}</div>`;
  html += `<div class="muted">SLO target: ${pct(data.slo_target)} | SLA target: ${pct(data.sla_target)}</div>`;
  if(!data.checks || !data.checks.length){
    html += '<div class="muted" style="margin-top:8px">No checks in this project.</div>';
    if(root) root.innerHTML = html;
    return;
  }

  html += '<table style="margin-top:8px"><thead><tr><th>Check</th><th>Uptime %</th><th>SLO</th><th>SLA</th></tr></thead><tbody>';
  for(const c of data.checks){
    const sloClass = c.slo_met ? "status-up" : "status-down";
    const slaClass = c.sla_met ? "status-up" : "status-down";
    html += `<tr><td>${c.name || c.check_id}</td><td>${pct(c.uptime_percent)}</td><td><span class="badge ${sloClass}">${c.slo_met ? "met" : "missed"}</span></td><td><span class="badge ${slaClass}">${c.sla_met ? "met" : "missed"}</span></td></tr>`;
  }
  html += "</tbody></table>";
  if(root) root.innerHTML = html;
}

async function exportCsv(){
  const pid = document.getElementById("projectId").value || "1";
  const checkId = document.getElementById("checkId").value || null;
  const start = document.getElementById("start").value || null;
  const end = document.getElementById("end").value || null;
  const params = new URLSearchParams();
  if(checkId) params.set("check_id", checkId);
  if(start) params.set("start", start);
  if(end) params.set("end", end);

  const headers = snapshotHeaders();
  const res = await fetch(`/projects/${pid}/metrics/snapshots?${params.toString()}`, {headers});
  if(!res.ok){
    alert("Failed to fetch snapshots for CSV");
    return;
  }
  const data = await res.json();
  if(!data || !data.length){
    alert("No snapshot data");
    return;
  }

  const header = ["id", "project_id", "check_id", "window_start", "window_end", "uptime_percent", "mttr_seconds"];
  const lines = [header.join(",")];
  for(const row of data){
    lines.push([row.id, row.project_id, row.check_id, `"${row.window_start}"`, `"${row.window_end}"`, row.uptime_percent, row.mttr_seconds].join(","));
  }
  const blob = new Blob([lines.join("\n")], {type: "text/csv"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `snapshots_${pid}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function loadSnapshots(){
  const pid = document.getElementById("projectId").value || "1";
  const checkId = document.getElementById("checkId").value || null;
  const start = document.getElementById("start").value || null;
  const end = document.getElementById("end").value || null;
  const out = document.getElementById("list");
  if(out) out.innerText = "Loading...";

  if(window.LastPingShell){
    window.LastPingShell.setChartLoading("snapshotUptimeChartCard", true);
    window.LastPingShell.setChartEmpty("snapshotChartEmpty", false, "");
  }

  try{
    const headers = snapshotHeaders();
    const params = new URLSearchParams();
    if(checkId) params.set("check_id", checkId);
    if(start) params.set("start", start);
    if(end) params.set("end", end);
    const q = (url)=> params.toString() ? `${url}?${params.toString()}` : url;

    const [checksRes, uptimeRes, mttrRes, snapsRes] = await Promise.all([
      fetch(`/projects/${pid}/checks`),
      fetch(q(`/projects/${pid}/metrics/uptime`), {headers}),
      fetch(q(`/projects/${pid}/metrics/mttr`), {headers}),
      fetch(q(`/projects/${pid}/metrics/snapshots`), {headers}),
    ]);

    const checks = checksRes.ok ? await checksRes.json() : [];
    const shellPromise = window.LastPingShell
      ? window.LastPingShell.hydratePageShell(pid, checks)
      : Promise.resolve({checks, health: null});

    if(!uptimeRes.ok || !mttrRes.ok || !snapsRes.ok){
      if(out) out.innerText = "Failed to load metrics";
      const shellData = await shellPromise;
      renderSnapshotCards(shellData.checks, shellData.health, null, null, []);
      return;
    }

    const uptimeJson = await uptimeRes.json();
    const mttrJson = await mttrRes.json();
    const snapsJson = await snapsRes.json();
    const shellData = await shellPromise;

    renderSnapshotCards(shellData.checks, shellData.health, uptimeJson, mttrJson, snapsJson);
    renderSnapshotRows(uptimeJson, mttrJson, snapsJson);
    renderSnapshotChart(snapsJson);

    try{
      localStorage.setItem("lastSnapshotsPrefs", JSON.stringify({projectId: pid, checkId, start, end}));
    }catch(_e){
      // ignore cache failures
    }
  }catch(_e){
    if(out) out.innerText = "Error loading snapshots";
    if(window.LastPingShell){
      window.LastPingShell.setChartEmpty("snapshotChartEmpty", true, "Unable to render chart.");
    }
  }finally{
    if(window.LastPingShell){
      window.LastPingShell.setChartLoading("snapshotUptimeChartCard", false);
    }
  }
}

document.addEventListener("DOMContentLoaded", ()=>{
  loadPrefs();
  if(!document.getElementById("start").value && !document.getElementById("end").value){
    applyPreset(24 * 7);
  }
  updateSettingsLink();

  const pidEl = document.getElementById("projectId");
  const apiEl = document.getElementById("apiKey");
  const loadBtn = document.getElementById("loadSnapshotsBtn");
  const saveBtn = document.getElementById("savePrefsBtn");
  const exportBtn = document.getElementById("exportCsvBtn");
  const availabilityBtn = document.getElementById("availabilityBtn");

  if(pidEl){
    pidEl.addEventListener("change", ()=>{
      updateSettingsLink();
      loadChecks();
    });
  }
  if(apiEl) apiEl.addEventListener("change", loadChecks);
  if(loadBtn){
    loadBtn.addEventListener("click", ()=>{
      const start = document.getElementById("start").value;
      const end = document.getElementById("end").value;
      if((start && !isIsoTimestamp(start)) || (end && !isIsoTimestamp(end))){
        alert("Start/end must be valid ISO timestamps (YYYY-MM-DDTHH:MM:SS)");
        return;
      }
      loadSnapshots();
    });
  }
  if(saveBtn) saveBtn.addEventListener("click", savePrefs);
  if(exportBtn) exportBtn.addEventListener("click", exportCsv);
  if(availabilityBtn) availabilityBtn.addEventListener("click", openAvailability);

  const p1h = document.getElementById("p1h");
  const p6h = document.getElementById("p6h");
  const p24h = document.getElementById("p24h");
  const p7d = document.getElementById("p7d");
  if(p1h) p1h.addEventListener("click", ()=> applyPreset(1));
  if(p6h) p6h.addEventListener("click", ()=> applyPreset(6));
  if(p24h) p24h.addEventListener("click", ()=> applyPreset(24));
  if(p7d) p7d.addEventListener("click", ()=> applyPreset(24 * 7));

  loadChecks();
  loadSnapshots();
});
