function reportHeaders(){
  const apiKey = document.getElementById("apiKey").value || null;
  const headers = {};
  if(apiKey) headers.Authorization = `Bearer ${apiKey}`;
  return headers;
}

function toIso(dt){
  return dt.toISOString().slice(0, 19);
}

function applyPreset(days){
  const end = new Date();
  const start = new Date(end.getTime() - days * 24 * 3600 * 1000);
  document.getElementById("start").value = toIso(start);
  document.getElementById("end").value = toIso(end);
}

function getRange(){
  const start = document.getElementById("start").value || null;
  const end = document.getElementById("end").value || null;
  const checkId = document.getElementById("checkId").value || null;
  const rollupEl = document.getElementById("rollup");
  const rollup = rollupEl ? rollupEl.value : "day";
  return {start, end, checkId, rollup};
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
    // ignore
  }
}

function renderReportCards(checks, health, series){
  const root = document.getElementById("reportCards");
  if(!root) return;

  const counts = window.LastPingShell
    ? window.LastPingShell.checkCounts(checks || [])
    : {total: 0, up: 0, down: 0, degraded: 0, flapping: 0};
  const openIncidents = health && health.active_incidents !== undefined && health.active_incidents !== null
    ? Number(health.active_incidents)
    : 0;
  const rows = series || [];
  const latest = rows.length ? rows[rows.length - 1] : null;
  const latestUptime = latest && latest.uptime_percent !== undefined && latest.uptime_percent !== null
    ? Number(latest.uptime_percent)
    : null;

  const checksState = counts.down > 0 ? "kpi-critical" : (counts.degraded > 0 ? "kpi-warning" : "kpi-healthy");
  const uptimeState = latestUptime === null ? "kpi-neutral" : (latestUptime < 97 ? "kpi-critical" : (latestUptime < 99.5 ? "kpi-warning" : "kpi-healthy"));
  const incidentState = openIncidents > 0 ? "kpi-critical" : "kpi-healthy";

  root.innerHTML = [
    `<article class="card kpi-card ${checksState}"><div class="metric-label">Checks</div><div class="metric-value">${counts.total}</div><div class="metric-sub">${counts.up} up | ${counts.down} down | ${counts.degraded} degraded</div></article>`,
    `<article class="card kpi-card ${uptimeState}"><div class="metric-label">Latest uptime</div><div class="metric-value">${latestUptime !== null ? latestUptime.toFixed(2) + "%" : "n/a"}</div><div class="metric-sub">Most recent ${document.getElementById("rollup").value} period</div></article>`,
    `<article class="card kpi-card ${incidentState}"><div class="metric-label">Open incidents</div><div class="metric-value">${openIncidents}</div><div class="metric-sub">Current unresolved threads</div></article>`,
    `<article class="card kpi-card kpi-neutral"><div class="metric-label">Periods</div><div class="metric-value">${rows.length}</div><div class="metric-sub">Rows in selected range</div></article>`,
  ].join("");
}

function formatBudgetSeconds(seconds){
  if(seconds === null || seconds === undefined) return "n/a";
  const total = Math.max(0, Math.round(Number(seconds)));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if(days > 0) return `${days}d ${hours}h`;
  if(hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function burnState(errorBudget){
  if(!errorBudget) return "kpi-neutral";
  if(errorBudget.alert && errorBudget.alert.triggered) return "kpi-critical";
  const remaining = errorBudget.remaining_percent;
  if(remaining === null || remaining === undefined) return "kpi-neutral";
  if(Number(remaining) <= 25) return "kpi-warning";
  return "kpi-healthy";
}

function renderBurnRateCards(errorBudget){
  const root = document.getElementById("burnRateCards");
  if(!root) return;

  if(!errorBudget){
    root.innerHTML = '<article class="card kpi-card kpi-neutral"><div class="metric-label">Error budget</div><div class="metric-value">n/a</div><div class="metric-sub">Unable to load burn-rate data.</div></article>';
    return;
  }

  const windows = errorBudget.burn_rate_windows || [];
  const shortWindow = windows[0] || null;
  const longWindow = windows[1] || null;
  const overallState = burnState(errorBudget);
  const shortState = shortWindow && shortWindow.burn_rate !== null && shortWindow.burn_rate !== undefined
    ? (Number(shortWindow.burn_rate) >= Number(shortWindow.threshold || 0) ? "kpi-critical" : (Number(shortWindow.burn_rate) >= 1 ? "kpi-warning" : "kpi-healthy"))
    : "kpi-neutral";
  const longState = longWindow && longWindow.burn_rate !== null && longWindow.burn_rate !== undefined
    ? (Number(longWindow.burn_rate) >= Number(longWindow.threshold || 0) ? "kpi-critical" : (Number(longWindow.burn_rate) >= 1 ? "kpi-warning" : "kpi-healthy"))
    : "kpi-neutral";
  const offender = (errorBudget.top_offenders || [])[0] || null;

  root.innerHTML = [
    `<article class="card kpi-card ${overallState}"><div class="metric-label">Budget remaining</div><div class="metric-value">${errorBudget.remaining_percent !== null && errorBudget.remaining_percent !== undefined ? Number(errorBudget.remaining_percent).toFixed(1) + "%" : "n/a"}</div><div class="metric-sub">${formatBudgetSeconds(errorBudget.remaining_seconds)} remaining in selected window</div></article>`,
    `<article class="card kpi-card ${overallState}"><div class="metric-label">Budget consumed</div><div class="metric-value">${errorBudget.consumed_percent !== null && errorBudget.consumed_percent !== undefined ? Number(errorBudget.consumed_percent).toFixed(1) + "%" : "n/a"}</div><div class="metric-sub">SLO target ${(errorBudget.slo_target ?? 0).toFixed ? Number(errorBudget.slo_target).toFixed(2) : errorBudget.slo_target}%</div></article>`,
    `<article class="card kpi-card ${shortState}"><div class="metric-label">${shortWindow ? shortWindow.label : "Short"} burn</div><div class="metric-value">${shortWindow && shortWindow.burn_rate !== null && shortWindow.burn_rate !== undefined ? Number(shortWindow.burn_rate).toFixed(2) + "x" : "n/a"}</div><div class="metric-sub">Threshold ${shortWindow ? shortWindow.threshold : "n/a"}x</div></article>`,
    `<article class="card kpi-card ${longState}"><div class="metric-label">${longWindow ? longWindow.label : "Long"} burn</div><div class="metric-value">${longWindow && longWindow.burn_rate !== null && longWindow.burn_rate !== undefined ? Number(longWindow.burn_rate).toFixed(2) + "x" : "n/a"}</div><div class="metric-sub">${offender ? `Lowest uptime: ${offender.name} (${Number(offender.uptime_percent).toFixed(2)}%)` : "No recent offenders"}</div></article>`,
  ].join("");

  if(errorBudget.alert && errorBudget.alert.triggered){
    root.insertAdjacentHTML(
      "beforeend",
      `<article class="card kpi-card kpi-critical"><div class="metric-label">Alert state</div><div class="metric-value">Triggered</div><div class="metric-sub">${errorBudget.alert.reason}</div></article>`
    );
  }
}

function renderReportTable(series, rollup){
  const tbody = document.querySelector("#reportTable tbody");
  const head = document.querySelector("#reportTable thead th");
  if(!tbody) return;
  const label = rollup === "day" ? "day" : "period";
  if(head) head.innerText = rollup === "day" ? "Date" : "Period";

  if(!series.length){
    tbody.innerHTML = '<tr><td colspan="4" class="muted">No data in range.</td></tr>';
    return;
  }

  tbody.innerHTML = series.map((row)=>{
    const sloClass = row.slo_met ? "status-up" : "status-down";
    const slaClass = row.sla_met ? "status-up" : "status-down";
    const sloText = row.slo_met === null || row.slo_met === undefined ? "n/a" : (row.slo_met ? "met" : "missed");
    const slaText = row.sla_met === null || row.sla_met === undefined ? "n/a" : (row.sla_met ? "met" : "missed");
    return `<tr><td>${row[label]}</td><td>${Number(row.uptime_percent).toFixed(2)}%</td><td><span class="badge ${sloClass}">${sloText}</span></td><td><span class="badge ${slaClass}">${slaText}</span></td></tr>`;
  }).join("");
}

function renderReportChart(series, rollup){
  const canvas = document.getElementById("reportChart");
  if(!canvas || typeof Chart === "undefined"){
    if(window.LastPingShell){
      window.LastPingShell.setChartEmpty("reportChartEmpty", true, "Chart library not available.");
    }
    return;
  }

  const label = rollup === "day" ? "day" : "period";
  const labels = series.map((row)=> row[label]);
  const data = series.map((row)=> row.uptime_percent);
  const hasData = labels.length > 0;

  if(window._reportChart){
    try{ window._reportChart.destroy(); }catch(_e){}
    window._reportChart = null;
  }

  const ctx = canvas.getContext("2d");
  window._reportChart = new Chart(ctx, {
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
          ticks: {color: "#9cb6d7", maxTicksLimit: 7},
          title: {display: true, text: rollup === "day" ? "Day" : "Period", color: "#a9c0df"}
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
    window.LastPingShell.setChartEmpty("reportChartEmpty", !hasData, "No recent data for selected range.");
  }
}

async function loadReport(){
  const perf = window.LastPingShell ? window.LastPingShell.createPerfTracker("Reports") : null;
  const pid = document.getElementById("projectId").value || "1";
  const {start, end, checkId, rollup} = getRange();
  const headers = reportHeaders();

  if(window.LastPingShell){
    window.LastPingShell.setChartLoading("reportChartCard", true);
    window.LastPingShell.setChartEmpty("reportChartEmpty", false, "");
  }

  try{
    const params = new URLSearchParams();
    if(start) params.set("start", start);
    if(end) params.set("end", end);
    if(checkId) params.set("check_id", checkId);

    let url = `/projects/${pid}/metrics/availability/history?${params.toString()}`;
    if(rollup && rollup !== "day"){
      params.set("period", rollup);
      url = `/projects/${pid}/metrics/availability/rollup?${params.toString()}`;
    }

    const [checksRes, reportRes, errorBudgetRes] = await Promise.all([
      perf && window.LastPingShell
        ? perf.fetchJson("checks", `/projects/${pid}/checks`)
        : fetch(`/projects/${pid}/checks`).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
      perf && window.LastPingShell
        ? perf.fetchJson("availability-report", url, {headers})
        : fetch(url, {headers}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
      perf && window.LastPingShell
        ? perf.fetchJson("error-budget", `/projects/${pid}/metrics/error-budget?${params.toString()}`, {headers})
        : fetch(`/projects/${pid}/metrics/error-budget?${params.toString()}`, {headers}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
    ]);
    const checks = checksRes.ok ? (checksRes.data || []) : [];
    const shellPromise = window.LastPingShell
      ? window.LastPingShell.hydratePageShell(pid, checks, {perf})
      : Promise.resolve({checks, health: null});
    const errorBudget = errorBudgetRes.ok ? (errorBudgetRes.data || null) : null;

    if(!reportRes.ok){
      alert("Failed to load report");
      const shellData = await shellPromise;
      renderReportCards(shellData.checks, shellData.health, []);
      renderBurnRateCards(errorBudget);
      renderReportTable([], rollup);
      return;
    }

    const payload = reportRes.data || {};
    const series = payload.series || [];
    const shellData = await shellPromise;
    if(perf){
      perf.measureRender("reports-render", ()=>{
        renderReportCards(shellData.checks, shellData.health, series);
        renderBurnRateCards(errorBudget);
        renderReportTable(series, rollup);
        renderReportChart(series, rollup);
      });
    }else{
      renderReportCards(shellData.checks, shellData.health, series);
      renderBurnRateCards(errorBudget);
      renderReportTable(series, rollup);
      renderReportChart(series, rollup);
    }
  }catch(_e){
    alert("Failed to load report");
    renderBurnRateCards(null);
    renderReportTable([], getRange().rollup);
    if(window.LastPingShell){
      window.LastPingShell.setChartEmpty("reportChartEmpty", true, "Unable to render chart.");
    }
  }finally{
    if(window.LastPingShell){
      window.LastPingShell.setChartLoading("reportChartCard", false);
    }
    if(perf) perf.finish();
  }
}

async function exportCsv(){
  const pid = document.getElementById("projectId").value || "1";
  const {start, end, checkId, rollup} = getRange();
  const headers = reportHeaders();

  const params = new URLSearchParams();
  if(start) params.set("start", start);
  if(end) params.set("end", end);
  if(checkId) params.set("check_id", checkId);

  let url = `/projects/${pid}/metrics/availability/report.csv?${params.toString()}`;
  if(rollup && rollup !== "day"){
    params.set("period", rollup);
    url = `/projects/${pid}/metrics/availability/rollup.csv?${params.toString()}`;
  }

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

document.addEventListener("DOMContentLoaded", ()=>{
  const now = new Date();
  const start = new Date(now.getTime() - 30 * 24 * 3600 * 1000);
  document.getElementById("start").value = toIso(start);
  document.getElementById("end").value = toIso(now);

  document.getElementById("p7d").addEventListener("click", ()=> applyPreset(7));
  document.getElementById("p30d").addEventListener("click", ()=> applyPreset(30));
  document.getElementById("p90d").addEventListener("click", ()=> applyPreset(90));
  document.getElementById("p180d").addEventListener("click", ()=> applyPreset(180));
  document.getElementById("loadBtn").addEventListener("click", loadReport);
  document.getElementById("exportBtn").addEventListener("click", exportCsv);

  const rollup = document.getElementById("rollup");
  if(rollup) rollup.addEventListener("change", loadReport);
  const api = document.getElementById("apiKey");
  if(api) api.addEventListener("change", loadChecks);
  const pid = document.getElementById("projectId");
  if(pid) pid.addEventListener("change", loadChecks);

  loadChecks();
  loadReport();
});
