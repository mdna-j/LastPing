// /ui/dashboard client script
const DashboardShell = window.LastPingShell;

function dashHeaders(){
  const apiKey = document.getElementById("apiKey").value || null;
  const headers = {};
  if(apiKey) headers["Authorization"] = `Bearer ${apiKey}`;
  return headers;
}

function toIso(dt){
  return dt.toISOString().slice(0, 19);
}

function getRange(){
  const start = document.getElementById("start").value || null;
  const end = document.getElementById("end").value || null;
  return {start, end};
}

function applyDefaultRange(){
  const startEl = document.getElementById("start");
  const endEl = document.getElementById("end");
  if(!startEl.value && !endEl.value){
    const end = new Date();
    const start = new Date(end.getTime() - 24 * 7 * 3600 * 1000);
    startEl.value = toIso(start);
    endEl.value = toIso(end);
  }
}

function statusBadge(status){
  if(!status) return "";
  const up = status.toLowerCase() === "up";
  const down = status.toLowerCase() === "down";
  const degraded = status.toLowerCase() === "degraded";
  const cls = up ? "status-up" : (down ? "status-down" : (degraded ? "status-degraded" : ""));
  return `<span class="badge ${cls}">${status}</span>`;
}

function containsFlappingSignal(obj){
  if(!obj) return false;
  try{
    const raw = JSON.stringify(obj).toLowerCase();
    return raw.includes("flap");
  }catch(_e){
    return false;
  }
}

function countFlappingSignals(predictive, anomalies){
  const seen = new Set();
  const collections = [];
  if(predictive && Array.isArray(predictive.warnings)) collections.push(predictive.warnings);
  if(anomalies && Array.isArray(anomalies.warnings)) collections.push(anomalies.warnings);
  for(const arr of collections){
    for(const item of arr){
      if(!containsFlappingSignal(item)) continue;
      if(item && item.check_id !== undefined && item.check_id !== null){
        seen.add(String(item.check_id));
      }else{
        seen.add(`signal-${seen.size + 1}`);
      }
    }
  }
  return seen.size;
}

function uptimeKpiState(uptimePct){
  if(uptimePct === null || uptimePct === undefined) return "kpi-neutral";
  if(uptimePct < 97) return "kpi-critical";
  if(uptimePct < 99.5) return "kpi-warning";
  return "kpi-healthy";
}

function mttrKpiState(mttrVal){
  if(mttrVal === null || mttrVal === undefined) return "kpi-neutral";
  if(mttrVal >= 600) return "kpi-critical";
  if(mttrVal >= 180) return "kpi-warning";
  return "kpi-healthy";
}

function setChartLoading(isLoading){
  ["uptimeChartCard", "trendChartCard"].forEach((id)=>{
    const card = document.getElementById(id);
    if(card) card.classList.toggle("is-loading", !!isLoading);
  });
}

function setChartEmpty(id, shouldShow, message){
  DashboardShell.setChartEmpty(id, shouldShow, message);
}

function measureRender(perf, label, fn){
  return perf ? perf.measureRender(label, fn) : fn();
}

function goToCheckIncidents(pid, checkId){
  if(!checkId) return;
  const url = new URL("/ui/incidents", window.location.origin);
  url.searchParams.set("project", pid);
  url.searchParams.set("check_id", checkId);
  window.location.href = `${url.pathname}${url.search}`;
}

function wireChecksTableAffordance(pid){
  const rows = document.querySelectorAll("#checksTable tbody tr[data-check-id]");
  rows.forEach((row)=>{
    const checkId = row.getAttribute("data-check-id");
    if(!checkId) return;
    row.setAttribute("role", "link");
    row.setAttribute("tabindex", "0");
    row.setAttribute("title", "Open incidents for this check");
    row.addEventListener("click", ()=>{
      goToCheckIncidents(pid, checkId);
    });
    row.addEventListener("keydown", (e)=>{
      if(e.key === "Enter" || e.key === " "){
        e.preventDefault();
        goToCheckIncidents(pid, checkId);
      }
    });
  });
}

function renderIntelligenceSummary({hasApiKey, predictive, anomalies, checks}){
  const summaryEl = document.getElementById("intelligenceSummary");
  const metaEl = document.getElementById("intelligenceMeta");
  if(!summaryEl) return;

  const downCount = (checks || []).filter((c)=> (c.status || "").toLowerCase() === "down").length;
  const degradedCount = (checks || []).filter((c)=> (c.status || "").toLowerCase() === "degraded").length;
  const flappingCount = countFlappingSignals(predictive, anomalies);
  const predictiveCount = predictive && Array.isArray(predictive.warnings) ? predictive.warnings.length : 0;
  const anomalyCount = anomalies && Array.isArray(anomalies.warnings) ? anomalies.warnings.length : 0;

  const pills = [];
  pills.push(`<span class="intel-pill intel-pill-critical">${downCount} down</span>`);
  if(degradedCount > 0) pills.push(`<span class="intel-pill intel-pill-warning">${degradedCount} degraded</span>`);
  if(flappingCount > 0) pills.push(`<span class="intel-pill intel-pill-flapping">${flappingCount} flapping</span>`);
  pills.push(`<span class="intel-pill">${predictiveCount} predictive</span>`);
  pills.push(`<span class="intel-pill">${anomalyCount} anomalies</span>`);

  if(metaEl){
    metaEl.textContent = hasApiKey ? "Signals from recent windows" : "Add API key for full signal context";
  }

  const footnote = hasApiKey
    ? "Intelligence updates on each dashboard load."
    : "Set API key to unlock predictive and anomaly detail lists.";
  summaryEl.innerHTML = `<div class="intel-pill-row">${pills.join("")}</div><div class="muted">${footnote}</div>`;
}

async function loadHealthStrip(pid, checks, perf){
  DashboardShell.setHealthValue("healthLastRefresh", DashboardShell.localTime(new Date().toISOString()));
  DashboardShell.setHealthValue("healthActiveIncidents", "...");
  DashboardShell.setHealthValue("healthWorkersOnline", "...");
  DashboardShell.setHealthValue("healthRegionHealth", DashboardShell.regionSummaryFromChecks(checks));

  try{
    let data = null;
    if(perf && window.LastPingShell){
      const res = await perf.fetchJson("health", `/ui/dashboard/health?project_id=${encodeURIComponent(pid)}`);
      if(!res.ok) throw new Error(`health ${res.status}`);
      data = res.data;
    }else{
      const res = await fetch(`/ui/dashboard/health?project_id=${encodeURIComponent(pid)}`);
      if(!res.ok) throw new Error(`health ${res.status}`);
      data = await res.json();
    }
    DashboardShell.setHealthValue("healthLastRefresh", DashboardShell.localTime(data.last_refresh));
    DashboardShell.setHealthValue("healthActiveIncidents", String(data.active_incidents ?? "n/a"));
    DashboardShell.setHealthValue("healthWorkersOnline", String(data.workers_online ?? "n/a"));
    DashboardShell.setHealthValue("healthRegionHealth", data.region_health_summary || DashboardShell.regionSummaryFromChecks(checks));
    return data;
  }catch(_e){
    DashboardShell.setHealthValue("healthActiveIncidents", "n/a");
    DashboardShell.setHealthValue("healthWorkersOnline", "n/a");
    return null;
  }
}

async function exportAvailabilityCsv(){
  const pid = document.getElementById("projectId").value || "1";
  const headers = dashHeaders();
  if(!headers.Authorization){
    alert("API key required to export CSV");
    return;
  }
  const {start, end} = getRange();
  const params = new URLSearchParams();
  if(start) params.set("start", start);
  if(end) params.set("end", end);
  const url = `/projects/${pid}/metrics/availability/report.csv?${params.toString()}`;
  const res = await fetch(url, {headers});
  if(!res.ok){
    alert("Failed to export CSV");
    return;
  }
  const text = await res.text();
  const blob = new Blob([text], {type: "text/csv"});
  const dl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = dl;
  a.download = `availability_${pid}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(dl);
}

async function loadDashboard(){
  const perf = window.LastPingShell ? window.LastPingShell.createPerfTracker("Dashboard") : null;
  const pid = document.getElementById("projectId").value || "1";
  const headers = dashHeaders();
  const {start, end} = getRange();
  const params = new URLSearchParams();
  if(start) params.set("start", start);
  if(end) params.set("end", end);
  const q = (url)=> params.toString() ? `${url}?${params.toString()}` : url;

  // load checks (public)
  let checks = [];
  if(perf && window.LastPingShell){
    const checksRes = await perf.fetchJson("checks", `/projects/${pid}/checks`);
    checks = checksRes.ok ? (checksRes.data || []) : [];
  }else{
    const checksRes = await fetch(`/projects/${pid}/checks`);
    checks = checksRes.ok ? await checksRes.json() : [];
  }
  const total = checks.length;
  const upCount = checks.filter((c)=> (c.status || "").toLowerCase() === "up").length;
  const downCount = checks.filter((c)=> (c.status || "").toLowerCase() === "down").length;
  const degradedCount = checks.filter((c)=> (c.status || "").toLowerCase() === "degraded").length;
  let openIncidentCardState = downCount > 0 ? "kpi-critical" : (degradedCount > 0 ? "kpi-warning" : "kpi-neutral");
  setChartLoading(true);
  setChartEmpty("uptimeChartEmpty", false, "");
  setChartEmpty("trendChartEmpty", false, "");

  const healthData = await loadHealthStrip(pid, checks, perf);
  DashboardShell.renderHeroBanner(healthData, checks);

  // metrics (requires API key)
  let uptime = null;
  let mttr = null;
  let snaps = [];
  let trends = null;
  let predictive = null;
  let anomalies = null;
  try{
    const jsonFetch = (label, url, opts)=> {
      if(perf && window.LastPingShell) return perf.fetchJson(label, url, opts);
      return fetch(url, opts).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null}));
    };
    const reqs = [
      jsonFetch("uptime", q(`/projects/${pid}/metrics/uptime`), {headers}),
      jsonFetch("mttr", q(`/projects/${pid}/metrics/mttr`), {headers}),
      jsonFetch("snapshots", `/projects/${pid}/metrics/snapshots?limit=30`, {headers}),
      jsonFetch("trends", `/projects/${pid}/analytics/trends?days=7&interval=day`, {headers}),
    ];
    if(headers.Authorization){
      reqs.push(jsonFetch("predictive", `/projects/${pid}/analytics/predictive?recent_hours=24`, {headers}));
      reqs.push(jsonFetch("anomalies", `/projects/${pid}/analytics/anomalies?recent_hours=24`, {headers}));
    }
    const resps = await Promise.all(reqs);
    const uptimeRes = resps[0];
    const mttrRes = resps[1];
    const snapsRes = resps[2];
    const trendRes = resps[3];
    const predRes = resps[4];
    const anomRes = resps[5];
    if(uptimeRes && uptimeRes.ok) uptime = uptimeRes.data;
    if(mttrRes && mttrRes.ok) mttr = mttrRes.data;
    if(snapsRes && snapsRes.ok) snaps = snapsRes.data || [];
    if(trendRes && trendRes.ok) trends = trendRes.data;
    if(predRes && predRes.ok) predictive = predRes.data;
    if(anomRes && anomRes.ok) anomalies = anomRes.data;
  }catch(_e){
    // ignore partial metric failures in UI
  }

  // incidents (requires API key)
  const incidentsEl = document.getElementById("incidentsList");
  const incCountEl = document.getElementById("openIncidentsCount");
  if(headers.Authorization){
    const incRes = perf && window.LastPingShell
      ? await perf.fetchJson("incidents", `/projects/${pid}/incidents?status=open`, {headers})
      : await fetch(`/projects/${pid}/incidents?status=open`, {headers}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null}));
    if(incRes.ok){
      const incs = incRes.data || [];
      if(incCountEl) incCountEl.innerText = String(incs.length);
      DashboardShell.setHealthValue("healthActiveIncidents", String(incs.length));
      openIncidentCardState = incs.length > 0 ? "kpi-critical" : "kpi-healthy";
      if(!incs.length){
        incidentsEl.innerHTML = '<div class="muted">No open incidents.</div>';
      }else{
        const rows = incs
          .slice(0, 6)
          .map((i)=> `<div class="insight-item"><div><strong>#${i.id}</strong> check ${i.check_id} - ${i.status}</div><div class="muted">Started: ${i.started_at}</div></div>`)
          .join("");
        incidentsEl.innerHTML = rows;
      }
    }else{
      incidentsEl.innerText = "Failed to load incidents (check API key).";
      if(incCountEl) incCountEl.innerText = "n/a";
      openIncidentCardState = "kpi-warning";
    }
  }else{
    incidentsEl.innerText = "Provide API key to load incidents.";
    if(incCountEl) incCountEl.innerText = "locked";
    openIncidentCardState = "kpi-neutral";
  }

  const predEl = document.getElementById("predictiveList");
  if(predEl){
    if(!headers.Authorization){
      predEl.innerText = "Provide API key to load predictive alerts.";
    }else if(predictive && predictive.warnings){
      const modelNote = predictive.model_used ? `<div class="muted">Model: ${predictive.model_type || "trained"} (${predictive.model_count || 0})</div>` : "";
      if(!predictive.warnings.length){
        predEl.innerHTML = modelNote + '<div class="muted">No predictive alerts in the recent window.</div>';
      }else{
        predEl.innerHTML = modelNote + predictive.warnings.map((w)=> {
          const next = (w.predicted_next_hour !== null && w.predicted_next_hour !== undefined) ? Number(w.predicted_next_hour).toFixed(2) : "n/a";
          const slope = (w.trend_slope_per_hour !== null && w.trend_slope_per_hour !== undefined) ? Number(w.trend_slope_per_hour).toFixed(3) : "n/a";
          const ratio = (w.ratio !== null && w.ratio !== undefined) ? Number(w.ratio).toFixed(2) : null;
          const z = (w.zscore !== null && w.zscore !== undefined) ? Number(w.zscore).toFixed(2) : null;
          const detail = z ? `z ${z} | slope ${slope}` : `ratio ${ratio || "n/a"} | slope ${slope}`;
          return `<div class="insight-item"><div><strong>Check ${w.check_id}</strong> | forecast ${next} events</div><div class="muted">${detail}</div></div>`;
        }).join("");
      }
    }else{
      predEl.innerText = "Failed to load predictive alerts (check API key).";
    }
  }

  const anomEl = document.getElementById("anomalyList");
  if(anomEl){
    if(!headers.Authorization){
      anomEl.innerText = "Provide API key to load anomaly warnings.";
    }else if(anomalies && anomalies.warnings){
      if(!anomalies.warnings.length){
        anomEl.innerHTML = '<div class="muted">No anomaly warnings in the recent window.</div>';
      }else{
        anomEl.innerHTML = anomalies.warnings.map((w)=> {
          const score = (w.anomaly_score !== null && w.anomaly_score !== undefined) ? Number(w.anomaly_score).toFixed(2) : "n/a";
          const next = (w.predicted_next_hour !== null && w.predicted_next_hour !== undefined) ? Number(w.predicted_next_hour).toFixed(2) : "n/a";
          return `<div class="insight-item"><div><strong>Check ${w.check_id}</strong> | anomaly ${score}</div><div class="muted">predicted ${next} | slope ${w.trend_slope_per_hour}</div></div>`;
        }).join("");
      }
    }else{
      anomEl.innerText = "Failed to load anomaly warnings (check API key).";
    }
  }
  renderIntelligenceSummary({
    hasApiKey: !!headers.Authorization,
    predictive,
    anomalies,
    checks
  });

  // cards
  const cards = document.getElementById("cards");
  const uptimePct = uptime && uptime.uptime !== undefined ? uptime.uptime : (uptime && uptime.project_uptime_percent !== undefined ? uptime.project_uptime_percent : null);
  const mttrVal = mttr && mttr.mttr_seconds !== undefined ? mttr.mttr_seconds : null;
  const flappingCount = countFlappingSignals(predictive, anomalies);
  const renderDashboardDom = ()=>{
    const checksStateClass = downCount > 0
      ? "kpi-critical"
      : (flappingCount > 0 ? "kpi-flapping" : (degradedCount > 0 ? "kpi-warning" : "kpi-healthy"));
    const uptimeStateClass = uptimeKpiState(uptimePct);
    const mttrStateClass = mttrKpiState(mttrVal);
    const checksSub = `${upCount} up | ${downCount} down | ${degradedCount} degraded${flappingCount > 0 ? ` | ${flappingCount} flapping` : ""}`;
    let html = "";
    html += `<article id="checksKpiCard" class="card kpi-card ${checksStateClass}"><div class="metric-label">Checks</div><div class="metric-value">${total}</div><div class="metric-sub">${checksSub}</div></article>`;
    html += `<article id="uptimeKpiCard" class="card kpi-card ${uptimeStateClass}"><div class="metric-label">Uptime</div><div class="metric-value">${uptimePct !== null ? uptimePct.toFixed(2) + "%" : "n/a"}</div><div class="metric-sub">Selected range</div></article>`;
    html += `<article id="mttrKpiCard" class="card kpi-card ${mttrStateClass}"><div class="metric-label">MTTR</div><div class="metric-value">${mttrVal !== null ? mttrVal.toFixed(1) + "s" : "n/a"}</div><div class="metric-sub">Selected range</div></article>`;
    html += `<article id="openIncidentsCard" class="card kpi-card ${openIncidentCardState}"><div class="metric-label">Open incidents</div><div class="metric-value" id="openIncidentsCount">${headers.Authorization ? "..." : "locked"}</div><div class="metric-sub">API key required</div></article>`;
    html += '<article id="availabilityKpiCard" class="card kpi-card kpi-neutral"><div class="metric-label">Availability CSV</div><div><button id="exportAvailabilityCsvBtn" class="btn btn-secondary">Export</button></div><div class="metric-sub">Uses selected range</div></article>';
    cards.innerHTML = html;
    DashboardShell.setKpiState("openIncidentsCard", openIncidentCardState);
    const exportBtn = document.getElementById("exportAvailabilityCsvBtn");
    if(exportBtn) exportBtn.onclick = exportAvailabilityCsv;

    const tbody = document.querySelector("#checksTable tbody");
    if(!checks.length){
      tbody.innerHTML = '<tr><td colspan="6" class="muted">No checks found.</td></tr>';
    }else{
      tbody.innerHTML = checks.map((c)=> {
        const lat = (c.last_latency_ms !== null && c.last_latency_ms !== undefined) ? `${Number(c.last_latency_ms).toFixed(1)}ms` : "n/a";
        return `<tr class="checks-row-clickable" data-check-id="${c.id}"><td>${c.name}</td><td>${c.type}</td><td>${statusBadge(c.status)}</td><td>${c.last_ping || "n/a"}</td><td>${lat}</td><td>${c.region || ""}</td></tr>`;
      }).join("");
      wireChecksTableAffordance(pid);
    }
  };
  measureRender(perf, "dashboard-dom", renderDashboardDom);

  // charts
  try{
    const chartGridColor = "rgba(71, 98, 134, 0.34)";
    const chartTickColor = "#9cb6d7";
    const chartTitleColor = "#a9c0df";

    if(typeof Chart !== "undefined"){
      if(window._uptimeChart){ try{ window._uptimeChart.destroy(); }catch(_e){} }
      if(window._trendChart){ try{ window._trendChart.destroy(); }catch(_e){} }

      measureRender(perf, "dashboard-charts", ()=> {
        const uptimeLabels = (snaps || []).map((s)=> s.window_end).reverse();
        const uptimeData = (snaps || []).map((s)=> s.uptime_percent).reverse();
        const hasUptimeData = uptimeLabels.length > 0 && uptimeData.some((v)=> v !== null && v !== undefined);
        const uctx = document.getElementById("uptimeChart").getContext("2d");
        window._uptimeChart = new Chart(uctx, {
          type: "line",
          data: { labels: uptimeLabels, datasets: [{ label: "Uptime %", data: uptimeData, borderColor: "rgba(96, 157, 255, 0.95)", backgroundColor: "rgba(71, 131, 229, 0.16)", tension: 0.22, fill: true, pointRadius: 2.5 }] },
          options: {
            maintainAspectRatio: false,
            scales: {
              x: {
                grid: {color: chartGridColor},
                ticks: {color: chartTickColor, maxTicksLimit: 6},
                title: {display: true, text: "Time", color: chartTitleColor}
              },
              y: {
                beginAtZero: true,
                suggestedMax: 100,
                grid: {color: chartGridColor},
                ticks: {color: chartTickColor},
                title: {display: true, text: "Uptime %", color: chartTitleColor}
              }
            },
            plugins: { legend: { display: true, labels: {color: "#d9e8ff"} } }
          }
        });
        setChartEmpty("uptimeChartEmpty", !hasUptimeData, "No recent data for selected range.");

        const tlabels = (trends && trends.series) ? trends.series.map((s)=> s.bucket_start) : [];
        const tdata = (trends && trends.series) ? trends.series.map((s)=> s.down_events) : [];
        const hasTrendData = tlabels.length > 0;
        const tctx = document.getElementById("trendChart").getContext("2d");
        window._trendChart = new Chart(tctx, {
          type: "bar",
          data: { labels: tlabels, datasets: [{ label: "Down events", data: tdata, backgroundColor: "rgba(232, 108, 132, 0.72)", borderRadius: 6, maxBarThickness: 24 }] },
          options: {
            maintainAspectRatio: false,
            scales: {
              x: {
                grid: {color: chartGridColor},
                ticks: {color: chartTickColor, maxTicksLimit: 7},
                title: {display: true, text: "Day", color: chartTitleColor}
              },
              y: {
                beginAtZero: true,
                grid: {color: chartGridColor},
                ticks: {color: chartTickColor},
                title: {display: true, text: "Down events", color: chartTitleColor}
              }
            },
            plugins: { legend: { display: true, labels: {color: "#d9e8ff"} } }
          }
        });
        setChartEmpty("trendChartEmpty", !hasTrendData, "No recent data for selected range.");
      });
    }else{
      setChartEmpty("uptimeChartEmpty", true, "Chart library not available.");
      setChartEmpty("trendChartEmpty", true, "Chart library not available.");
    }
  }catch(_e){
    setChartEmpty("uptimeChartEmpty", true, "Unable to render chart.");
    setChartEmpty("trendChartEmpty", true, "Unable to render chart.");
  }finally{
    setChartLoading(false);
    if(perf) perf.finish();
  }
}

document.addEventListener("DOMContentLoaded", ()=>{
  applyDefaultRange();
  const btn = document.getElementById("loadDashboardBtn");
  if(btn) btn.addEventListener("click", loadDashboard);
  loadDashboard();
});
