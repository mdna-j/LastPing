function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatTimestamp(value) {
  if (!value) return "n/a";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function formatDuration(seconds) {
  if (seconds == null) return "n/a";
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function badgeClass(status) {
  const normalized = String(status || "unknown").toLowerCase();
  if (normalized === "down") return "badge status-down";
  if (normalized === "degraded") return "badge status-degraded";
  return "badge status-up";
}

function overallLabel(status) {
  if (status === "major_outage") return "Major outage";
  if (status === "degraded") return "Degraded performance";
  if (status === "operational") return "All systems operational";
  return "Status unavailable";
}

function renderComponents(components) {
  if (!components.length) {
    return '<div class="muted">No public components configured for this project yet.</div>';
  }

  return `
    <div class="status-component-grid">
      ${components.map((component) => {
        const lastEvent = component.last_event
          ? `
            <div class="status-component-meta">
              <span class="muted">Latest signal</span>
              <strong>${escapeHtml(component.last_event.type)}</strong>
            </div>
            <div class="muted">${escapeHtml(component.last_event.message || "No message")} · ${escapeHtml(formatTimestamp(component.last_event.created_at))}</div>
          `
          : '<div class="muted">No recent events.</div>';

        return `
          <article class="card status-component-card">
            <div class="status-component-head">
              <div>
                <div class="status-component-name">${escapeHtml(component.name)}</div>
                <div class="muted">${escapeHtml(component.type)}${component.region ? ` · ${escapeHtml(component.region)}` : ""}</div>
              </div>
              <span class="${badgeClass(component.status)}">${escapeHtml(component.status || "UNKNOWN")}</span>
            </div>
            <div class="status-component-meta-grid">
              <div class="status-component-meta">
                <span class="muted">Last heartbeat</span>
                <strong>${escapeHtml(formatTimestamp(component.last_ping))}</strong>
              </div>
              <div class="status-component-meta">
                <span class="muted">Incident</span>
                <strong>${component.incident_open ? `Open #${component.incident_id}` : "None"}</strong>
              </div>
            </div>
            ${lastEvent}
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderIncidentHistory(incidents) {
  if (!incidents.length) {
    return '<div class="muted">No incidents have been recorded for this project yet.</div>';
  }

  return `
    <div class="status-history-list">
      ${incidents.map((incident) => `
        <article class="card status-history-card ${incident.resolved_at ? "status-history-resolved" : "status-history-open"}">
          <div class="status-history-head">
            <div>
              <div class="status-history-title">${escapeHtml(incident.check_name)} <span class="${badgeClass(incident.resolved_at ? "up" : "down")}">${incident.resolved_at ? "Resolved" : "Investigating"}</span></div>
              <div class="muted">Incident #${incident.id}</div>
            </div>
            <div class="status-history-duration">${escapeHtml(formatDuration(incident.duration_seconds))}</div>
          </div>
          <div class="status-history-meta">
            <div><span class="muted">Started</span><strong>${escapeHtml(formatTimestamp(incident.started_at))}</strong></div>
            <div><span class="muted">Resolved</span><strong>${escapeHtml(formatTimestamp(incident.resolved_at))}</strong></div>
          </div>
          <div class="muted">${incident.latest_event ? `${escapeHtml(incident.latest_event.message || incident.latest_event.type)} · ${escapeHtml(formatTimestamp(incident.latest_event.created_at))}` : "No timeline details available."}</div>
        </article>
      `).join("")}
    </div>
  `;
}

function bindSubscriptionForms(root, projectId) {
  const feedback = root.querySelector("#statusSubscribeFeedback");
  const setFeedback = (message, isError) => {
    if (!feedback) return;
    feedback.className = `status-subscribe-feedback ${isError ? "status-subscribe-error" : "status-subscribe-success"}`;
    feedback.textContent = message;
  };

  root.querySelectorAll("[data-status-subscribe]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const channel = form.getAttribute("data-channel");
      const input = form.querySelector("input");
      const button = form.querySelector("button");
      const target = (input?.value || "").trim();
      if (!channel || !target) {
        setFeedback("Enter a destination before subscribing.", true);
        return;
      }

      try {
        if (button) button.disabled = true;
        setFeedback("Saving subscription...", false);
        const resp = await fetch(`/ui/status/${projectId}/subscribe`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ channel, target }),
        });
        const payload = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          const detail = payload?.detail || "Failed to save subscription.";
          setFeedback(typeof detail === "string" ? detail : "Failed to save subscription.", true);
          return;
        }
        setFeedback(payload.message || "Subscription saved.", false);
        if (input) input.value = "";
      } catch (error) {
        setFeedback(error?.message || "Failed to save subscription.", true);
      } finally {
        if (button) button.disabled = false;
      }
    });
  });
}

function renderStatus(data) {
  const summary = data.summary || {};
  const components = data.components || data.checks || [];
  const openIncidents = data.open_incidents || [];
  const history = data.incident_history || [];
  const leadIncident = openIncidents[0];
  const heroClass = `status-hero status-hero-${summary.overall_status || "unknown"}`;
  const heroSubtitle = openIncidents.length
    ? `${openIncidents.length} active incident${openIncidents.length === 1 ? "" : "s"}${leadIncident ? ` · investigating ${leadIncident.check_name}` : ""}`
    : "No active incidents. Subscribe for new incident updates.";

  return `
    <section class="card ${heroClass}">
      <div class="status-hero-copy">
        <div class="status-hero-kicker">Current state</div>
        <h2>${escapeHtml(overallLabel(summary.overall_status))}</h2>
        <div class="muted">${escapeHtml(heroSubtitle)}</div>
      </div>
      <div class="status-overview-grid">
        <div class="status-overview-stat">
          <span class="muted">Components</span>
          <strong>${summary.component_count ?? 0}</strong>
        </div>
        <div class="status-overview-stat">
          <span class="muted">Operational</span>
          <strong>${summary.up_count ?? 0}</strong>
        </div>
        <div class="status-overview-stat">
          <span class="muted">Impacted</span>
          <strong>${summary.down_count ?? 0}</strong>
        </div>
        <div class="status-overview-stat">
          <span class="muted">Degraded</span>
          <strong>${summary.degraded_count ?? 0}</strong>
        </div>
      </div>
    </section>

    <section class="status-summary-grid">
      <article class="card status-summary-card">
        <div class="status-summary-label">Open incidents</div>
        <div class="status-summary-value">${summary.open_incident_count ?? 0}</div>
        <div class="muted">Actively being investigated right now.</div>
      </article>
      <article class="card status-summary-card">
        <div class="status-summary-label">Last updated</div>
        <div class="status-summary-value">${escapeHtml(formatTimestamp(summary.generated_at))}</div>
        <div class="muted">Status snapshots refresh each page load.</div>
      </article>
    </section>

    <section class="status-main-grid">
      <article class="card">
        <div class="section-head">
          <h3>Component status</h3>
          <div class="muted">Live service health for each monitored component.</div>
        </div>
        ${renderComponents(components)}
      </article>

      <article class="card">
        <div class="section-head">
          <h3>Subscribe to updates</h3>
          <div class="muted">Receive new incident and resolution notices by email or webhook.</div>
        </div>
        <form class="status-subscribe-form" data-status-subscribe data-channel="email">
          <label>Email updates</label>
          <div class="status-subscribe-row">
            <input type="email" placeholder="ops@example.com" />
            <button class="btn" type="submit">Subscribe</button>
          </div>
        </form>
        <form class="status-subscribe-form" data-status-subscribe data-channel="webhook">
          <label>Webhook updates</label>
          <div class="status-subscribe-row">
            <input type="url" placeholder="https://example.com/status-webhook" />
            <button class="btn btn-secondary" type="submit">Add webhook</button>
          </div>
        </form>
        <div id="statusSubscribeFeedback" class="status-subscribe-feedback muted">Subscriptions are project-scoped and public to this page.</div>
      </article>
    </section>

    <section class="card">
      <div class="section-head">
        <h3>Incident history</h3>
        <div class="muted">Recent incidents and their investigation timeline.</div>
      </div>
      ${renderIncidentHistory(history)}
    </section>
  `;
}

async function loadStatus() {
  const root = document.getElementById("statusRoot");
  if (!root) return;
  const projectId = root.getAttribute("data-project-id");
  root.innerHTML = '<div class="card"><div class="muted">Loading public status...</div></div>';

  try {
    const resp = await fetch(`/ui/status/${projectId}/data`);
    if (!resp.ok) {
      throw new Error("Failed to load status page");
    }
    const data = await resp.json();
    root.innerHTML = renderStatus(data);
    bindSubscriptionForms(root, projectId);
  } catch (error) {
    root.innerHTML = `
      <div class="card">
        <h3>Status unavailable</h3>
        <div class="muted">${escapeHtml(error?.message || "Failed to load status page.")}</div>
      </div>
    `;
  }
}

document.addEventListener("DOMContentLoaded", loadStatus);
