import os
from datetime import datetime, timedelta

from fastapi.testclient import TestClient


def test_incident_timeline_and_postmortem_exports(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_incident_postmortem.sqlite'}"
    os.environ["ADMIN_TOKEN"] = "admintoken"

    from sqlmodel import Session

    from src import db as dbmod
    from src.main import app
    from src.models import (
        AuditLog,
        Check,
        CheckStatus,
        CheckType,
        Event,
        EventType,
        Incident,
        IncidentNote,
        OnCallAlert,
        Project,
        RemediationApproval,
        RemediationLog,
    )

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        project = Project(name="postmortem-project")
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(project_id=project.id, name="checkout-api", type=CheckType.HEARTBEAT, status=CheckStatus.DOWN)
        session.add(check)
        session.commit()
        session.refresh(check)

        started_at = datetime.utcnow() - timedelta(minutes=15)
        incident = Incident(
            project_id=project.id,
            check_id=check.id,
            started_at=started_at,
            resolved_at=started_at + timedelta(minutes=12),
            status="resolved",
            share_token="shared-incident-token-12345",
            owner="alice",
            acknowledged_at=started_at + timedelta(minutes=2),
            acknowledged_by="user:1",
        )
        session.add(incident)
        session.commit()
        session.refresh(incident)

        session.add(
            Event(
                check_id=check.id,
                project_id=project.id,
                incident_id=incident.id,
                event_type=EventType.DOWN,
                message="heartbeat overdue",
                created_at=started_at + timedelta(minutes=1),
            )
        )
        session.add(
            IncidentNote(
                incident_id=incident.id,
                project_id=project.id,
                author="user:1",
                body="Investigated region saturation and restarted the worker.",
                created_at=started_at + timedelta(minutes=6),
            )
        )
        session.add(
            AuditLog(
                actor="user:1",
                action="assign_incident",
                target_type="incident",
                target_id=incident.id,
                details="owner:-->alice",
                created_at=started_at + timedelta(minutes=2),
            )
        )
        session.add(
            OnCallAlert(
                project_id=project.id,
                check_id=check.id,
                event_type="down",
                message="Primary on-call paged",
                created_at=started_at + timedelta(minutes=3),
                last_notified_at=started_at + timedelta(minutes=4),
                escalation_level=1,
            )
        )
        session.add(
            RemediationApproval(
                hook_id=1,
                project_id=project.id,
                check_id=check.id,
                event_type="down",
                reason="Worker restart needed",
                status="approved",
                requested_at=started_at + timedelta(minutes=5),
                decided_at=started_at + timedelta(minutes=5, seconds=30),
                decided_by="user:1",
                executed_at=started_at + timedelta(minutes=7),
                execution_status="success",
                execution_message="Restart approved and executed",
            )
        )
        session.add(
            RemediationLog(
                hook_id=1,
                project_id=project.id,
                check_id=check.id,
                event_type="down",
                status="success",
                response_code=200,
                message="Restarted checkout worker",
                created_at=started_at + timedelta(minutes=7),
            )
        )
        session.commit()

        project_id = project.id
        incident_id = incident.id

    headers = {"X-ADMIN-TOKEN": "admintoken"}

    detail = client.get(f"/projects/{project_id}/incidents/{incident_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    detail_json = detail.json()
    assert detail_json["timeline_stats"]["alerts"] == 1
    assert detail_json["timeline_stats"]["remediation_steps"] >= 2
    titles = [item["title"] for item in detail_json["timeline"]]
    assert "Incident opened" in titles
    assert "Check reported DOWN" in titles
    assert "Incident assigned" in titles
    assert "On-call alert opened" in titles
    assert "Remediation approval requested" in titles
    assert "Remediation step executed" in titles

    timeline = client.get(f"/projects/{project_id}/incidents/{incident_id}/timeline", headers=headers)
    assert timeline.status_code == 200, timeline.text
    timeline_json = timeline.json()
    assert timeline_json["check_name"] == "checkout-api"
    assert len(timeline_json["timeline"]) >= 6

    markdown = client.get(f"/projects/{project_id}/incidents/{incident_id}/postmortem.md", headers=headers)
    assert markdown.status_code == 200, markdown.text
    assert markdown.headers["content-type"].startswith("text/markdown")
    assert "Incident Postmortem" in markdown.text
    assert "## Root Cause" in markdown.text
    assert "## Action Items" in markdown.text
    assert "Primary on-call paged" in markdown.text
    assert "Restarted checkout worker" in markdown.text
    assert f"/ui/status/{project_id}" in markdown.text
    assert "/ui/incidents/public/shared-incident-token-12345" in markdown.text

    pdf = client.get(f"/projects/{project_id}/incidents/{incident_id}/postmortem.pdf", headers=headers)
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert pdf.content.startswith(b"%PDF-1.4")
