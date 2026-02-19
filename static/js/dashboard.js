// /ui/dashboard client script
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

function setHealthValue(id, value){
  const el = document.getElementById(id);
  if(el) el.textContent = value;
}

function localTime(value){
  if(!value) return "n/a";
  const dt = new Date(value);
  if(Number.isNaN(dt.getTime())) return "n/a";
  return dt.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
}

function regionSummaryFromChecks(checks){
  if(!checks || !checks.length) return "No checks";
  const buckets = {};
  for(const c of checks){
    const raw = (c.region || "").trim();
    let region = "global";
    if(raw){
      const lowered = raw.toLowerCase();
      if(lowered === "*" || lowered === "all" || lowered === "any") region = "any";
      else if(raw.includes(",") || raw.includes(" ")) region = "multi";
      else region = raw;
    }
    if(!buckets[region]) buckets[region] = {down: 0, degraded: 0, total: 0};
    buckets[region].total += 1;
    const st = (c.status || "").toLowerCase();
    if(st === "down") buckets[region].down += 1;
    else if(st === "degraded") buckets[region].degraded += 1;
  }
  const names = Object.keys(buckets).sort();
  return names.map((name)=>{
    const b = buckets[name];
    if(b.down > 0) return `${name}: ${b.down} down`;
    if(b.degraded > 0) return `${name}: ${b.degraded} degraded`;
    return `${name}: healthy`;
  }).join(" | ");
}

async function loadHealthStrip(pid, checks){
  setHealthValue("healthLastRefresh", localTime(new Date().toISOString()));
  setHealthValue("healthActiveIncidents", "...");
  setHealthValue("healthWorkersOnline", "...");
  setHealthValue("healthRegionHealth", regionSummaryFromChecks(checks));

  try{
    const res = await fetch(`/ui/dashboard/health?project_id=${encodeURIComponent(pid)}`);
    if(!res.ok) throw new Error(`health ${res.status}`);
    const data = await res.json();
    setHealthValue("healthLastRefresh", localTime(data.last_refresh));
    setHealthValue("healthActiveIncidents", String(data.active_incidents ?? "n/a"));
    setHealthValue("healthWorkersOnline", String(data.workers_online ?? "n/a"));
    setHealthValue("healthRegionHealth", data.region_health_summary || regionSummaryFromChecks(checks));
  }catch(_e){
    setHealthValue("healthActiveIncidents", "n/a");
    setHealthValue("healthWorkersOnline", "n/a");
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
  const pid = document.getElementById("projectId").value || "1";
  const headers = dashHeaders();
  const {start, end} = getRange();
  const params = new URLSearchParams();
  if(start) params.set("start", start);
  if(end) params.set("end", end);
  const q = (url)=> params.toString() ? `${url}?${params.toString()}` : url;

  // load checks (public)
  const checksRes = await fetch(`/projects/${pid}/checks`);
  const checks = checksRes.ok ? await checksRes.json() : [];
  const total = checks.length;
  const upCount = checks.filter((c)=> (c.status || "").toLowerCase() === "up").length;
  const downCount = checks.filter((c)=> (c.status || "").toLowerCase() === "down").length;
  const degradedCount = checks.filter((c)=> (c.status || "").toLowerCase() === "degraded").length;

  await loadHealthStrip(pid, checks);

  // metrics (requires API key)
  let uptime = null;
  let mttr = null;
  let snaps = [];
  let trends = null;
  let predictive = null;
  let anomalies = null;
  try{
    const reqs = [
      fetch(q(`/projects/${pid}/metrics/uptime`), {headers}),
      fetch(q(`/projects/${pid}/metrics/mttr`), {headers}),
      fetch(`/projects/${pid}/metrics/snapshots?limit=30`, {headers}),
      fetch(`/projects/${pid}/analytics/trends?days=7&interval=day`, {headers}),
    ];
    if(headers.Authorization){
      reqs.push(fetch(`/projects/${pid}/analytics/predictive?recent_hours=24`, {headers}));
      reqs.push(fetch(`/projects/${pid}/analytics/anomalies?recent_hours=24`, {headers}));
    }
    const resps = await Promise.all(reqs);
    const uptimeRes = resps[0];
    const mttrRes = resps[1];
    const snapsRes = resps[2];
    const trendRes = resps[3];
    const predRes = resps[4];
    const anomRes = resps[5];
    if(uptimeRes && uptimeRes.ok) uptime = await uptimeRes.json();
    if(mttrRes && mttrRes.ok) mttr = await mttrRes.json();
    if(snapsRes && snapsRes.ok) snaps = await snapsRes.json();
    if(trendRes && trendRes.ok) trends = await trendRes.json();
    if(predRes && predRes.ok) predictive = await predRes.json();
    if(anomRes && anomRes.ok) anomalies = await anomRes.json();
  }catch(_e){
    // ignore partial metric failures in UI
  }

  // incidents (requires API key)
  const incidentsEl = document.getElementById("incidentsList");
  const incCountEl = document.getElementById("openIncidentsCount");
  if(headers.Authorization){
    const incRes = await fetch(`/projects/${pid}/incidents?status=open`, {headers});
    if(incRes.ok){
      const incs = await incRes.json();
      if(incCountEl) incCountEl.innerText = String(incs.length);
      setHealthValue("healthActiveIncidents", String(incs.length));
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
    }
  }else{
    incidentsEl.innerText = "Provide API key to load incidents.";
    if(incCountEl) incCountEl.innerText = "locked";
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

  // cards
  const cards = document.getElementById("cards");
  const uptimePct = uptime && uptime.uptime !== undefined ? uptime.uptime : (uptime && uptime.project_uptime_percent !== undefined ? uptime.project_uptime_percent : null);
  const mttrVal = mttr && mttr.mttr_seconds !== undefined ? mttr.mttr_seconds : null;
  let html = "";
  html += `<article class="card kpi-card"><div class="metric-label">Checks</div><div class="metric-value">${total}</div><div class="metric-sub">${upCount} up | ${downCount} down | ${degradedCount} degraded</div></article>`;
  html += `<article class="card kpi-card"><div class="metric-label">Uptime</div><div class="metric-value">${uptimePct !== null ? uptimePct.toFixed(2) + "%" : "n/a"}</div><div class="metric-sub">Selected range</div></article>`;
  html += `<article class="card kpi-card"><div class="metric-label">MTTR</div><div class="metric-value">${mttrVal !== null ? mttrVal.toFixed(1) + "s" : "n/a"}</div><div class="metric-sub">Selected range</div></article>`;
  html += `<article class="card kpi-card"><div class="metric-label">Open incidents</div><div class="metric-value" id="openIncidentsCount">${headers.Authorization ? "..." : "locked"}</div><div class="metric-sub">API key required</div></article>`;
  html += '<article class="card kpi-card"><div class="metric-label">Availability CSV</div><div><button id="exportAvailabilityCsvBtn" class="btn btn-secondary">Export</button></div><div class="metric-sub">Uses selected range</div></article>';
  cards.innerHTML = html;
  const exportBtn = document.getElementById("exportAvailabilityCsvBtn");
  if(exportBtn) exportBtn.onclick = exportAvailabilityCsv;

  // checks table
  const tbody = document.querySelector("#checksTable tbody");
  if(!checks.length){
    tbody.innerHTML = '<tr><td colspan="6" class="muted">No checks found.</td></tr>';
  }else{
    tbody.innerHTML = checks.map((c)=> {
      const lat = (c.last_latency_ms !== null && c.last_latency_ms !== undefined) ? `${Number(c.last_latency_ms).toFixed(1)}ms` : "n/a";
      return `<tr><td>${c.name}</td><td>${c.type}</td><td>${statusBadge(c.status)}</td><td>${c.last_ping || "n/a"}</td><td>${lat}</td><td>${c.region || ""}</td></tr>`;
    }).join("");
  }

  // charts
  try{
    if(typeof Chart !== "undefined"){
      if(window._uptimeChart){ try{ window._uptimeChart.destroy(); }catch(_e){} }
      if(window._trendChart){ try{ window._trendChart.destroy(); }catch(_e){} }

      const uptimeLabels = (snaps || []).map((s)=> s.window_end).reverse();
      const uptimeData = (snaps || []).map((s)=> s.uptime_percent).reverse();
      const uctx = document.getElementById("uptimeChart").getContext("2d");
      window._uptimeChart = new Chart(uctx, {
        type: "line",
        data: { labels: uptimeLabels, datasets: [{ label: "Uptime %", data: uptimeData, borderColor: "rgba(51,122,183,1)", backgroundColor: "rgba(51,122,183,0.1)", tension: 0.2, fill: true }] },
        options: { scales: { y: { beginAtZero: true, suggestedMax: 100 } }, plugins: { legend: { display: true } } }
      });

      const tlabels = (trends && trends.series) ? trends.series.map((s)=> s.bucket_start) : [];
      const tdata = (trends && trends.series) ? trends.series.map((s)=> s.down_events) : [];
      const tctx = document.getElementById("trendChart").getContext("2d");
      window._trendChart = new Chart(tctx, {
        type: "bar",
        data: { labels: tlabels, datasets: [{ label: "Down events", data: tdata, backgroundColor: "rgba(217,83,79,0.6)" }] },
        options: { plugins: { legend: { display: true } } }
      });
    }
  }catch(_e){
    // ignore chart errors in UI
  }
}

document.addEventListener("DOMContentLoaded", ()=>{
  applyDefaultRange();
  const btn = document.getElementById("loadDashboardBtn");
  if(btn) btn.addEventListener("click", loadDashboard);
  loadDashboard();
});
