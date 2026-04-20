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
    return token
      ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }
      : { "Content-Type": "application/json" };
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
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

  function setOrgMemberStatus(message, isError) {
    const el = $("orgMemberStatus");
    el.textContent = message;
    el.style.color = isError ? "var(--danger)" : "var(--muted)";
  }

  function setTeamMemberStatus(message, isError) {
    const el = $("teamMemberStatus");
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
    return `<option value="${escapeHtml(String(value))}" ${selected ? "selected" : ""}>${escapeHtml(label)}</option>`;
  }

  function renderOrgSwitcher(orgs) {
    const select = $("tenantOrgSelect");
    const preferred = getSelectedOrgId();
    const selected =
      preferred && orgs.some((org) => String(org.organization_id) === preferred)
        ? preferred
        : orgs[0]
          ? String(orgs[0].organization_id)
          : "";
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
                <div class="metric-label">${escapeHtml(org.organization_name)}</div>
                <div class="metric-sub">Role: ${escapeHtml(org.role)}</div>
                <div class="metric-sub">${org.project_count} projects - ${org.team_count} teams - ${org.service_account_count} service accounts</div>
              </article>`
          )
          .join("")
      : '<div class="muted">No organization memberships found for this user.</div>';
  }

  function populateTeamOptions(teams) {
    return teams.map((team) => ({ value: team.id, label: team.name }));
  }

  function renderProjects(data) {
    const projects = data.projects || [];
    const teamOptions = populateTeamOptions(data.teams || []);
    $("tenantProjectsRows").innerHTML = projects.length
      ? projects
          .map((project) => {
            const currentOwner =
              project.owner_teams && project.owner_teams.length ? String(project.owner_teams[0].id) : "";
            const ownerSelect = `<select data-owner-team-select="${project.id}" style="width:180px">
                ${teamOptions
                  .map((team) => optionHtml(team.value, team.label, String(team.value) === currentOwner))
                  .join("")}
              </select>`;
            const accessibleTeams = (project.accessible_teams || []).length
              ? project.accessible_teams
                  .map((team) => `${escapeHtml(team.name)} (${escapeHtml(team.role)})`)
                  .join(", ")
              : '<span class="muted">No team access</span>';
            return `<tr>
              <td><strong>${escapeHtml(project.name)}</strong></td>
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
          await fetchJson(
            `/orgs/${encodeURIComponent($("tenantOrgSelect").value)}/projects/${encodeURIComponent(projectId)}/owner-team`,
            {
              method: "PUT",
              headers: authHeaders(),
              body: JSON.stringify({ team_id: Number(select.value) }),
            }
          );
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
      .map((team) => optionHtml(team.id, team.name, false))
      .join("")}`;
  }

  function renderTokens(payload) {
    const rows = payload.tokens || [];
    $("tenantTokenRows").innerHTML = rows.length
      ? rows
          .map((row) => {
            const state = row.revoked_at
              ? "revoked"
              : row.rotation_required
                ? "rotation due"
                : row.is_active
                  ? "active"
                  : "inactive";
            const revokeButton = row.is_primary
              ? '<span class="muted">primary</span>'
              : row.revoked_at
                ? '<span class="muted">revoked</span>'
                : `<button class="btn btn-secondary" data-revoke-token="${row.id}">Revoke</button>`;
            return `<tr>
              <td>
                <strong>${escapeHtml(row.name || "unnamed")}</strong>
                <div class="muted">${escapeHtml(row.description || "")}</div>
              </td>
              <td>${escapeHtml(row.token_type)}</td>
              <td>${escapeHtml(row.project_name)}</td>
              <td>${escapeHtml(row.managed_by_team_name || "n/a")}</td>
              <td>${formatDate(row.last_used_at)}</td>
              <td>${formatDate(row.expires_at)}</td>
              <td>${escapeHtml(state)}</td>
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
              <td>${escapeHtml(row.actor || "system")}</td>
              <td>${escapeHtml(row.action)}</td>
              <td>team=${row.team_id || "n/a"} - project=${row.project_id || "n/a"}</td>
              <td>${escapeHtml(row.details || "")}</td>
            </tr>`
          )
          .join("")
      : '<tr><td colspan="5" class="muted">No membership/access audit rows yet.</td></tr>';
  }

  function renderOrgMembers(rows) {
    $("orgMemberRows").innerHTML = rows.length
      ? rows
          .map(
            (row) => `<tr>
              <td><strong>${escapeHtml(row.email)}</strong></td>
              <td>
                <select data-org-member-role="${row.user_id}" style="width:140px">
                  ${optionHtml("member", "member", row.role === "member")}
                  ${optionHtml("admin", "admin", row.role === "admin")}
                  ${optionHtml("owner", "owner", row.role === "owner")}
                </select>
              </td>
              <td>
                <div class="row" style="gap:8px;">
                  <button class="btn btn-secondary" data-save-org-member="${row.user_id}" data-email="${escapeHtml(row.email)}">Save</button>
                  <button class="btn btn-secondary" data-remove-org-member="${row.user_id}">Remove</button>
                </div>
              </td>
            </tr>`
          )
          .join("")
      : '<tr><td colspan="3" class="muted">No org members found.</td></tr>';

    document.querySelectorAll("[data-save-org-member]").forEach((button) => {
      button.addEventListener("click", async () => {
        const userId = button.getAttribute("data-save-org-member");
        const email = button.getAttribute("data-email");
        const roleSelect = document.querySelector(`[data-org-member-role="${userId}"]`);
        try {
          await saveOrgMember(email, roleSelect.value);
          setOrgMemberStatus(`Updated role for ${email}.`, false);
          await refreshSelectedOrg();
        } catch (err) {
          setOrgMemberStatus(err.message, true);
        }
      });
    });

    document.querySelectorAll("[data-remove-org-member]").forEach((button) => {
      button.addEventListener("click", async () => {
        const userId = button.getAttribute("data-remove-org-member");
        try {
          await fetchJson(`/orgs/${encodeURIComponent($("tenantOrgSelect").value)}/members/${encodeURIComponent(userId)}`, {
            method: "DELETE",
            headers: authHeaders(),
          });
          setOrgMemberStatus(`Removed org member ${userId}.`, false);
          await refreshSelectedOrg();
        } catch (err) {
          setOrgMemberStatus(err.message, true);
        }
      });
    });
  }

  function populateTeamMemberSelector(teams) {
    const select = $("teamMemberTeamSelect");
    const current = select.value;
    select.innerHTML = teams.length
      ? teams.map((team, index) => optionHtml(team.id, team.name, String(team.id) === String(current) || (!current && index === 0))).join("")
      : '<option value="">No teams</option>';
  }

  function renderTeamMembers(rows) {
    $("teamMemberRows").innerHTML = rows.length
      ? rows
          .map(
            (row) => `<tr>
              <td><strong>${escapeHtml(row.email)}</strong></td>
              <td>
                <select data-team-member-role="${row.user_id}" style="width:140px">
                  ${optionHtml("member", "member", row.role === "member")}
                  ${optionHtml("lead", "lead", row.role === "lead")}
                </select>
              </td>
              <td>
                <div class="row" style="gap:8px;">
                  <button class="btn btn-secondary" data-save-team-member="${row.user_id}" data-email="${escapeHtml(row.email)}">Save</button>
                  <button class="btn btn-secondary" data-remove-team-member="${row.user_id}">Remove</button>
                </div>
              </td>
            </tr>`
          )
          .join("")
      : '<tr><td colspan="3" class="muted">No members on the selected team.</td></tr>';

    document.querySelectorAll("[data-save-team-member]").forEach((button) => {
      button.addEventListener("click", async () => {
        const userId = button.getAttribute("data-save-team-member");
        const email = button.getAttribute("data-email");
        const roleSelect = document.querySelector(`[data-team-member-role="${userId}"]`);
        try {
          await saveTeamMember(email, roleSelect.value);
          setTeamMemberStatus(`Updated team role for ${email}.`, false);
          await refreshSelectedOrg();
        } catch (err) {
          setTeamMemberStatus(err.message, true);
        }
      });
    });

    document.querySelectorAll("[data-remove-team-member]").forEach((button) => {
      button.addEventListener("click", async () => {
        const teamId = $("teamMemberTeamSelect").value;
        const userId = button.getAttribute("data-remove-team-member");
        try {
          await fetchJson(
            `/orgs/${encodeURIComponent($("tenantOrgSelect").value)}/teams/${encodeURIComponent(teamId)}/members/${encodeURIComponent(userId)}`,
            {
              method: "DELETE",
              headers: authHeaders(),
            }
          );
          setTeamMemberStatus(`Removed team member ${userId}.`, false);
          await refreshSelectedOrg();
        } catch (err) {
          setTeamMemberStatus(err.message, true);
        }
      });
    });
  }

  async function refreshSelectedTeamMembers() {
    const orgId = $("tenantOrgSelect").value;
    const teamId = $("teamMemberTeamSelect").value;
    if (!orgId || !teamId) {
      $("teamMemberRows").innerHTML = '<tr><td colspan="3" class="muted">No team selected.</td></tr>';
      setTeamMemberStatus("Select a team to edit its membership.", false);
      return;
    }
    try {
      const rows = await fetchJson(
        `/orgs/${encodeURIComponent(orgId)}/teams/${encodeURIComponent(teamId)}/members`,
        { headers: authHeaders() }
      );
      renderTeamMembers(rows || []);
      setTeamMemberStatus(`Loaded ${rows.length || 0} members for the selected team.`, false);
    } catch (err) {
      setTeamMemberStatus(err.message, true);
    }
  }

  async function refreshSelectedOrg() {
    const orgId = $("tenantOrgSelect").value;
    if (!orgId) {
      $("tenantProjectsRows").innerHTML = '<tr><td colspan="6" class="muted">No org selected.</td></tr>';
      $("tenantTokenRows").innerHTML = '<tr><td colspan="8" class="muted">No org selected.</td></tr>';
      $("tenantAuditRows").innerHTML = '<tr><td colspan="5" class="muted">No org selected.</td></tr>';
      $("orgMemberRows").innerHTML = '<tr><td colspan="3" class="muted">No org selected.</td></tr>';
      $("teamMemberRows").innerHTML = '<tr><td colspan="3" class="muted">No team selected.</td></tr>';
      $("teamMemberTeamSelect").innerHTML = '<option value="">No teams</option>';
      return;
    }
    setSelectedOrgId(orgId);
    try {
      const [overview, tokens, audit, orgMembers] = await Promise.all([
        fetchJson(`/orgs/${encodeURIComponent(orgId)}/overview`, { headers: authHeaders() }),
        fetchJson(`/orgs/${encodeURIComponent(orgId)}/token-inventory`, { headers: authHeaders() }),
        fetchJson(`/orgs/${encodeURIComponent(orgId)}/membership-audit`, { headers: authHeaders() }),
        fetchJson(`/orgs/${encodeURIComponent(orgId)}/members`, { headers: authHeaders() }),
      ]);
      renderProjects(overview);
      renderTokens(tokens);
      renderAudit(audit);
      renderOrgMembers(orgMembers || []);
      populateTeamMemberSelector(overview.teams || []);
      await refreshSelectedTeamMembers();
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
      const res = await fetchJson(
        `/orgs/${encodeURIComponent(orgId)}/projects/${encodeURIComponent(projectId)}/service-accounts`,
        {
          method: "POST",
          headers: authHeaders(),
          body: JSON.stringify(body),
        }
      );
      $("serviceAccountPlaintext").value = res.api_key || "";
      setServiceAccountStatus(`Created service account ${res.token.name} for ${res.token.project_name}.`, false);
      await refreshSelectedOrg();
    } catch (err) {
      setServiceAccountStatus(err.message, true);
    }
  }

  async function saveOrgMember(email, role) {
    const orgId = $("tenantOrgSelect").value;
    return fetchJson(`/orgs/${encodeURIComponent(orgId)}/members`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ email, role }),
    });
  }

  async function saveTeamMember(email, role) {
    const orgId = $("tenantOrgSelect").value;
    const teamId = $("teamMemberTeamSelect").value;
    if (!teamId) {
      throw new Error("Select a team first.");
    }
    return fetchJson(`/orgs/${encodeURIComponent(orgId)}/teams/${encodeURIComponent(teamId)}/members`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ email, role }),
    });
  }

  async function createOrgMember() {
    const email = $("orgMemberEmail").value.trim();
    const role = $("orgMemberRole").value;
    if (!email) {
      setOrgMemberStatus("Enter an email to add an organization member.", true);
      return;
    }
    try {
      await saveOrgMember(email, role);
      $("orgMemberEmail").value = "";
      setOrgMemberStatus(`Added or updated ${email}.`, false);
      await refreshSelectedOrg();
    } catch (err) {
      setOrgMemberStatus(err.message, true);
    }
  }

  async function createTeamMember() {
    const email = $("teamMemberEmail").value.trim();
    const role = $("teamMemberRole").value;
    if (!email) {
      setTeamMemberStatus("Enter an email to add a team member.", true);
      return;
    }
    try {
      await saveTeamMember(email, role);
      $("teamMemberEmail").value = "";
      setTeamMemberStatus(`Added or updated ${email}.`, false);
      await refreshSelectedOrg();
    } catch (err) {
      setTeamMemberStatus(err.message, true);
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
  $("orgMemberAddBtn").addEventListener("click", createOrgMember);
  $("teamMemberAddBtn").addEventListener("click", createTeamMember);
  $("teamMemberTeamSelect").addEventListener("change", refreshSelectedTeamMembers);

  boot();
})();
