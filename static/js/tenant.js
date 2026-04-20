(function () {
  const tokenKey = "lastping_user_token";
  const orgStorageKey = "lastping_selected_org";

  function $(id) {
    return document.getElementById(id);
  }

  function getToken() {
    return (localStorage.getItem(tokenKey) || "").trim();
  }

  function getSelectedOrgId() {
    return (localStorage.getItem(orgStorageKey) || "").trim();
  }

  function setSelectedOrgId(value) {
    if (value) {
      localStorage.setItem(orgStorageKey, String(value));
    } else {
      localStorage.removeItem(orgStorageKey);
    }
  }

  function authHeaders() {
    const token = getToken();
    return token ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
  }

  async function fetchJson(url, options) {
    const res = await fetch(url, options);
    let body = {};
    try {
      body = await res.json();
    } catch (_err) {
      body = {};
    }
    if (!res.ok) {
      throw new Error(body.detail || `Request failed (${res.status})`);
    }
    return body;
  }

  function setStatus(message, isError) {
    const el = $("tenantStatus");
    el.textContent = message;
    el.style.color = isError ? "var(--danger)" : "var(--muted)";
  }

  function setServiceAccountStatus(message, isError) {
    const el = $("serviceAccountStatus");
    el.textContent = message;
    el.style.color = isError ? "var(--danger)" : "var(--muted)";
  }

  function formatDate(value) {
    if (!value) return "n/a";
    try {
      return new Date(value).toLocaleString();
    } catch (_err) {
      return value;
    }
  }

  function optionHtml(value, label, selected) {
    return `<option value="${String(value)}" ${selected ? "selected" : ""}>${label}</option>`;
  }

  function renderOrgSwitcher(orgs) {
    const select = $("tenantOrgSelect");
    const preferred = getSelectedOrgId();
    const selected = (preferred && orgs.some((org) => String(org.organization_id) === preferred))
      ? preferred
      : (orgs[0] ? String(orgs[0].organization_id) : "");
    select.innerHTML = orgs.length
      ? orgs
          .map((org) =>
            optionHtml(
              org.organization_id,
              `${org.organization_name} (${org.role})`,
              String(org.organization_id) === selected
            )
          )
          .join("")
      : '<option value="">No organizations</option>';
    if (selected) {
      select.value = selected;
      setSelectedOrgId(selected);
    }
    $("tenantSummary").innerHTML = orgs.length
      ? orgs
          .map(
            (org) => `<article class="card" style="flex:1;min-width:220px;margin-bottom:0;">
                <div class="metric-label">${org.organization_name}</div>
                <div class="metric-sub">Role: ${org.role}</div>
                <div class="metric-sub">${org.project_count} projects · ${org.team_count} teams · ${org.service_account_count} service accounts</div>
              </article>`
          )
          .join("")
      : '<div class="muted">No organization memberships found for this user.</div>';
  }

  function renderProjects(data) {
    const projects = data.projects || [];
    const teamOptions = (data.teams || []).map((team) => ({ value: team.id, label: team.name }));
    $("tenantProjectsRows").innerHTML = projects.length
      ? projects
          .map((project) => {
            const currentOwner = project.owner_teams && project.owner_teams.length ? String(project.owner_teams[0].id) : "";
            const ownerSelect = `<select data-owner-team-select="${project.id}" style="width:180px">
                ${teamOptions.map((team) => optionHtml(team.value, team.label, String(team.value) === currentOwner)).join("")}
              </select>`;
            const accessibleTeams = (project.accessible_teams || []).length
              ? project.accessible_teams.map((team) => `${team.name} (${team.role})`).join(", ")
              : '<span class="muted">No team access</span>';
            return `<tr>
              <td><strong>${project.name}</strong></td>
              <td>${ownerSelect}</td>
              <td>${accessibleTeams}</td>
              <td>${project.service_account_count}</td>
              <td>${project.active_token_count}</td>
              <td><button class="btn btn-secondary" data-save-owner-team="${project.id}">Save</button></td>
            </tr>`;
          })
          .join("")
      : '<tr><td colspan="6" class="muted">No projects belong to this organization yet.</td></tr>';

    document.querySelectorAll("[data-save-owner-team]").forEach((button) => {
      button.addEventListener("click", async () => {
        const projectId = button.getAttribute("data-save-owner-team");
        const select = document.querySelector(`[data-owner-team-select="${projectId}"]`);
        if (!select || !select.value) {
          setStatus("Select a team before saving owner team.", true);
          return;
        }
        try {
          await fetchJson(`/orgs/${encodeURIComponent($("tenantOrgSelect").value)}/projects/${encodeURIComponent(projectId)}/owner-team`, {
            method: "PUT",
            headers: authHeaders(),
            body: JSON.stringify({ team_id: Number(select.value) }),
          });
          setStatus(`Updated owner team for project ${projectId}.`, false);
          await refreshSelectedOrg();
        } catch (err) {
          setStatus(err.message, true);
        }
      });
    });

    $("serviceAccountProject").innerHTML = projects.length
      ? projects.map((project, index) => optionHtml(project.id, project.name, index === 0)).join("")
      : '<option value="">No projects</option>';
    $("serviceAccountTeam").innerHTML = `<option value="">(none)</option>${(data.teams || [])
      .map((team, index) => optionHtml(team.id, team.name, false))
      .join("")}`;
  }

  function renderTokens(payload) {
    const rows = payload.tokens || [];
    $("tenantTokenRows").innerHTML = rows.length
      ? rows
          .map((row) => {
            const state = row.revoked_at ? "revoked" : row.rotation_required ? "rotation due" : row.is_active ? "active" : "inactive";
            const revokeButton = row.is_primary
              ? '<span class="muted">primary</span>'
              : row.revoked_at
              ? '<span class="muted">revoked</span>'
              : `<button class="btn btn-secondary" data-revoke-token="${row.id}">Revoke</button>`;
            return `<tr>
              <td>
                <strong>${row.name || "unnamed"}</strong>
                <div class="muted">${row.description || ""}</div>
              </td>
              <td>${row.token_type}</td>
              <td>${row.project_name}</td>
              <td>${row.managed_by_team_name || "n/a"}</td>
              <td>${formatDate(row.last_used_at)}</td>
              <td>${formatDate(row.expires_at)}</td>
              <td>${state}</td>
              <td>${revokeButton}</td>
            </tr>`;
          })
          .join("")
      : '<tr><td colspan="8" class="muted">No tokens found for this organization.</td></tr>';

    document.querySelectorAll("[data-revoke-token]").forEach((button) => {
      button.addEventListener("click", async () => {
        const tokenId = button.getAttribute("data-revoke-token");
        try {
          await fetchJson(`/orgs/${encodeURIComponent($("tenantOrgSelect").value)}/tokens/${encodeURIComponent(tokenId)}/revoke`, {
            method: "POST",
            headers: authHeaders(),
          });
          setStatus(`Revoked token ${tokenId}.`, false);
          await refreshSelectedOrg();
        } catch (err) {
          setStatus(err.message, true);
        }
      });
    });
  }

  function renderAudit(payload) {
    const rows = payload.items || [];
    $("tenantAuditRows").innerHTML = rows.length
      ? rows
          .map(
            (row) => `<tr>
              <td>${formatDate(row.created_at)}</td>
              <td>${row.actor || "system"}</td>
              <td>${row.action}</td>
              <td>team=${row.team_id || "n/a"} · project=${row.project_id || "n/a"}</td>
              <td>${row.details || ""}</td>
            </tr>`
          )
          .join("")
      : '<tr><td colspan="5" class="muted">No membership/access audit rows yet.</td></tr>';
  }

  async function refreshSelectedOrg() {
    const orgId = $("tenantOrgSelect").value;
    if (!orgId) {
      $("tenantProjectsRows").innerHTML = '<tr><td colspan="6" class="muted">No org selected.</td></tr>';
      $("tenantTokenRows").innerHTML = '<tr><td colspan="8" class="muted">No org selected.</td></tr>';
      $("tenantAuditRows").innerHTML = '<tr><td colspan="5" class="muted">No org selected.</td></tr>';
      return;
    }
    setSelectedOrgId(orgId);
    try {
      const [overview, tokens, audit] = await Promise.all([
        fetchJson(`/orgs/${encodeURIComponent(orgId)}/overview`, { headers: authHeaders() }),
        fetchJson(`/orgs/${encodeURIComponent(orgId)}/token-inventory`, { headers: authHeaders() }),
        fetchJson(`/orgs/${encodeURIComponent(orgId)}/membership-audit`, { headers: authHeaders() }),
      ]);
      renderProjects(overview);
      renderTokens(tokens);
      renderAudit(audit);
      setStatus(`Loaded tenant console for ${overview.organization.name}.`, false);
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  async function createServiceAccount() {
    const orgId = $("tenantOrgSelect").value;
    const projectId = $("serviceAccountProject").value;
    if (!orgId || !projectId) {
      setServiceAccountStatus("Select an organization and project first.", true);
      return;
    }
    try {
      const body = {
        name: $("serviceAccountName").value.trim(),
        description: $("serviceAccountDescription").value.trim() || null,
        role: $("serviceAccountRole").value,
        team_id: $("serviceAccountTeam").value ? Number($("serviceAccountTeam").value) : null,
        expires_at: $("serviceAccountExpiresAt").value.trim() || null,
        rotation_interval_days: $("serviceAccountRotationDays").value.trim()
          ? Number($("serviceAccountRotationDays").value)
          : null,
      };
      const res = await fetchJson(`/orgs/${encodeURIComponent(orgId)}/projects/${encodeURIComponent(projectId)}/service-accounts`, {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify(body),
      });
      $("serviceAccountPlaintext").value = res.api_key || "";
      setServiceAccountStatus(`Created service account ${res.token.name} for ${res.token.project_name}.`, false);
      await refreshSelectedOrg();
    } catch (err) {
      setServiceAccountStatus(err.message, true);
    }
  }

  async function boot() {
    const token = getToken();
    if (!token) {
      setStatus("No bearer session found. Sign in via Enterprise Access first.", true);
      return;
    }
    try {
      const orgs = await fetchJson("/orgs/mine/overview", { headers: authHeaders() });
      renderOrgSwitcher(orgs || []);
      await refreshSelectedOrg();
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  $("tenantRefreshBtn").addEventListener("click", refreshSelectedOrg);
  $("tenantOrgSelect").addEventListener("change", refreshSelectedOrg);
  $("createServiceAccountBtn").addEventListener("click", createServiceAccount);

  boot();
})();
