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

function renderTimelineItem(item) {
  return `
    <div class="card incident-timeline-item">
      <div class="incident-card-head">
        <div>
          <strong>${escapeHtml(item.title || "Timeline entry")}</strong>
          <span class="badge status-up">${escapeHtml(item.kind || "timeline")}</span>
        </div>
        <span class="muted">${escapeHtml(formatTimestamp(item.ts))}</span>
      </div>
      <div>${escapeHtml(item.summary || "")}</div>
      ${item.actor ? `<div class="muted">Actor: ${escapeHtml(item.actor)}</div>` : ""}
    </div>
  `;
}

function renderPublicIncident(data) {
  const incident = data.incident || {};
  const project = data.project || {};
  const timeline = data.timeline || [];
  const stats = data.timeline_stats || {};

  return `
    <section class="card status-hero status-hero-${incident.resolved_at ? "operational" : "major_outage"}">
      <div class="status-hero-copy">
        <div class="status-hero-kicker">Shared incident</div>
        <h2>${escapeHtml(incident.check_name || `Incident ${incident.id || ""}`)}</h2>
        <div class="muted">${incident.resolved_at ? "Resolved incident timeline and follow-up context." : "Active investigation timeline and current customer-safe summary."}</div>
      </div>
      <div class="status-overview-grid">
        <div class="status-overview-stat">
          <span class="muted">Incident</span>
          <strong>#${incident.id ?? "n/a"}</strong>
        </div>
        <div class="status-overview-stat">
          <span class="muted">Status</span>
          <strong>${escapeHtml(incident.status || "unknown")}</strong>
        </div>
        <div class="status-overview-stat">
          <span class="muted">Duration</span>
          <strong>${escapeHtml(incident.duration || "n/a")}</strong>
        </div>
      </div>
    </section>

    <section class="status-summary-grid">
      <article class="card status-summary-card">
        <div class="status-summary-label">Project</div>
        <div class="status-summary-value">${escapeHtml(project.name || project.id || "Unknown")}</div>
        <div class="muted">Status page and incident timeline links stay public.</div>
      </article>
      <article class="card status-summary-card">
        <div class="status-summary-label">Started</div>
        <div class="status-summary-value">${escapeHtml(formatTimestamp(incident.started_at))}</div>
        <div class="muted">${incident.resolved_at ? `Resolved ${escapeHtml(formatTimestamp(incident.resolved_at))}` : "Still under investigation"}</div>
      </article>
    </section>

    <section class="card">
      <div class="section-head">
        <h3>Share Links</h3>
        <div class="muted">Use the public status page for broader service state and this page for incident-specific context.</div>
      </div>
      <div class="row">
        ${project.status_page_url ? `<a class="btn btn-secondary" href="${escapeHtml(project.status_page_url)}">Open status page</a>` : ""}
        ${incident.share_url ? `<a class="btn btn-secondary" href="${escapeHtml(incident.share_url)}">Copyable incident link</a>` : ""}
      </div>
    </section>

    <section class="card">
      <div class="section-head">
        <h3>Timeline</h3>
        <div class="muted">Auto-built chronology from events, alerts, workflow changes, notes, and remediation.</div>
      </div>
      <div class="incident-meta-grid">
        <div><span class="muted">Events</span><div>${stats.events || 0}</div></div>
        <div><span class="muted">Notes</span><div>${stats.notes || 0}</div></div>
        <div><span class="muted">Alerts</span><div>${stats.alerts || 0}</div></div>
        <div><span class="muted">Remediation</span><div>${stats.remediation_steps || 0}</div></div>
      </div>
      <div id="publicIncidentTimeline">
        ${timeline.length ? timeline.map(renderTimelineItem).join("") : '<div class="muted">No public timeline entries available.</div>'}
      </div>
    </section>
  `;
}

async function loadPublicIncident() {
  const root = document.getElementById("publicIncidentRoot");
  if (!root) return;
  const token = root.getAttribute("data-token");
  root.innerHTML = '<div class="card"><div class="muted">Loading incident timeline...</div></div>';

  try {
    const resp = await fetch(`/incidents/public/${token}`);
    if (!resp.ok) {
      root.innerHTML = '<div class="card"><div class="muted">Failed to load shared incident.</div></div>';
      return;
    }
    const data = await resp.json();
    root.innerHTML = renderPublicIncident(data);
  } catch (_error) {
    root.innerHTML = '<div class="card"><div class="muted">Failed to load shared incident.</div></div>';
  }
}

loadPublicIncident();
