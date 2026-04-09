import os

from sqlmodel import Session
from sqlalchemy import text


def test_integration_secrets_are_encrypted_at_rest(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_secret_encryption.sqlite'}"
    os.environ["LASTPING_ENCRYPTION_KEY"] = "test-encryption-key"

    from src import db as dbmod
    from src.models import Check, Project, RemediationHook
    from src.security import hash_api_key

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(
            name="encrypted-project",
            api_key_hash=hash_api_key("owner-key"),
            slack_webhook_url="https://hooks.slack.com/services/T000/B000/secret",
            pagerduty_integration_key="pd-secret",
            jira_user_email="jira-user@example.com",
            jira_api_token="jira-token",
            sms_auth_token="twilio-auth-token",
            oncall_email="ops@example.com",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        check = Check(
            project_id=project.id,
            name="secret-check",
            alert_pagerduty_integration_key="check-pd-secret",
            alert_generic_webhook_url="https://example.com/hook",
        )
        session.add(check)
        session.commit()
        session.refresh(check)

        hook = RemediationHook(
            project_id=project.id,
            event_type="down",
            url="https://example.com/remediate",
            secret="remediation-secret",
        )
        session.add(hook)
        session.commit()
        session.refresh(hook)

        project_id = project.id
        check_id = check.id
        hook_id = hook.id

        assert project.slack_webhook_url.endswith("/secret")
        assert project.pagerduty_integration_key == "pd-secret"
        assert project.jira_api_token == "jira-token"
        assert check.alert_pagerduty_integration_key == "check-pd-secret"
        assert hook.secret == "remediation-secret"

    with dbmod.engine.connect() as conn:
        project_row = conn.execute(
            text(
                "SELECT slack_webhook_url, pagerduty_integration_key, jira_user_email, "
                "jira_api_token, sms_auth_token, oncall_email FROM project WHERE id = :project_id"
            ),
            {"project_id": project_id},
        ).mappings().one()
        check_row = conn.execute(
            text(
                "SELECT alert_pagerduty_integration_key, alert_generic_webhook_url "
                "FROM \"check\" WHERE id = :check_id"
            ),
            {"check_id": check_id},
        ).mappings().one()
        hook_row = conn.execute(
            text("SELECT secret FROM remediation_hook WHERE id = :hook_id"),
            {"hook_id": hook_id},
        ).mappings().one()

    for stored_value, plaintext in (
        (project_row["slack_webhook_url"], "https://hooks.slack.com/services/T000/B000/secret"),
        (project_row["pagerduty_integration_key"], "pd-secret"),
        (project_row["jira_user_email"], "jira-user@example.com"),
        (project_row["jira_api_token"], "jira-token"),
        (project_row["sms_auth_token"], "twilio-auth-token"),
        (project_row["oncall_email"], "ops@example.com"),
        (check_row["alert_pagerduty_integration_key"], "check-pd-secret"),
        (check_row["alert_generic_webhook_url"], "https://example.com/hook"),
        (hook_row["secret"], "remediation-secret"),
    ):
        assert stored_value != plaintext
        assert str(stored_value).startswith("enc$fernet$")


def test_legacy_plaintext_secret_rows_remain_readable(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_secret_legacy.sqlite'}"
    os.environ["LASTPING_ENCRYPTION_KEY"] = "test-encryption-key"

    from src import db as dbmod
    from src.models import Project
    from src.security import hash_api_key

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="legacy-project", api_key_hash=hash_api_key("owner-key"))
        session.add(project)
        session.commit()
        session.refresh(project)
        project_id = project.id

    with dbmod.engine.begin() as conn:
        conn.execute(
            text("UPDATE project SET pagerduty_integration_key = :value WHERE id = :project_id"),
            {"value": "legacy-plain-pd-key", "project_id": project_id},
        )

    with Session(dbmod.engine) as session:
        project = session.get(Project, project_id)
        assert project is not None
        assert project.pagerduty_integration_key == "legacy-plain-pd-key"
