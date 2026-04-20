function sloHeaders(){
  const apiKey = document.getElementById("apiKey").value || null;
  const headers = {};
  if(apiKey) headers["X-API-KEY"] = apiKey;
  return headers;
}

function sloToIso(dt){
  return dt.toISOString().slice(0, 19);
}

function sloApplyPreset(days){
  const end = new Date();
  const start = new Date(end.getTime() - days * 24 * 3600 * 1000);
  document.getElementById("start").value = sloToIso(start);
  document.getElementById("end").value = sloToIso(end);
}

function sloRange(){
  const start = document.getElementById("start").value || null;
  const end = document.getElementById("end").value || null;
  return {start, end};
}

function formatBudgetWindow(seconds){
  if(seconds === null || seconds === undefined || Number.isNaN(seconds)) return "n/a";
  return window.LastPingShell ? window.LastPingShell.formatDuration(Number(seconds)) : `${Math.round(Number(seconds))}s`;
}

function percentage(value, digits){
  if(value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return `${Number(value).toFixed(digits ?? 1)}%`;
}

function burnMultiple(value){
  if(value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return `${Number(value).toFixed(2)}x`;
}

function stateClassFromSummary(summary){
  const state = summary && summary.state;
  if(state === "critical") return "kpi-critical";
  if(state === "warning") return "kpi-warning";
  return "kpi-healthy";
}

function stateClassFromBurn(windowRow){
  if(!windowRow || windowRow.burn_rate === null || windowRow.burn_rate === undefined) return "kpi-neutral";
  if(Number(windowRow.burn_rate) >= Number(windowRow.threshold || 0)) return "kpi-critical";
  if(Number(windowRow.burn_rate) >= 1) return "kpi-warning";
  return "kpi-healthy";
}

function stateClassFromComponent(row){
  if(!row) return "kpi-neutral";
  if(row.slo_met === false || Number(row.consumed_percent || 0) >= 100) return "kpi-critical";
  if(Number(row.consumed_percent || 0) >= 50) return "kpi-warning";
  return "kpi-healthy";
}

function complianceBadge(value){
  if(value === null || value === undefined) return '<span class="badge">n/a</span>';
  return `<span class="badge ${value ? "status-up" : "status-down"}">${value ? "met" : "missed"}</span>`;
}

function renderSloSummaryCards(summary, checks, health){
  const root = document.getElementById("sloSummaryCards");
  if(!root) return;
  if(!summary){
    root.innerHTML = '<article class="card kpi-card kpi-neutral"><div class="metric-label">SLO summary</div><div class="metric-value">n/a</div><div class="metric-sub">API key required to load SLO data.</div></article>';
    return;
  }

  const counts = window.LastPingShell ? window.LastPingShell.checkCounts(checks || []) : {total: 0, down: 0, degraded: 0, up: 0};
  const openIncidents = health && health.active_incidents !== undefined && health.active_incidents !== null
    ? Number(health.active_incidents)
    : 0;
  const summaryState = stateClassFromSummary(summary);
  const checksState = counts.down > 0 ? "kpi-critical" : (counts.degraded > 0 ? "kpi-warning" : "kpi-healthy");
  const incidentState = openIncidents > 0 ? "kpi-critical" : "kpi-healthy";

  root.innerHTML = [
    `<article class="card kpi-card ${summaryState}"><div class="metric-label">Budget Remaining</div><div class="metric-value">${percentage(summary.remaining_percent, 1)}</div><div class="metric-sub">${formatBudgetWindow(summary.remaining_seconds)} remaining in selected window</div></article>`,
    `<article class="card kpi-card ${summaryState}"><div class="metric-label">Budget Consumed</div><div class="metric-value">${percentage(summary.consumed_percent, 1)}</div><div class="metric-sub">Error budget ${percentage(summary.error_budget_percent, 2)}</div></article>`,
    `<article class="card kpi-card ${summaryState}"><div class="metric-label">Project Uptime</div><div class="metric-value">${percentage(summary.project_uptime_percent, 2)}</div><div class="metric-sub">SLO target ${percentage(summary.slo_target, 2)}</div></article>`,
    `<article class="card kpi-card ${checksState}"><div class="metric-label">Components</div><div class="metric-value">${counts.total}</div><div class="metric-sub">${counts.up} up | ${counts.down} down | ${counts.degraded} degraded</div></article>`,
    `<article class="card kpi-card ${incidentState}"><div class="metric-label">Open Incidents</div><div class="metric-value">${openIncidents}</div><div class="metric-sub">Current unresolved incident threads</div></article>`,
  ].join("");
}

function renderSloBurnCards(windows){
  const root = document.getElementById("sloBurnCards");
  if(!root) return;
  if(!windows || !windows.length){
    root.innerHTML = '<article class="card kpi-card kpi-neutral"><div class="metric-label">Burn rate</div><div class="metric-value">n/a</div><div class="metric-sub">No burn-rate data available.</div></article>';
    return;
  }
  root.innerHTML = windows.map((row)=> `
    <article class="card kpi-card ${stateClassFromBurn(row)}">
      <div class="metric-label">${row.label} Burn</div>
      <div class="metric-value">${burnMultiple(row.burn_rate)}</div>
      <div class="metric-sub">Threshold ${burnMultiple(row.threshold)} | uptime ${percentage(row.uptime_percent, 2)}</div>
    </article>
  `).join("");
}

function renderTopOffenders(rows){
  const root = document.getElementById("sloTopOffenders");
  if(!root) return;
  if(!rows || !rows.length){
    root.innerHTML = '<div class="muted">No components are consuming budget in this range.</div>';
    return;
  }
  root.innerHTML = rows.map((row)=> `
    <div class="insight-item">
      <div class="incident-card-head">
        <strong>${row.name}</strong>
        <span class="badge ${stateClassFromComponent(row) === "kpi-critical" ? "status-down" : (stateClassFromComponent(row) === "kpi-warning" ? "status-degraded" : "status-up")}">${percentage(row.consumed_percent, 1)} used</span>
      </div>
      <div class="muted">Uptime ${percentage(row.uptime_percent, 2)} | Remaining ${percentage(row.remaining_percent, 1)} | Share ${percentage(row.consumed_share_percent, 1)}</div>
    </div>
  `).join("");
}

function renderComplianceSummary(history){
  const root = document.getElementById("sloComplianceSummary");
  const monthlyRoot = document.getElementById("sloMonthlySummary");
  if(!root || !monthlyRoot) return;
  if(!history){
    root.innerHTML = '<div class="muted">No compliance history loaded.</div>';
    monthlyRoot.innerHTML = '<div class="muted">No monthly rollups loaded.</div>';
    return;
  }
  const daily = history.daily || {summary: {}};
  const monthly = history.monthly || {summary: {}, series: []};
  root.innerHTML = `
    <div class="incident-meta-grid">
      <div><span class="muted">Daily periods</span><div>${daily.summary.total || 0}</div></div>
      <div><span class="muted">Met</span><div>${daily.summary.met || 0}</div></div>
      <div><span class="muted">Missed</span><div>${daily.summary.missed || 0}</div></div>
      <div><span class="muted">Unknown</span><div>${daily.summary.unknown || 0}</div></div>
    </div>
  `;
  const monthlySeries = monthly.series || [];
  if(!monthlySeries.length){
    monthlyRoot.innerHTML = '<div class="muted">No monthly rollups in range.</div>';
    return;
  }
  monthlyRoot.innerHTML = monthlySeries.slice(-6).reverse().map((row)=> `
    <div class="insight-item">
      <div class="incident-card-head">
        <strong>${row.period}</strong>
        ${complianceBadge(row.slo_met)}
      </div>
      <div class="muted">Uptime ${percentage(row.uptime_percent, 2)} | SLA ${row.sla_met === null || row.sla_met === undefined ? "n/a" : (row.sla_met ? "met" : "missed")}</div>
    </div>
  `).join("");
}

function renderComponentTable(rows){
  const tbody = document.querySelector("#sloComponentTable tbody");
  if(!tbody) return;
  if(!rows || !rows.length){
    tbody.innerHTML = '<tr><td colspan="7" class="muted">No component budget data in range.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((row)=> `
    <tr>
      <td>${row.name}</td>
      <td>${percentage(row.uptime_percent, 2)}</td>
      <td>${percentage(row.consumed_percent, 1)}</td>
      <td>${percentage(row.remaining_percent, 1)}</td>
      <td>${percentage(row.consumed_share_percent, 1)}</td>
      <td>${complianceBadge(row.slo_met)}</td>
      <td>${complianceBadge(row.sla_met)}</td>
    </tr>
  `).join("");
}

function renderSloHistoryChart(series, sloTarget){
  if(window._sloHistoryChart){
    try{ window._sloHistoryChart.destroy(); }catch(_e){}
    window._sloHistoryChart = null;
  }
  const canvas = document.getElementById("sloHistoryChart");
  if(!canvas || typeof Chart === "undefined"){
    if(window.LastPingShell) window.LastPingShell.setChartEmpty("sloHistoryChartEmpty", true, "Chart library not available.");
    return;
  }
  const labels = (series || []).map((row)=> row.day);
  const data = (series || []).map((row)=> row.uptime_percent);
  const hasData = labels.length > 0;
  const ctx = canvas.getContext("2d");
  window._sloHistoryChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "Uptime %",
          data,
          borderColor: "rgba(96, 157, 255, 0.95)",
          backgroundColor: "rgba(71, 131, 229, 0.16)",
          tension: 0.22,
          fill: true,
          pointRadius: 2.5,
        },
        {
          label: "SLO target",
          data: labels.map(()=> sloTarget),
          borderColor: "rgba(255, 180, 84, 0.92)",
          borderDash: [6, 5],
          tension: 0,
          pointRadius: 0,
          fill: false,
        }
      ],
    },
    options: {
      maintainAspectRatio: false,
      scales: {
        x: {
          grid: {color: "rgba(71, 98, 134, 0.34)"},
          ticks: {color: "#9cb6d7", maxTicksLimit: 8},
          title: {display: true, text: "Day", color: "#a9c0df"},
        },
        y: {
          beginAtZero: true,
          suggestedMax: 100,
          grid: {color: "rgba(71, 98, 134, 0.34)"},
          ticks: {color: "#9cb6d7"},
          title: {display: true, text: "Uptime %", color: "#a9c0df"},
        }
      },
      plugins: {
        legend: {display: true, labels: {color: "#d9e8ff"}},
      },
    },
  });
  if(window.LastPingShell) window.LastPingShell.setChartEmpty("sloHistoryChartEmpty", !hasData, "No recent data for selected range.");
}

function renderSloComponentChart(rows){
  if(window._sloComponentChart){
    try{ window._sloComponentChart.destroy(); }catch(_e){}
    window._sloComponentChart = null;
  }
  const canvas = document.getElementById("sloComponentChart");
  if(!canvas || typeof Chart === "undefined"){
    if(window.LastPingShell) window.LastPingShell.setChartEmpty("sloComponentChartEmpty", true, "Chart library not available.");
    return;
  }
  const ranked = (rows || []).slice(0, 8);
  const labels = ranked.map((row)=> row.name);
  const data = ranked.map((row)=> Number(row.consumed_share_percent || 0));
  const colors = ranked.map((row)=> {
    const cls = stateClassFromComponent(row);
    if(cls === "kpi-critical") return "rgba(255, 93, 125, 0.85)";
    if(cls === "kpi-warning") return "rgba(255, 180, 84, 0.85)";
    return "rgba(42, 209, 138, 0.82)";
  });
  const hasData = labels.length > 0 && data.some((value)=> value > 0);
  const ctx = canvas.getContext("2d");
  window._sloComponentChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "Consumed budget share %",
        data,
        backgroundColor: colors,
        borderColor: colors,
        borderWidth: 1,
        borderRadius: 8,
      }],
    },
    options: {
      maintainAspectRatio: false,
      scales: {
        x: {
          grid: {display: false},
          ticks: {color: "#9cb6d7", maxRotation: 0, minRotation: 0},
        },
        y: {
          beginAtZero: true,
          grid: {color: "rgba(71, 98, 134, 0.34)"},
          ticks: {color: "#9cb6d7"},
          title: {display: true, text: "Consumed share %", color: "#a9c0df"},
        },
      },
      plugins: {
        legend: {display: false},
      },
    },
  });
  if(window.LastPingShell) window.LastPingShell.setChartEmpty("sloComponentChartEmpty", !hasData, "No component budget pressure in selected range.");
}

async function loadSloDashboard(){
  const perf = window.LastPingShell ? window.LastPingShell.createPerfTracker("SLO") : null;
  const pid = document.getElementById("projectId").value || "1";
  const headers = sloHeaders();
  const {start, end} = sloRange();
  const params = new URLSearchParams();
  if(start) params.set("start", start);
  if(end) params.set("end", end);

  if(window.LastPingShell){
    window.LastPingShell.setChartLoading("sloHistoryChartCard", true);
    window.LastPingShell.setChartLoading("sloComponentChartCard", true);
    window.LastPingShell.setChartEmpty("sloHistoryChartEmpty", false, "");
    window.LastPingShell.setChartEmpty("sloComponentChartEmpty", false, "");
  }

  try{
    const shellPromise = window.LastPingShell
      ? window.LastPingShell.hydratePageShell(pid, null, {perf})
      : Promise.resolve({checks: [], health: null});
    const sloPromise = perf && window.LastPingShell
      ? perf.fetchJson("slo-dashboard", `/projects/${pid}/metrics/slo-dashboard?${params.toString()}`, {headers})
      : fetch(`/projects/${pid}/metrics/slo-dashboard?${params.toString()}`, {headers}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null}));

    const [shellData, sloRes] = await Promise.all([shellPromise, sloPromise]);
    if(!sloRes.ok){
      renderSloSummaryCards(null, shellData.checks, shellData.health);
      renderSloBurnCards(null);
      renderTopOffenders([]);
      renderComplianceSummary(null);
      renderComponentTable([]);
      if(window.LastPingShell){
        window.LastPingShell.setChartEmpty("sloHistoryChartEmpty", true, "API key required to load SLO history.");
        window.LastPingShell.setChartEmpty("sloComponentChartEmpty", true, "API key required to load component split.");
      }
      return;
    }

    const payload = sloRes.data || {};
    const summary = payload.summary || null;
    const windows = payload.burn_rate_windows || [];
    const topOffenders = payload.top_offenders || [];
    const history = payload.historical_compliance || null;
    const componentRows = payload.service_budget_split && payload.service_budget_split.checks
      ? payload.service_budget_split.checks
      : [];

    if(perf){
      perf.measureRender("slo-render", ()=>{
        renderSloSummaryCards(summary, shellData.checks, shellData.health);
        renderSloBurnCards(windows);
        renderTopOffenders(topOffenders);
        renderComplianceSummary(history);
        renderComponentTable(componentRows);
        renderSloHistoryChart(history && history.daily ? history.daily.series || [] : [], summary ? summary.slo_target : null);
        renderSloComponentChart(componentRows);
      });
    }else{
      renderSloSummaryCards(summary, shellData.checks, shellData.health);
      renderSloBurnCards(windows);
      renderTopOffenders(topOffenders);
      renderComplianceSummary(history);
      renderComponentTable(componentRows);
      renderSloHistoryChart(history && history.daily ? history.daily.series || [] : [], summary ? summary.slo_target : null);
      renderSloComponentChart(componentRows);
    }
  }catch(_e){
    renderSloSummaryCards(null, [], null);
    renderSloBurnCards(null);
    renderTopOffenders([]);
    renderComplianceSummary(null);
    renderComponentTable([]);
    if(window.LastPingShell){
      window.LastPingShell.setChartEmpty("sloHistoryChartEmpty", true, "Unable to load SLO history.");
      window.LastPingShell.setChartEmpty("sloComponentChartEmpty", true, "Unable to load component split.");
    }
  }finally{
    if(window.LastPingShell){
      window.LastPingShell.setChartLoading("sloHistoryChartCard", false);
      window.LastPingShell.setChartLoading("sloComponentChartCard", false);
    }
    if(perf) perf.finish();
  }
}

document.addEventListener("DOMContentLoaded", ()=>{
  const now = new Date();
  const start = new Date(now.getTime() - 30 * 24 * 3600 * 1000);
  document.getElementById("start").value = sloToIso(start);
  document.getElementById("end").value = sloToIso(now);

  document.getElementById("slo7d").addEventListener("click", ()=> sloApplyPreset(7));
  document.getElementById("slo30d").addEventListener("click", ()=> sloApplyPreset(30));
  document.getElementById("slo90d").addEventListener("click", ()=> sloApplyPreset(90));
  document.getElementById("slo180d").addEventListener("click", ()=> sloApplyPreset(180));
  document.getElementById("loadSloBtn").addEventListener("click", loadSloDashboard);

  loadSloDashboard();
});
