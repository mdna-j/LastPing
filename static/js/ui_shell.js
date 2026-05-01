// Shared shell helpers for /ui pages outside dashboard.
(function(){
  const UI_STATE_KEYS = {
    projectId: "lastping.ui.project_id",
    apiKey: "lastping.ui.api_key",
    adminToken: "lastping.ui.admin_token",
    userToken: "lastping.ui.user_token",
  };

  function getUiStateStore(){
    try{
      return window.sessionStorage;
    }catch(_e){
      return null;
    }
  }

  function getStoredUiValue(key){
    const store = getUiStateStore();
    if(!store) return "";
    try{
      return store.getItem(key) || "";
    }catch(_e){
      return "";
    }
  }

  function setStoredUiValue(key, value){
    const store = getUiStateStore();
    if(!store) return;
    try{
      if(value && String(value).trim()){
        store.setItem(key, String(value));
      }else{
        store.removeItem(key);
      }
    }catch(_e){
      // ignore storage failures
    }
  }

  function readFieldValue(id){
    const el = document.getElementById(id);
    return el ? (el.value || "") : "";
  }

  function normalizeProjectId(value){
    const raw = String(value || "").trim();
    if(/^\d+$/.test(raw) && Number(raw) >= 1) return raw;
    const stored = String(getStoredUiValue(UI_STATE_KEYS.projectId) || "").trim();
    if(/^\d+$/.test(stored) && Number(stored) >= 1) return stored;
    return "1";
  }

  function currentOrStoredValue(fieldId, storageKey){
    const current = readFieldValue(fieldId);
    if(current && String(current).trim()) return current;
    return getStoredUiValue(storageKey) || "";
  }

  function syncProjectAwareLinks(projectId){
    const pid = normalizeProjectId(projectId);
    document.querySelectorAll('a[href]').forEach((anchor)=>{
      const href = anchor.getAttribute("href") || "";
      if(/^\/ui\/projects\/(?:\d+|\{project_id\})\/settings$/.test(href)){
        anchor.setAttribute("href", `/ui/projects/${pid}/settings`);
      }else if(/^\/ui\/projects\/(?:\d+|\{project_id\})\/oncall$/.test(href)){
        anchor.setAttribute("href", `/ui/projects/${pid}/oncall`);
      }else if(/^\/ui\/projects\/(?:\d+|\{project_id\})\/remediation$/.test(href)){
        anchor.setAttribute("href", `/ui/projects/${pid}/remediation`);
      }
    });
  }

  function hydrateUiInputsFromSession(){
    const projectInput = document.getElementById("projectId");
    if(projectInput && !(projectInput.value || "").trim()){
      projectInput.value = normalizeProjectId(getStoredUiValue(UI_STATE_KEYS.projectId));
    }

    const apiInput = document.getElementById("apiKey");
    if(apiInput && !apiInput.value){
      apiInput.value = getStoredUiValue(UI_STATE_KEYS.apiKey);
    }

    const adminInput = document.getElementById("adminToken");
    if(adminInput && !adminInput.value){
      adminInput.value = getStoredUiValue(UI_STATE_KEYS.adminToken);
    }

    const userInput = document.getElementById("userToken");
    if(userInput && !userInput.value){
      userInput.value = getStoredUiValue(UI_STATE_KEYS.userToken);
    }
  }

  function persistUiInputsToSession(){
    const bind = (id, storageKey, callback)=>{
      const el = document.getElementById(id);
      if(!el) return;

      const sync = ()=>{
        setStoredUiValue(storageKey, el.value || "");
        if(callback) callback(el.value || "");
      };

      el.addEventListener("input", sync);
      el.addEventListener("change", sync);
      sync();
    };

    bind("projectId", UI_STATE_KEYS.projectId, (value)=> syncProjectAwareLinks(value));
    bind("apiKey", UI_STATE_KEYS.apiKey);
    bind("adminToken", UI_STATE_KEYS.adminToken);
    bind("userToken", UI_STATE_KEYS.userToken);
  }

  function buildProjectHeaders(){
    const headers = {};
    const apiKey = currentOrStoredValue("apiKey", UI_STATE_KEYS.apiKey);
    const adminToken = currentOrStoredValue("adminToken", UI_STATE_KEYS.adminToken);
    const userToken = currentOrStoredValue("userToken", UI_STATE_KEYS.userToken);
    if(apiKey) headers["X-API-KEY"] = apiKey;
    if(adminToken) headers["X-ADMIN-TOKEN"] = adminToken;
    if(userToken) headers["Authorization"] = `Bearer ${userToken}`;
    return headers;
  }

  function byteLength(text){
    if(!text) return 0;
    try{
      return new TextEncoder().encode(text).length;
    }catch(_e){
      return text.length;
    }
  }

  function formatPerfMs(value){
    if(value === null || value === undefined || Number.isNaN(value)) return "n/a";
    if(value >= 1000) return `${(value / 1000).toFixed(2)}s`;
    return `${value.toFixed(value >= 100 ? 0 : 1)}ms`;
  }

  function formatPerfBytes(bytes){
    if(bytes === null || bytes === undefined || Number.isNaN(bytes)) return "n/a";
    if(bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
    if(bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${bytes} B`;
  }

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

  function ensurePerfStrip(){
    const main = document.querySelector(".main-stage");
    if(!main) return null;
    let el = document.getElementById("frontendPerfStrip");
    if(el) return el;

    el = document.createElement("section");
    el.id = "frontendPerfStrip";
    el.className = "card perf-strip";
    el.innerHTML = `
      <div class="perf-strip-row">
        <div class="perf-item"><span class="health-label">Profile</span><span class="health-value" id="perfViewName">-</span></div>
        <div class="perf-item"><span class="health-label">Page load</span><span class="health-value" id="perfLoadMs">-</span></div>
        <div class="perf-item"><span class="health-label">Render</span><span class="health-value" id="perfRenderMs">-</span></div>
        <div class="perf-item"><span class="health-label">Payload</span><span class="health-value" id="perfPayload">-</span></div>
        <div class="perf-item"><span class="health-label">Requests</span><span class="health-value" id="perfRequestCount">-</span></div>
        <div class="perf-item perf-item-wide"><span class="health-label">Slowest</span><span class="health-value" id="perfSlowest">-</span></div>
      </div>
      <div class="perf-strip-meta">
        <div class="perf-meta" id="perfRequestSummary">No requests measured yet.</div>
        <div class="perf-meta" id="perfPaintSummary">FCP n/a</div>
      </div>
    `;

    const health = main.querySelector(".health-strip");
    const controls = main.querySelector(".controls-card");
    if(health){
      health.insertAdjacentElement("afterend", el);
    }else if(controls){
      controls.insertAdjacentElement("beforebegin", el);
    }else{
      main.insertBefore(el, main.firstChild);
    }
    return el;
  }

  function navPerfSummary(){
    const nav = performance.getEntriesByType ? performance.getEntriesByType("navigation")[0] : null;
    const paints = performance.getEntriesByType ? performance.getEntriesByType("paint") : [];
    const fcpEntry = paints.find((entry)=> entry.name === "first-contentful-paint");
    return {
      domReadyMs: nav ? nav.domContentLoadedEventEnd : null,
      loadEventMs: nav ? nav.loadEventEnd : null,
      fcpMs: fcpEntry ? fcpEntry.startTime : null,
    };
  }

  function renderPerfStrip(state){
    const el = ensurePerfStrip();
    if(!el || !state) return;

    const requests = state.requests || [];
    const slowest = requests.length
      ? requests.slice().sort((a, b)=> b.duration - a.duration)[0]
      : null;
    const totalMs = state.finishedAt
      ? Math.max(0, state.finishedAt - state.startedAt)
      : Math.max(0, performance.now() - state.startedAt);
    const requestSummary = requests.length
      ? requests
        .slice()
        .sort((a, b)=> b.duration - a.duration)
        .slice(0, 4)
        .map((r)=> `${r.label} ${formatPerfMs(r.duration)} (${formatPerfBytes(r.bytes)})`)
        .join(" | ")
      : "No requests measured yet.";
    const paints = [];
    if(state.navPerf && state.navPerf.fcpMs !== null && state.navPerf.fcpMs !== undefined){
      paints.push(`FCP ${formatPerfMs(state.navPerf.fcpMs)}`);
    }
    if(state.navPerf && state.navPerf.domReadyMs !== null && state.navPerf.domReadyMs !== undefined){
      paints.push(`DOM ready ${formatPerfMs(state.navPerf.domReadyMs)}`);
    }
    if(state.navPerf && state.navPerf.loadEventMs !== null && state.navPerf.loadEventMs !== undefined){
      paints.push(`window load ${formatPerfMs(state.navPerf.loadEventMs)}`);
    }

    const setText = (id, value)=>{
      const node = document.getElementById(id);
      if(node) node.textContent = value;
    };
    setText("perfViewName", state.pageName || "UI");
    setText("perfLoadMs", formatPerfMs(totalMs));
    setText("perfRenderMs", formatPerfMs(state.renderMs || 0));
    setText("perfPayload", formatPerfBytes(state.payloadBytes || 0));
    setText("perfRequestCount", String(requests.length));
    setText("perfSlowest", slowest ? `${slowest.label} ${formatPerfMs(slowest.duration)}` : "n/a");
    setText("perfRequestSummary", requestSummary);
    setText("perfPaintSummary", paints.length ? paints.join(" | ") : "FCP n/a");
  }

  function createPerfTracker(pageName){
    const state = {
      pageName,
      startedAt: performance.now(),
      finishedAt: null,
      payloadBytes: 0,
      renderMs: 0,
      requests: [],
      navPerf: navPerfSummary(),
    };

    function recordRequest(entry){
      state.requests.push(entry);
      state.payloadBytes += entry.bytes || 0;
      renderPerfStrip(state);
    }

    renderPerfStrip(state);

    return {
      async fetchJson(label, url, options){
        const startedAt = performance.now();
        try{
          const res = await fetch(url, options);
          const text = await res.text();
          const duration = performance.now() - startedAt;
          recordRequest({
            label,
            duration,
            bytes: byteLength(text),
            ok: res.ok,
            status: res.status,
          });

          let data = null;
          if(text){
            try{
              data = JSON.parse(text);
            }catch(err){
              if(res.ok) throw err;
            }
          }
          return {ok: res.ok, status: res.status, data, text};
        }catch(err){
          recordRequest({
            label,
            duration: performance.now() - startedAt,
            bytes: 0,
            ok: false,
            status: "ERR",
          });
          throw err;
        }
      },

      measureRender(_label, fn){
        const startedAt = performance.now();
        const result = fn();
        state.renderMs += performance.now() - startedAt;
        renderPerfStrip(state);
        return result;
      },

      finish(){
        state.finishedAt = performance.now();
        renderPerfStrip(state);
        return state;
      },
    };
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

  async function fetchChecks(projectId, perf){
    const headers = buildProjectHeaders();
    try{
      if(perf){
        const res = await perf.fetchJson("checks", `/projects/${projectId}/checks`, {headers});
        return res.ok ? (res.data || []) : [];
      }
      const res = await fetch(`/projects/${projectId}/checks`, {headers});
      if(!res.ok) return [];
      return await res.json();
    }catch(_e){
      return [];
    }
  }

  async function fetchHealth(projectId, perf){
    try{
      if(perf){
        const res = await perf.fetchJson("health", `/ui/dashboard/health?project_id=${encodeURIComponent(projectId)}`);
        return res.ok ? (res.data || null) : null;
      }
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

  async function hydratePageShell(projectId, checksOverride, options){
    const perf = options && options.perf;
    let checks = Array.isArray(checksOverride) ? checksOverride : null;
    if(!checks) checks = await fetchChecks(projectId, perf);

    setHealthStripFromFallback(checks);
    const health = await fetchHealth(projectId, perf);
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
    setHealthValue,
    regionSummaryFromChecks,
    checkCounts,
    setKpiState,
    setChartLoading,
    setChartEmpty,
    renderHeroBanner: renderHero,
    hydratePageShell,
    renderShellKpis,
    createPerfTracker,
    formatPerfBytes,
    formatPerfMs,
    projectHeaders: buildProjectHeaders,
    syncProjectAwareLinks,
  };

  document.addEventListener("DOMContentLoaded", ()=>{
    hydrateUiInputsFromSession();
    syncProjectAwareLinks(readFieldValue("projectId"));
    persistUiInputsToSession();
  });
})();
