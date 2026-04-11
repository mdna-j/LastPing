import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlmodel import Session

from ..db import get_session
from ..deps import limit_admin_api_requests
from ..security_ops import build_security_summary

router = APIRouter(
    prefix="/admin/security",
    tags=["admin_security"],
    dependencies=[Depends(limit_admin_api_requests)],
)


def _require_admin_token(x_admin_token: Optional[str]) -> None:
    admin_token = os.environ.get("ADMIN_TOKEN")
    if not admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=403, detail="Admin token required")


@router.get("/ops/summary")
def security_ops_summary(
    hours: int = Query(168, ge=1, le=24 * 365),
    project_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(25, ge=1, le=100),
    x_admin_token: Optional[str] = Header(None),
    session: Session = Depends(get_session),
):
    _require_admin_token(x_admin_token)
    return build_security_summary(session, hours=hours, project_id=project_id, limit=limit)


@router.get("/ui", response_class=HTMLResponse)
def security_ops_ui():
    return """
    <html>
    <head>
      <title>Security Ops</title>
      <meta name="viewport" content="width=device-width,initial-scale=1" />
      <link rel="stylesheet" href="/static/css/ui.css" />
    </head>
    <body class="page-security-ops">
    <div class="app-shell">
      <aside class="nav-rail">
        <div class="rail-brand">LP</div>
        <nav class="rail-links">
          <a class="rail-link" href="/ui/dashboard">Dashboard</a>
          <a class="rail-link" href="/ui/incidents">Incidents</a>
          <a class="rail-link active" href="/admin/security/ui">Security</a>
          <a class="rail-link" href="/admin/apikeys/ui">Admin Keys</a>
        </nav>
      </aside>

      <main class="main-stage">
        <header class="topbar">
          <div>
            <h1>Security Ops</h1>
            <div class="muted">Audit secret rotations, webhook failures, token lifecycle, admin actions, and suspicious auth in one place.</div>
          </div>
        </header>

        <section class="card controls-card">
          <div class="row dashboard-controls-row">
            <div class="dashboard-inputs">
              <label>Admin token: <input id="adminToken" type="password" autocomplete="off" placeholder="required" style="width:260px"/></label>
              <label>Hours: <input id="hours" type="number" min="1" max="8760" value="168" style="width:120px"/></label>
              <label>Project ID: <input id="projectId" type="number" min="1" placeholder="optional" style="width:140px"/></label>
              <label>Rows: <input id="limit" type="number" min="1" max="100" value="25" style="width:120px"/></label>
            </div>
            <div class="dashboard-actions">
              <button id="loadSecurityBtn" class="btn">Load Security Ops</button>
            </div>
          </div>
          <div id="securityOpsStatus" class="muted">Enter an admin token to load the security dashboard.</div>
        </section>

        <section id="securityOpsCards" class="kpi-grid"></section>

        <section class="security-ops-grid">
          <section class="card">
            <div class="section-head">
              <h3>Suspicious Auth Patterns</h3>
              <div class="muted">Repeated failures grouped by IP so brute-force and bad-client noise stand out quickly.</div>
            </div>
            <div id="securityAuthPatterns" class="muted">No data loaded.</div>
          </section>

          <section class="card">
            <div class="section-head">
              <h3>Webhook Failures</h3>
              <div class="muted">Delivery failures and inbound verification issues for PagerDuty, Jira, and other webhook flows.</div>
            </div>
            <div id="securityWebhookFailures" class="muted">No data loaded.</div>
          </section>
        </section>

        <section class="security-ops-grid">
          <section class="card">
            <div class="section-head">
              <h3>Secret Rotations</h3>
              <div class="muted">Credential changes, integration secret updates, and key rotation events.</div>
            </div>
            <div id="securitySecretChanges" class="muted">No data loaded.</div>
          </section>

          <section class="card">
            <div class="section-head">
              <h3>Token Lifecycle</h3>
              <div class="muted">Project token creation and revocation across admin and project-scoped flows.</div>
            </div>
            <div id="securityTokenEvents" class="muted">No data loaded.</div>
          </section>
        </section>

        <section class="security-ops-grid security-ops-grid-wide">
          <section class="card">
            <div class="section-head">
              <h3>Admin Actions</h3>
              <div class="muted">Recent actions taken with the admin token.</div>
            </div>
            <div id="securityAdminActions" class="muted">No data loaded.</div>
          </section>

          <section class="card">
            <div class="section-head">
              <h3>Recent Events</h3>
              <div class="muted">Latest security-relevant audit events in the selected time window.</div>
            </div>
            <div id="securityRecentEvents" class="muted">No data loaded.</div>
          </section>
        </section>
      </main>
    </div>
    <script src="/static/js/security_ops.js"></script>
    </body>
    </html>
    """
