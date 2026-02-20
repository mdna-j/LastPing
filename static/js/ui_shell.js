// Shared shell helpers for /ui pages outside dashboard.
(function(){
  function localTime(value){
    if(!value) return "n/a";
    const dt = new Date(value);
    if(Number.isNaN(dt.getTime())) return "n/a";
    return dt.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
  }

  function formatDuration(totalSeconds){
    if(totalSeconds === null || totalSeconds === undefined || Number.isNaN(totalSeconds)) return "n/a";
    const secs = Math.max(0, Math.floor(totalSeconds));
    const days = Math.floor(secs / 86400);
    const hours = Math.floor((secs % 86400) / 3600);
    const mins = Math.floor((secs % 3600) / 60);
    const rem = secs % 60;
    if(days > 0) return `${days}d ${hours}h ${mins}m`;
    if(hours > 0) return `${hours}h ${mins}m ${rem}s`;
    if(mins > 0) return `${mins}m ${rem}s`;
    return `${rem}s`;
  }

  function setHealthValue(id, value){
    const el = document.getElementById(id);
    if(el) el.textContent = value;
  }

  function normalizeRegion(rawRegion){
    const raw = (rawRegion || "").trim();
    if(!raw) return "global";
    const lower = raw.toLowerCase();
    if(lower === "*" || lower === "all" || lower === "any") return "any";
    if(raw.includes(",") || raw.includes(" ")) return "multi";
    return raw;
  }

  function regionSummaryFromChecks(checks){
    if(!checks || !checks.length) return "No checks";
    const buckets = {};
    for(const c of checks){
      const region = normalizeRegion(c.region);
      if(!buckets[region]) buckets[region] = {down: 0, degraded: 0, total: 0};
      buckets[region].total += 1;
      const st = (c.status || "").toLowerCase();
      if(st === "down") buckets[region].down += 1;
      else if(st === "degraded") buckets[region].degraded += 1;
    }
    return Object.keys(buckets).sort().map((name)=>{
      const b = buckets[name];
      if(b.down > 0) return `${name}: ${b.down} down`;
      if(b.degraded > 0) return `${name}: ${b.degraded} degraded`;
      return `${name}: healthy`;
    }).join(" | ");
  }

  function checkCounts(checks){
    const rows = checks || [];
    const counts = {total: rows.length, up: 0, down: 0, degraded: 0, flapping: 0};
    for(const c of rows){
      const st = (c.status || "").toLowerCase();
      if(st === "up") counts.up += 1;
      else if(st === "down") counts.down += 1;
      else if(st === "degraded") counts.degraded += 1;
      if(st.includes("flap")) counts.flapping += 1;
    }
    return counts;
  }

  function setKpiState(target, stateClass){
    const el = typeof target === "string" ? document.getElementById(target) : target;
    if(!el) return;
    el.classList.remove("kpi-neutral", "kpi-healthy", "kpi-warning", "kpi-critical", "kpi-flapping");
    el.classList.add(stateClass || "kpi-neutral");
  }

  function setChartLoading(cardId, isLoading){
    const card = document.getElementById(cardId);
    if(card) card.classList.toggle("is-loading", !!isLoading);
  }

  function setChartEmpty(id, shouldShow, message){
    const emptyEl = document.getElementById(id);
    if(!emptyEl) return;
    emptyEl.textContent = message || "No recent data";
    emptyEl.classList.toggle("hidden", !shouldShow);
  }

  function renderHero(healthData, checks){
    const banner = document.getElementById("incidentHeroBanner");
    const title = document.getElementById("incidentHeroTitle");
    const sub = document.getElementById("incidentHeroSub");
    if(!banner || !title || !sub) return;

    const fallbackDown = (checks || []).filter((c)=> (c.status || "").toLowerCase() === "down");
    const downCount = (healthData && healthData.down_checks_count !== undefined && healthData.down_checks_count !== null)
      ? Number(healthData.down_checks_count)
      : fallbackDown.length;
    if(!downCount || downCount <= 0){
      banner.classList.add("hero-banner-hidden");
      title.textContent = "No active outages";
      sub.textContent = "All checks currently healthy.";
      return;
    }

    let primaryName = "check";
    let primaryDuration = "n/a";
    if(healthData && healthData.primary_down_check){
      primaryName = healthData.primary_down_check.name || primaryName;
      primaryDuration = formatDuration(healthData.primary_down_check.down_seconds);
    }else if(fallbackDown.length){
      primaryName = fallbackDown[0].name || primaryName;
    }

    const noun = downCount === 1 ? "Check Down" : "Checks Down";
    title.textContent = `${downCount} ${noun} - ${primaryName} (${primaryDuration})`;
    const impact = (healthData && healthData.region_health_summary) ? healthData.region_health_summary : regionSummaryFromChecks(checks || []);
    sub.textContent = `Region impact: ${impact}`;
    banner.classList.remove("hero-banner-hidden");
  }

  async function fetchChecks(projectId){
    try{
      const res = await fetch(`/projects/${projectId}/checks`);
      if(!res.ok) return [];
      return await res.json();
    }catch(_e){
      return [];
    }
  }

  async function fetchHealth(projectId){
    try{
      const res = await fetch(`/ui/dashboard/health?project_id=${encodeURIComponent(projectId)}`);
      if(!res.ok) return null;
      return await res.json();
    }catch(_e){
      return null;
    }
  }

  function setHealthStripFromFallback(checks){
    setHealthValue("healthLastRefresh", localTime(new Date().toISOString()));
    setHealthValue("healthActiveIncidents", "...");
    setHealthValue("healthWorkersOnline", "...");
    setHealthValue("healthRegionHealth", regionSummaryFromChecks(checks || []));
  }

  function setHealthStripFromData(data, checks){
    if(!data){
      setHealthValue("healthActiveIncidents", "n/a");
      setHealthValue("healthWorkersOnline", "n/a");
      setHealthValue("healthRegionHealth", regionSummaryFromChecks(checks || []));
      return;
    }
    setHealthValue("healthLastRefresh", localTime(data.last_refresh));
    setHealthValue("healthActiveIncidents", String(data.active_incidents ?? "n/a"));
    setHealthValue("healthWorkersOnline", String(data.workers_online ?? "n/a"));
    setHealthValue("healthRegionHealth", data.region_health_summary || regionSummaryFromChecks(checks || []));
  }

  async function hydratePageShell(projectId, checksOverride){
    let checks = Array.isArray(checksOverride) ? checksOverride : null;
    if(!checks) checks = await fetchChecks(projectId);

    setHealthStripFromFallback(checks);
    const health = await fetchHealth(projectId);
    setHealthStripFromData(health, checks);
    renderHero(health, checks);
    return {checks, health};
  }

  function renderShellKpis(containerId, checks, health, options){
    const root = document.getElementById(containerId);
    if(!root) return;
    const opts = options || {};
    const c = checkCounts(checks || []);
    const incidents = health && health.active_incidents !== undefined && health.active_incidents !== null
      ? Number(health.active_incidents)
      : 0;
    const checksState = c.down > 0 ? "kpi-critical" : (c.degraded > 0 ? "kpi-warning" : "kpi-healthy");
    const downState = c.down > 0 ? "kpi-critical" : "kpi-healthy";
    const degradedState = c.degraded > 0 ? "kpi-warning" : "kpi-healthy";
    const incidentState = incidents > 0 ? "kpi-critical" : "kpi-healthy";

    const rows = [];
    rows.push(`<article class="card kpi-card ${checksState}"><div class="metric-label">Checks</div><div class="metric-value">${c.total}</div><div class="metric-sub">${c.up} up | ${c.down} down | ${c.degraded} degraded</div></article>`);
    rows.push(`<article class="card kpi-card ${downState}"><div class="metric-label">Down checks</div><div class="metric-value">${c.down}</div><div class="metric-sub">${opts.downSub || "Needs immediate action"}</div></article>`);
    rows.push(`<article class="card kpi-card ${degradedState}"><div class="metric-label">Degraded checks</div><div class="metric-value">${c.degraded}</div><div class="metric-sub">${opts.degradedSub || "Performance under threshold"}</div></article>`);
    rows.push(`<article class="card kpi-card ${incidentState}"><div class="metric-label">Open incidents</div><div class="metric-value">${incidents}</div><div class="metric-sub">${opts.incidentSub || "Current unresolved threads"}</div></article>`);

    root.innerHTML = rows.join("");
  }

  window.LastPingShell = {
    localTime,
    formatDuration,
    regionSummaryFromChecks,
    checkCounts,
    setKpiState,
    setChartLoading,
    setChartEmpty,
    hydratePageShell,
    renderShellKpis,
  };
})();
