(function () {
  const tokenKey = "lastping_user_token";
  let pendingChallenge = null;
  let pendingChallengeMode = null;

  function $(id) {
    return document.getElementById(id);
  }

  function getToken() {
    return (localStorage.getItem(tokenKey) || "").trim();
  }

  function setToken(token) {
    if (token) {
      localStorage.setItem(tokenKey, token);
    } else {
      localStorage.removeItem(tokenKey);
    }
  }

  function authHeaders() {
    const headers = { "Content-Type": "application/json" };
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    return headers;
  }

  function setStatus(message, isError) {
    const el = $("accountStatus");
    el.textContent = message;
    el.style.color = isError ? "var(--danger)" : "var(--muted)";
  }

  function setMfaStatus(message, isError) {
    const el = $("mfaStatus");
    el.textContent = message;
    el.style.color = isError ? "var(--danger)" : "var(--muted)";
  }

  function showMfaCard(show) {
    $("mfaCard").style.display = show ? "block" : "none";
  }

  function showEnrollment(secret, uri) {
    $("mfaEnrollBlock").style.display = secret ? "block" : "none";
    $("mfaSecret").value = secret || "";
    $("mfaUri").value = uri || "";
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

  function renderProviders(providers) {
    const root = $("ssoProviders");
    if (!providers.length) {
      root.innerHTML = '<div class="muted">No SSO providers are configured.</div>';
      return;
    }
    root.innerHTML = providers
      .map(
        (provider) =>
          `<a class="btn btn-secondary" href="/users/sso/${encodeURIComponent(
            provider.name
          )}/start?redirect_to=${encodeURIComponent(window.location.origin + "/ui/account")}">Continue with ${provider.label}</a>`
      )
      .join("");
  }

  function renderProfile(data) {
    $("accountSummary").innerHTML = `
      <div><strong>${data.display_name || data.email}</strong></div>
      <div class="muted">${data.email}</div>
      <div class="muted">MFA: ${data.mfa_enabled ? "enabled" : "not enabled"} | Last login: ${
      data.last_login_at || "n/a"
    }</div>
    `;
    const orgRoles = data.organizations || [];
    $("orgRoles").innerHTML = orgRoles.length
      ? orgRoles
          .map(
            (row) =>
              `<div class="card" style="margin-bottom:8px;"><div><strong>${row.organization_name}</strong></div><div class="muted">Role: ${row.role}</div></div>`
          )
          .join("")
      : '<div class="muted">No organization memberships.</div>';
    const identities = data.linked_identities || [];
    $("linkedIdentities").innerHTML = identities.length
      ? identities
          .map(
            (row) =>
              `<div class="card" style="margin-bottom:8px;"><div><strong>${row.provider}</strong></div><div class="muted">${
                row.email || "no email"
              }</div><div class="muted">Last login: ${row.last_login_at || "n/a"}</div></div>`
          )
          .join("")
      : '<div class="muted">No linked identities.</div>';
    setMfaStatus(
      data.mfa_enabled
        ? "MFA is enabled for this account."
        : "MFA is not enabled. Admin users will be prompted to enroll during password login.",
      false
    );
    if (!pendingChallenge) {
      showMfaCard(true);
      showEnrollment("", "");
    }
  }

  function renderSessions(payload) {
    const rows = (payload.sessions || []).map((row) => {
      const status = row.revoked_at ? "revoked" : row.current ? "current" : "active";
      return `<tr>
        <td>
          <strong>${row.session_name || "session"}</strong>
          <div class="muted">${row.issued_from_ip || "unknown IP"}</div>
        </td>
        <td>
          <div>${row.auth_method || "unknown"}</div>
          <div class="muted">${row.auth_provider || (row.mfa_verified_at ? "MFA verified" : "single factor")}</div>
        </td>
        <td>${row.created_at || "n/a"}</td>
        <td>${row.last_seen_at || "n/a"}</td>
        <td>${status}</td>
        <td>${
          row.revoked_at
            ? '<span class="muted">revoked</span>'
            : `<button class="btn btn-secondary" data-revoke-session="${row.id}">${
                row.current ? "Revoke current" : "Revoke"
              }</button>`
        }</td>
      </tr>`;
    });
    $("sessionRows").innerHTML = rows.length
      ? rows.join("")
      : '<tr><td colspan="6" class="muted">No sessions found.</td></tr>';
    document.querySelectorAll("[data-revoke-session]").forEach((button) => {
      button.addEventListener("click", async () => {
        const sessionId = button.getAttribute("data-revoke-session");
        try {
          await fetchJson(`/users/sessions/${encodeURIComponent(sessionId)}`, {
            method: "DELETE",
            headers: authHeaders(),
          });
          setStatus(`Revoked session ${sessionId}.`, false);
          if (String(sessionId) === String(button.textContent.includes("current") ? sessionId : "")) {
            // no-op; current-session self-revoke is handled by follow-up refresh
          }
          await refreshAccount();
        } catch (err) {
          setStatus(err.message, true);
        }
      });
    });
  }

  async function refreshAccount() {
    const token = getToken();
    if (!token) {
      $("accountSummary").textContent = "No active session.";
      $("orgRoles").innerHTML = '<div class="muted">No organization memberships loaded.</div>';
      $("linkedIdentities").innerHTML = '<div class="muted">No linked SSO identities loaded.</div>';
      $("sessionRows").innerHTML = '<tr><td colspan="6" class="muted">No sessions loaded.</td></tr>';
      return;
    }
    try {
      const [me, sessions] = await Promise.all([
        fetchJson("/users/me", { headers: { Authorization: `Bearer ${token}` } }),
        fetchJson("/users/sessions", { headers: { Authorization: `Bearer ${token}` } }),
      ]);
      renderProfile(me);
      renderSessions(sessions);
      setStatus(`Authenticated as ${me.email}.`, false);
    } catch (err) {
      setStatus(err.message, true);
      if (/token/i.test(err.message)) {
        setToken("");
      }
    }
  }

  async function login() {
    try {
      const body = await fetchJson("/users/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: $("authEmail").value.trim(),
          password: $("authPassword").value,
        }),
      });
      if (body.access_token) {
        setToken(body.access_token);
        pendingChallenge = null;
        pendingChallengeMode = null;
        showMfaCard(true);
        showEnrollment("", "");
        setStatus("Login successful.", false);
        await refreshAccount();
        return;
      }
      pendingChallenge = body.mfa_challenge_token || null;
      pendingChallengeMode = body.mfa_setup_required ? "enroll" : body.mfa_required ? "verify" : null;
      showMfaCard(Boolean(pendingChallenge));
      if (body.mfa_setup_required) {
        showEnrollment(body.mfa_enrollment_secret, body.mfa_enrollment_uri);
        setMfaStatus("Admin login requires MFA enrollment before issuing a session.", false);
      } else if (body.mfa_required) {
        showEnrollment("", "");
        setMfaStatus("Enter your current TOTP code to finish login.", false);
      } else {
        setMfaStatus("No MFA step required.", false);
      }
      setStatus("Additional MFA step required.", false);
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  async function completeMfa() {
    if (!pendingChallenge || !pendingChallengeMode) {
      setMfaStatus("No MFA challenge is pending.", true);
      return;
    }
    const path = pendingChallengeMode === "enroll" ? "/users/mfa/enable" : "/users/mfa/login/verify";
    try {
      const body = await fetchJson(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          challenge_token: pendingChallenge,
          code: $("mfaCode").value.trim(),
        }),
      });
      if (body.access_token) {
        setToken(body.access_token);
      }
      pendingChallenge = null;
      pendingChallengeMode = null;
      showEnrollment("", "");
      showMfaCard(true);
      setMfaStatus("MFA complete.", false);
      setStatus("Authenticated.", false);
      $("mfaCode").value = "";
      await refreshAccount();
    } catch (err) {
      setMfaStatus(err.message, true);
    }
  }

  async function startEnrollment() {
    try {
      const body = await fetchJson("/users/mfa/enroll", {
        method: "POST",
        headers: authHeaders(),
      });
      pendingChallenge = body.challenge_token;
      pendingChallengeMode = "enroll";
      showMfaCard(true);
      showEnrollment(body.secret, body.otpauth_uri);
      setMfaStatus("Enrollment secret generated. Enter the current code from your authenticator app.", false);
    } catch (err) {
      setMfaStatus(err.message, true);
    }
  }

  async function disableMfa() {
    try {
      await fetchJson("/users/mfa/disable", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ code: $("disableMfaCode").value.trim() }),
      });
      $("disableMfaCode").value = "";
      setMfaStatus("MFA disabled.", false);
      await refreshAccount();
    } catch (err) {
      setMfaStatus(err.message, true);
    }
  }

  async function revokeOthers() {
    try {
      const body = await fetchJson("/users/sessions/revoke-others", {
        method: "POST",
        headers: authHeaders(),
      });
      setStatus(`Revoked ${body.revoked} other sessions.`, false);
      await refreshAccount();
    } catch (err) {
      setStatus(err.message, true);
    }
  }

  async function loadProviders() {
    try {
      const body = await fetchJson("/users/sso/providers", { headers: { Accept: "application/json" } });
      renderProviders(body.providers || []);
    } catch (err) {
      $("ssoProviders").innerHTML = `<div class="muted">${err.message}</div>`;
    }
  }

  function logout() {
    setToken("");
    pendingChallenge = null;
    pendingChallengeMode = null;
    $("authPassword").value = "";
    showEnrollment("", "");
    $("sessionRows").innerHTML = '<tr><td colspan="6" class="muted">No sessions loaded.</td></tr>';
    $("accountSummary").textContent = "No active session.";
    setStatus("Cleared local session token.", false);
  }

  $("loginBtn").addEventListener("click", login);
  $("logoutBtn").addEventListener("click", logout);
  $("verifyMfaBtn").addEventListener("click", completeMfa);
  $("startMfaEnrollBtn").addEventListener("click", startEnrollment);
  $("disableMfaBtn").addEventListener("click", disableMfa);
  $("revokeOthersBtn").addEventListener("click", revokeOthers);

  loadProviders();
  refreshAccount();
})();
