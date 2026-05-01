function headersSettings(){
  const shellHeaders = window.LastPingShell && window.LastPingShell.projectHeaders
    ? window.LastPingShell.projectHeaders()
    : {};
  const apiKey = document.getElementById("apiKey").value || "";
  const admin = document.getElementById("adminToken").value || "";
  const headers = {"Content-Type": "application/json"};
  if(shellHeaders["Authorization"]) headers["Authorization"] = shellHeaders["Authorization"];
  if(shellHeaders["X-API-KEY"]) headers["X-API-KEY"] = shellHeaders["X-API-KEY"];
  if(shellHeaders["X-ADMIN-TOKEN"]) headers["X-ADMIN-TOKEN"] = shellHeaders["X-ADMIN-TOKEN"];
  if(apiKey) headers["X-API-KEY"] = apiKey;
  if(admin) headers["X-ADMIN-TOKEN"] = admin;
  return headers;
}

let clearJiraApiTokenRequested = false;
let clearPagerdutyIntegrationKeyRequested = false;
let selectedDeliveryId = null;

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

function formatSecretTimestamp(value, empty = "none"){
  if(!value) return empty;
  const dt = new Date(value);
  if(Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString();
}

function formatSecretInputValue(value){
  if(!value) return "";
  const dt = new Date(value);
  if(Number.isNaN(dt.getTime())) return value;
  return dt.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function renderSecretLifecycle(prefix, lifecycle){
  const data = lifecycle || {};
  const expiresInput = document.getElementById(`${prefix}ExpiresAt`);
  const intervalInput = document.getElementById(`${prefix}RotationIntervalDays`);
  const lastUsedEl = document.getElementById(`${prefix}LastUsedAt`);
  const lastRotatedEl = document.getElementById(`${prefix}LastRotatedAt`);
  const dueEl = document.getElementById(`${prefix}RotationDueAt`);
  const rolloverEl = document.getElementById(`${prefix}RolloverUntil`);

  if(expiresInput) expiresInput.value = formatSecretInputValue(data.expires_at);
  if(intervalInput) intervalInput.value = data.rotation_interval_days ?? "";
  if(lastUsedEl) lastUsedEl.textContent = formatSecretTimestamp(data.last_used_at, "never");
  if(lastRotatedEl) lastRotatedEl.textContent = formatSecretTimestamp(data.last_rotated_at, "unknown");
  if(dueEl){
    const base = formatSecretTimestamp(data.rotation_due_at, "none");
    dueEl.textContent = data.rotation_required && base !== "none" ? `${base} (overdue)` : base;
  }
  if(rolloverEl) rolloverEl.textContent = formatSecretTimestamp(data.rollover_active_until, "none");
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
  const statusText = (row)=>{
    const parts = [];
    if(row.delivery_status) parts.push(row.delivery_status);
    if(row.attempt_count !== undefined && row.attempt_count !== null){
      parts.push(`attempt ${row.attempt_count}`);
    }
    if(row.dead_at){
      parts.push(`dead @ ${new Date(row.dead_at).toLocaleString()}`);
    }else if(row.next_attempt_at && row.delivery_status && row.delivery_status !== "delivered"){
      parts.push(`next ${new Date(row.next_attempt_at).toLocaleString()}`);
    }
    if(row.last_retry_action && row.last_retry_at){
      parts.push(`${row.last_retry_action} @ ${new Date(row.last_retry_at).toLocaleString()}`);
    }
    if(row.detail) parts.push(row.detail);
    return parts.join(" · ") || "queued";
  };
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
              <td>${escapeHtml(statusText(row))}</td>
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

function queueBadgeClass(status){
  if(status === "dead") return "queue-badge queue-badge-dead";
  if(status === "retry") return "queue-badge queue-badge-retry";
  if(status === "processing") return "queue-badge queue-badge-processing";
  if(status === "delivered") return "queue-badge queue-badge-delivered";
  return "queue-badge queue-badge-queued";
}

function queueSummaryStateClass(state){
  if(state === "healthy") return "kpi-healthy";
  if(state === "warning") return "kpi-warning";
  if(state === "critical") return "kpi-critical";
  if(state === "flapping") return "kpi-flapping";
  return "kpi-neutral";
}

function queueThresholdState(value, warningThreshold, criticalThreshold){
  const numeric = Number(value || 0);
  if(numeric >= criticalThreshold) return "kpi-critical";
  if(numeric >= warningThreshold) return "kpi-warning";
  return "kpi-healthy";
}

function queueRateState(rate, warningThreshold, criticalThreshold){
  const numeric = Number(rate || 0);
  if(numeric >= criticalThreshold) return "kpi-critical";
  if(numeric >= warningThreshold) return "kpi-warning";
  return "kpi-healthy";
}

function queueFormatDuration(value){
  if(window.LastPingShell && window.LastPingShell.formatDuration){
    return window.LastPingShell.formatDuration(value);
  }
  if(value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return `${Math.max(0, Math.floor(Number(value)))}s`;
}

function queueFormatPerfMs(value){
  if(window.LastPingShell && window.LastPingShell.formatPerfMs){
    return window.LastPingShell.formatPerfMs(Number(value));
  }
  if(value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return `${Number(value).toFixed(1)}ms`;
}

function queueFormatPercent(value, digits = 1){
  if(value === null || value === undefined || Number.isNaN(Number(value))) return "n/a";
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function renderDeliveryQueueCards(rows, snapshot){
  const root = document.getElementById("deliveryQueueCards");
  if(!root) return;
  const deliveries = Array.isArray(rows) ? rows : [];
  const queue = snapshot || null;
  if(!queue){
    const count = (status)=> deliveries.filter((row)=> row.delivery_status === status).length;
    const items = [
      ["Queued", count("queued"), count("queued") > 0 ? "kpi-warning" : "kpi-neutral", "Waiting for first send attempt", "Showing visible rows only"],
      ["Retrying", count("retry"), count("retry") > 0 ? "kpi-warning" : "kpi-neutral", "Backoff or manual replay candidates", "Showing visible rows only"],
      ["Processing", count("processing"), count("processing") > 0 ? "kpi-warning" : "kpi-healthy", "Currently claimed by a worker", "Showing visible rows only"],
      ["Dead", count("dead"), count("dead") > 0 ? "kpi-critical" : "kpi-healthy", "Needs inspect, replay, or discard action", "Showing visible rows only"],
    ];
    root.innerHTML = items.map(([label, value, state, sub, meta])=> `
      <article class="card kpi-card ${state}">
        <div class="metric-label">${escapeHtml(label)}</div>
        <div class="metric-value">${escapeHtml(value)}</div>
        <div class="metric-sub">${escapeHtml(sub)}</div>
        <div class="muted">${escapeHtml(meta)}</div>
      </article>
    `).join("");
    return;
  }

  const channelParts = queue.per_channel_success
    ? Object.entries(queue.per_channel_success).map(([channel, row])=> {
      const completed = Number(row.completed || 0);
      const delivered = Number(row.delivered || 0);
      return `${channel} ${queueFormatPercent(row.success_rate, 0)} (${delivered}/${completed || 0})`;
    })
    : [];
  const completedWindow = Number(queue.completed_window || 0);
  const deliveredWindow = Number(queue.delivered_window || 0);
  const filteredRows = deliveries.length;
  const items = [
    [
      "Queue backlog",
      Number(queue.depth || 0) > 0 ? String(queue.depth) : "clear",
      queueSummaryStateClass(queue.state),
      `${queue.queued || 0} queued | ${queue.retrying || 0} retry | ${queue.processing || 0} processing`,
      `Overall async queue health. Table below shows ${filteredRows} filtered rows.`,
    ],
    [
      "Oldest pending",
      queue.oldest_pending_seconds ? queueFormatDuration(queue.oldest_pending_seconds) : "clear",
      Number(queue.depth || 0) === 0 ? "kpi-healthy" : queueThresholdState(queue.oldest_pending_seconds, 300, 1800),
      Number(queue.depth || 0) > 0 ? "Age of the oldest item still waiting to complete" : "No queued or retrying deliveries right now",
      `Full queue snapshot across all channels.`,
    ],
    [
      "Retry pressure",
      queueFormatPercent(queue.retry_rate),
      queueRateState(queue.retry_rate, 0.1, 0.5),
      `${queue.retrying || 0} items currently retrying`,
      `Measured over the last ${queue.window_hours || 24}h of queue activity.`,
    ],
    [
      "Dead letters",
      String(queue.dead_letters || 0),
      Number(queue.dead_letters || 0) === 0 ? "kpi-healthy" : queueThresholdState(queue.dead_letters, 1, 5),
      `Deliveries moved to dead state in the last ${queue.window_hours || 24}h`,
      Number(queue.dead_letters || 0) > 0 ? "Replay, inspect, or poison these intentionally." : "No recent dead-letter pressure.",
    ],
    [
      "Delivery latency",
      queue.p95_delivery_latency_ms !== null && queue.p95_delivery_latency_ms !== undefined
        ? queueFormatPerfMs(queue.p95_delivery_latency_ms)
        : "n/a",
      queue.p95_delivery_latency_ms === null || queue.p95_delivery_latency_ms === undefined
        ? "kpi-neutral"
        : queueThresholdState(queue.p95_delivery_latency_ms, 1500, 5000),
      queue.avg_delivery_latency_ms !== null && queue.avg_delivery_latency_ms !== undefined
        ? `avg ${queueFormatPerfMs(queue.avg_delivery_latency_ms)}`
        : "No recent successful deliveries to time",
      `${deliveredWindow} delivered / ${completedWindow} completed in the recent window.`,
    ],
    [
      "Success rate",
      completedWindow > 0 ? queueFormatPercent(queue.success_rate) : "n/a",
      completedWindow === 0 ? "kpi-neutral" : (Number(queue.success_rate || 0) < 0.8 ? "kpi-critical" : (Number(queue.success_rate || 0) < 0.95 ? "kpi-warning" : "kpi-healthy")),
      completedWindow > 0 ? `${deliveredWindow} of ${completedWindow} completed deliveries succeeded` : "No recent completed deliveries yet",
      channelParts.length ? channelParts.join(" | ") : "Per-channel success rates appear here once deliveries complete.",
    ],
  ];
  root.innerHTML = items.map(([label, value, state, sub, meta])=> `
    <article class="card kpi-card ${state}">
      <div class="metric-label">${escapeHtml(label)}</div>
      <div class="metric-value">${escapeHtml(value)}</div>
      <div class="metric-sub">${escapeHtml(sub)}</div>
      <div class="muted">${escapeHtml(meta)}</div>
    </article>
  `).join("");
}

function renderDeliveryInspect(detail){
  const root = document.getElementById("deliveryInspectPanel");
  if(!root) return;
  if(!detail){
    root.classList.add("hidden");
    root.innerHTML = "";
    return;
  }
  const payloadPreview = detail.payload_preview && Object.keys(detail.payload_preview).length
    ? JSON.stringify(detail.payload_preview, null, 2)
    : "No payload preview available.";
  const history = Array.isArray(detail.retry_history) ? detail.retry_history : [];
  root.classList.remove("hidden");
  root.innerHTML = `
    <div class="section-head">
      <h3>Delivery Inspect</h3>
      <div class="muted">Queue item #${escapeHtml(detail.id)} · ${escapeHtml(detail.channel)} / ${escapeHtml(detail.event)}</div>
    </div>
    <div class="queue-inspect-grid">
      <div class="queue-inspect-block">
        <div class="queue-inspect-meta">
          <div><span class="muted">Status</span><div><span class="${queueBadgeClass(detail.delivery_status)}">${escapeHtml(detail.delivery_status)}</span></div></div>
          <div><span class="muted">Attempts</span><div>${escapeHtml(detail.attempt_count)} / ${escapeHtml(detail.max_attempts)}</div></div>
          <div><span class="muted">Target</span><div>${escapeHtml(detail.target || "n/a")}</div></div>
          <div><span class="muted">Claimed by</span><div>${escapeHtml(detail.claimed_by || "unclaimed")}</div></div>
          <div><span class="muted">Next attempt</span><div>${escapeHtml(detail.next_attempt_at ? new Date(detail.next_attempt_at).toLocaleString() : "n/a")}</div></div>
          <div><span class="muted">HTTP status</span><div>${escapeHtml(detail.last_status_code ?? "n/a")}</div></div>
          <div><span class="muted">Incident</span><div>${escapeHtml(detail.incident_id ?? "n/a")}</div></div>
          <div><span class="muted">Check</span><div>${escapeHtml(detail.check_id ?? "n/a")}</div></div>
        </div>
        <div class="queue-detail-note">
          <strong>Last error:</strong> ${escapeHtml(detail.last_error || "none")}
        </div>
      </div>
      <div class="queue-inspect-block">
        <div class="queue-block-title">Payload preview</div>
        <pre class="queue-payload-preview">${escapeHtml(payloadPreview)}</pre>
      </div>
    </div>
    <div class="queue-history-block">
      <div class="queue-block-title">Retry / ops history</div>
      ${history.length ? `
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Actor</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              ${history.map((entry)=> `
                <tr>
                  <td>${escapeHtml(new Date(entry.created_at).toLocaleString())}</td>
                  <td>${escapeHtml(entry.action)}</td>
                  <td>${escapeHtml(entry.actor || "system")}</td>
                  <td>${escapeHtml(entry.detail || "n/a")}</td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      ` : '<div class="muted">No retry or operator history recorded for this delivery yet.</div>'}
    </div>
  `;
}

async function inspectNotificationDelivery(deliveryId){
  const pid = document.getElementById("projectId").value || "1";
  const headers = headersSettings();
  const root = document.getElementById("deliveryInspectPanel");
  if(root){
    root.classList.remove("hidden");
    root.innerHTML = '<div class="muted">Loading delivery details...</div>';
  }
  try{
    const res = await fetch(`/projects/${pid}/notification-deliveries/${deliveryId}`, {headers});
    const body = await res.json().catch(()=> ({}));
    if(!res.ok){
      const detail = body && body.detail ? body.detail : "Failed to inspect delivery";
      if(root) root.innerHTML = `<div class="muted">${escapeHtml(detail)}</div>`;
      return;
    }
    selectedDeliveryId = Number(deliveryId);
    renderDeliveryInspect(body);
  }catch(_e){
    if(root) root.innerHTML = '<div class="muted">Failed to inspect delivery.</div>';
  }
}

function renderNotificationFailures(rows, snapshot){
  const root = document.getElementById("notificationFailures");
  if(!root) return;
  const deliveries = Array.isArray(rows) ? rows : [];
  renderDeliveryQueueCards(deliveries, snapshot);
  if(!deliveries.length){
    root.innerHTML = '<div class="muted">No deliveries match the current filters.</div>';
    renderDeliveryInspect(null);
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
            <th>Summary</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${deliveries.map((row)=> `
            <tr class="queue-row ${selectedDeliveryId === row.id ? "queue-row-selected" : ""}" data-delivery-id="${row.id}">
              <td>${escapeHtml(new Date(row.created_at).toLocaleString())}</td>
              <td>${escapeHtml(row.channel)}</td>
              <td>${escapeHtml(row.event)}</td>
              <td title="${escapeHtml(row.target || "")}">${escapeHtml(row.target || "n/a")}</td>
              <td title="${escapeHtml(row.payload_summary || "")}">${escapeHtml(row.payload_summary || "n/a")}</td>
              <td>
                <div><span class="${queueBadgeClass(row.delivery_status)}">${escapeHtml(row.delivery_status)}</span></div>
                <div class="muted">attempt ${escapeHtml(row.attempt_count)} / ${escapeHtml(row.max_attempts)}</div>
                <div class="muted">${escapeHtml(row.last_error || "")}</div>
              </td>
              <td>
                <div class="queue-action-row">
                  <button class="btn btn-secondary queue-inspect-btn" data-delivery-id="${row.id}">Inspect</button>
                  <button class="btn btn-secondary queue-retry-btn" data-delivery-id="${row.id}" ${row.retryable ? "" : "disabled"}>Replay</button>
                  <button class="btn btn-secondary queue-cancel-btn" data-delivery-id="${row.id}" ${row.cancelable ? "" : "disabled"}>Cancel</button>
                  <button class="btn btn-secondary queue-poison-btn" data-delivery-id="${row.id}" ${row.poisonable ? "" : "disabled"}>Poison</button>
                </div>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
  root.querySelectorAll(".queue-inspect-btn").forEach((btn)=>{
    btn.addEventListener("click", ()=> inspectNotificationDelivery(btn.getAttribute("data-delivery-id")));
  });
  root.querySelectorAll(".queue-retry-btn").forEach((btn)=>{
    btn.addEventListener("click", ()=> retryNotificationFailure(btn.getAttribute("data-delivery-id")));
  });
  root.querySelectorAll(".queue-cancel-btn").forEach((btn)=>{
    btn.addEventListener("click", ()=> cancelNotificationDelivery(btn.getAttribute("data-delivery-id")));
  });
  root.querySelectorAll(".queue-poison-btn").forEach((btn)=>{
    btn.addEventListener("click", ()=> poisonNotificationDelivery(btn.getAttribute("data-delivery-id")));
  });
  root.querySelectorAll(".queue-row").forEach((row)=>{
    row.addEventListener("click", (event)=>{
      if(event.target && event.target.closest("button")) return;
      inspectNotificationDelivery(row.getAttribute("data-delivery-id"));
    });
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

function notificationQueueUrl(pid){
  const params = new URLSearchParams();
  const statusFilter = document.getElementById("deliveryStatusFilter");
  const channelFilter = document.getElementById("deliveryChannelFilter");
  const limitFilter = document.getElementById("deliveryLimit");
  params.set("delivery_status", statusFilter ? (statusFilter.value || "actionable") : "actionable");
  params.set("channel", channelFilter ? (channelFilter.value || "all") : "all");
  params.set("limit", limitFilter ? (limitFilter.value || "40") : "40");
  return `/projects/${pid}/notification-deliveries?${params.toString()}`;
}

async function loadSettings(){
  const perf = window.LastPingShell ? window.LastPingShell.createPerfTracker("Settings") : null;
  const pid = document.getElementById("projectId").value || "1";
  const headers = headersSettings();

  try{
    const [checksRes, sloRes, alertRes, jiraRes, pagerdutyRes, deliveryRes] = await Promise.all([
      perf && window.LastPingShell
        ? perf.fetchJson("checks", `/projects/${pid}/checks`, {headers})
        : fetch(`/projects/${pid}/checks`, {headers}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
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
        ? perf.fetchJson("notification-deliveries", notificationQueueUrl(pid), {headers})
        : fetch(notificationQueueUrl(pid), {headers}).then(async (res)=> ({ok: res.ok, status: res.status, data: res.ok ? await res.json() : null})),
    ]);
    const checks = checksRes.ok ? (checksRes.data || []) : null;
    const shellPromise = window.LastPingShell
      ? window.LastPingShell.hydratePageShell(pid, checks, {perf})
      : Promise.resolve({checks, health: null});

    if(!sloRes.ok || !alertRes.ok || !jiraRes.ok || !pagerdutyRes.ok || !deliveryRes.ok){
      alert("Failed to load settings");
      const shellData = await shellPromise;
      renderSettingsCards(shellData.checks, shellData.health, null, null, null, null);
      renderNotificationFailures([], shellData.health && shellData.health.platform ? shellData.health.platform.notification_queue : null);
      return;
    }

    const slo = sloRes.data || {};
    const alerts = alertRes.data || {};
    const jira = jiraRes.data || {};
    const pagerduty = pagerdutyRes.data || {};
    const deliveries = deliveryRes.data || [];
    const shellData = await shellPromise;
    const queueSnapshot = shellData.health && shellData.health.platform ? shellData.health.platform.notification_queue : null;

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
      renderSecretLifecycle("jiraToken", jira.secret_lifecycle);
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
      renderSecretLifecycle("pagerduty", pagerduty.secret_lifecycle);
      clearPagerdutyIntegrationKeyRequested = false;
      setPagerdutyKeyConfigured(!!pagerduty.integration_key_configured);

      renderSettingsCards(shellData.checks, shellData.health, slo, alerts, pagerduty, jira);
      renderNotificationFailures(deliveries, queueSnapshot);
    };
    if(perf) perf.measureRender("settings-render", render);
    else render();
    if(selectedDeliveryId){
      const visible = Array.isArray(deliveries) && deliveries.some((row)=> Number(row.id) === Number(selectedDeliveryId));
      if(visible) inspectNotificationDelivery(selectedDeliveryId);
      else renderDeliveryInspect(null);
    }
  }catch(_e){
    if(window.LastPingShell){
      const shellData = await window.LastPingShell.hydratePageShell(pid, null, {perf});
      renderSettingsCards(shellData.checks, shellData.health, null, null, null, null);
    }
    renderNotificationFailures([], null);
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
    clear_expiry: !document.getElementById("jiraTokenExpiresAt").value,
    clear_rotation_policy: !document.getElementById("jiraTokenRotationIntervalDays").value,
    grace_seconds: Math.max(0, parseInt(document.getElementById("jiraTokenGraceMinutes").value || "60", 10) || 0) * 60,
  };
  const jiraTokenValue = document.getElementById("jiraApiToken").value || "";
  if(jiraTokenValue) jiraPayload.api_token = jiraTokenValue;
  if(document.getElementById("jiraTokenExpiresAt").value) jiraPayload.expires_at = document.getElementById("jiraTokenExpiresAt").value;
  if(document.getElementById("jiraTokenRotationIntervalDays").value) jiraPayload.rotation_interval_days = parseInt(document.getElementById("jiraTokenRotationIntervalDays").value, 10);
  const pagerdutyPayload = {
    clear_integration_key: clearPagerdutyIntegrationKeyRequested,
    clear_expiry: !document.getElementById("pagerdutyExpiresAt").value,
    clear_rotation_policy: !document.getElementById("pagerdutyRotationIntervalDays").value,
    grace_seconds: Math.max(0, parseInt(document.getElementById("pagerdutyGraceMinutes").value || "60", 10) || 0) * 60,
  };
  const pagerdutyKeyValue = document.getElementById("pagerdutyIntegrationKey").value || "";
  if(pagerdutyKeyValue) pagerdutyPayload.integration_key = pagerdutyKeyValue;
  if(document.getElementById("pagerdutyExpiresAt").value) pagerdutyPayload.expires_at = document.getElementById("pagerdutyExpiresAt").value;
  if(document.getElementById("pagerdutyRotationIntervalDays").value) pagerdutyPayload.rotation_interval_days = parseInt(document.getElementById("pagerdutyRotationIntervalDays").value, 10);

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

async function runNotificationDeliveryAction(deliveryId, action, fallbackMessage){
  const pid = document.getElementById("projectId").value || "1";
  const headers = headersSettings();
  const root = document.getElementById("notificationFailures");
  try{
    const res = await fetch(`/projects/${pid}/notification-deliveries/${deliveryId}/${action}`, {method: "POST", headers});
    const body = await res.json().catch(()=> ({}));
    if(!res.ok){
      const detail = body && body.detail ? body.detail : fallbackMessage;
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
    alert(fallbackMessage);
  }
}

async function retryNotificationFailure(failureId){
  await runNotificationDeliveryAction(failureId, "retry", "Replay failed");
}

async function cancelNotificationDelivery(deliveryId){
  await runNotificationDeliveryAction(deliveryId, "cancel", "Cancel failed");
}

async function poisonNotificationDelivery(deliveryId){
  await runNotificationDeliveryAction(deliveryId, "poison", "Poison failed");
}

document.addEventListener("DOMContentLoaded", ()=>{
  const loadBtn = document.getElementById("loadBtn");
  const saveBtn = document.getElementById("saveBtn");
  const testBtn = document.getElementById("sendPagerdutyTestBtn");
  const clearJiraBtn = document.getElementById("clearJiraApiTokenBtn");
  const clearPdBtn = document.getElementById("clearPagerdutyIntegrationKeyBtn");
  const refreshQueueBtn = document.getElementById("refreshDeliveryQueueBtn");
  const deliveryStatusFilter = document.getElementById("deliveryStatusFilter");
  const deliveryChannelFilter = document.getElementById("deliveryChannelFilter");
  const deliveryLimit = document.getElementById("deliveryLimit");
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
  if(refreshQueueBtn) refreshQueueBtn.addEventListener("click", loadSettings);
  if(deliveryStatusFilter) deliveryStatusFilter.addEventListener("change", loadSettings);
  if(deliveryChannelFilter) deliveryChannelFilter.addEventListener("change", loadSettings);
  if(deliveryLimit) deliveryLimit.addEventListener("change", loadSettings);
  if(pid) pid.addEventListener("change", loadSettings);

  loadSettings();
});
