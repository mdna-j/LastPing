function securityHeaders() {
  const token = document.getElementById("adminToken").value || "";
  const headers = {"Content-Type": "application/json"};
  if (token) headers["X-ADMIN-TOKEN"] = token;
  return headers;
}

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtDate(value) {
  if (!value) return "n/a";
  try {
    return new Date(value).toLocaleString();
  } catch (_e) {
    return String(value);
  }
}

function renderCards(counts) {
  const root = document.getElementById("securityOpsCards");
  if (!root) return;
  const items = [
    ["Secret changes", counts.secret_changes, counts.secret_changes > 0 ? "kpi-warning" : "kpi-neutral", "Rotations and secret-setting changes"],
    ["Token events", counts.token_events, counts.token_events > 0 ? "kpi-warning" : "kpi-neutral", "Create and revoke activity"],
    ["Webhook failures", counts.webhook_failures, counts.webhook_failures > 0 ? "kpi-critical" : "kpi-healthy", "Delivery and verification failures"],
    ["Admin actions", counts.admin_actions, counts.admin_actions > 0 ? "kpi-warning" : "kpi-neutral", "Actions taken with admin authority"],
    ["Suspicious auth", counts.suspicious_auth_events, counts.suspicious_auth_events > 0 ? "kpi-critical" : "kpi-healthy", "401/403 security-relevant auth events"],
    ["Suspicious patterns", counts.suspicious_auth_patterns, counts.suspicious_auth_patterns > 0 ? "kpi-critical" : "kpi-healthy", "Grouped by source IP"],
  ];
  root.innerHTML = items.map(([label, value, state, sub]) => `
    <article class="card kpi-card ${state}">
      <div class="metric-label">${esc(label)}</div>
      <div class="metric-value">${esc(value)}</div>
      <div class="metric-sub">${esc(sub)}</div>
    </article>
  `).join("");
}

function renderAuditTable(rootId, rows, emptyMessage) {
  const root = document.getElementById(rootId);
  if (!root) return;
  if (!Array.isArray(rows) || !rows.length) {
    root.innerHTML = `<div class="muted">${esc(emptyMessage)}</div>`;
    return;
  }
  root.innerHTML = `
    <div class="table-wrap">
      <table class="security-table">
        <thead>
          <tr>
            <th>When</th>
            <th>Action</th>
            <th>Actor</th>
            <th>Project</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td>${esc(fmtDate(row.created_at))}</td>
              <td><span class="security-chip">${esc(row.action)}</span></td>
              <td>${esc(row.actor || "unknown")}</td>
              <td>${esc(row.project_id ?? "global")}</td>
              <td title="${esc(typeof row.details === "string" ? row.details : JSON.stringify(row.details))}">${esc(row.details_preview || "n/a")}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderAuthPatterns(rows) {
  const root = document.getElementById("securityAuthPatterns");
  if (!root) return;
  if (!Array.isArray(rows) || !rows.length) {
    root.innerHTML = '<div class="muted">No suspicious auth patterns in the selected window.</div>';
    return;
  }
  root.innerHTML = rows.map((row) => `
    <article class="security-pattern-card">
      <div class="security-pattern-head">
        <div>
          <div class="security-pattern-ip">${esc(row.actor_ip || "unknown")}</div>
          <div class="muted">Last seen ${esc(fmtDate(row.last_seen_at))}</div>
        </div>
        <div class="security-pattern-count">${esc(row.count)} events</div>
      </div>
      <div class="security-chip-row">
        ${Object.entries(row.actions || {}).map(([action, count]) => `<span class="security-chip security-chip-critical">${esc(action)} x${esc(count)}</span>`).join("")}
      </div>
      <div class="muted">Paths: ${esc((row.paths || []).join(", ") || "n/a")}</div>
    </article>
  `).join("");
}

async function loadSecurityOps() {
  const token = document.getElementById("adminToken").value || "";
  const statusEl = document.getElementById("securityOpsStatus");
  if (!token) {
    statusEl.textContent = "Admin token required to load security ops.";
    return;
  }

  const hours = document.getElementById("hours").value || "168";
  const limit = document.getElementById("limit").value || "25";
  const projectId = document.getElementById("projectId").value || "";
  const params = new URLSearchParams({hours, limit});
  if (projectId) params.set("project_id", projectId);

  statusEl.textContent = "Loading security audit summary...";
  try {
    const res = await fetch(`/admin/security/ops/summary?${params.toString()}`, {headers: securityHeaders()});
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      statusEl.textContent = body && body.detail ? body.detail : "Failed to load security ops";
      return;
    }
    statusEl.textContent = `Showing the last ${body.window.hours}h${body.window.project_id ? ` for project ${body.window.project_id}` : ""}.`;
    renderCards(body.counts || {});
    renderAuthPatterns(body.suspicious_auth_patterns || []);
    renderAuditTable("securityWebhookFailures", body.webhook_failures || [], "No webhook failures in the selected window.");
    renderAuditTable("securitySecretChanges", body.secret_changes || [], "No secret changes in the selected window.");
    renderAuditTable("securityTokenEvents", body.token_events || [], "No token lifecycle events in the selected window.");
    renderAuditTable("securityAdminActions", body.admin_actions || [], "No admin actions in the selected window.");
    renderAuditTable("securityRecentEvents", body.recent_events || [], "No recent security events in the selected window.");
  } catch (_e) {
    statusEl.textContent = "Failed to load security ops.";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const loadBtn = document.getElementById("loadSecurityBtn");
  if (loadBtn) loadBtn.addEventListener("click", loadSecurityOps);
});
