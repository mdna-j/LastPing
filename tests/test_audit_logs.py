import os


def test_admin_create_and_revoke_apikey(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'mdb_audit.sqlite'}"
    os.environ['ADMIN_TOKEN'] = 'admintoken'

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, AuditLog
    from src.routers.admin_apikeys import create_apikey, revoke_apikey

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        p = Project(name='proj_audit')
        session.add(p)
        session.commit()
        session.refresh(p)

        # create an api key via admin endpoint (supply CSRF token for direct-call helper)
        csrf = 'testcsrf'
        res = create_apikey(project_id=p.id, rate_limit_per_minute=10, x_admin_token='admintoken', x_csrf_token=csrf, admin_csrf=csrf, session=session)
        assert 'api_key' in res and 'id' in res
        ak_id = res['id']

        # ensure audit row created
        rows = session.exec(select(AuditLog).where(AuditLog.action == 'create_apikey')).all()
        assert any(r.target_id == p.id for r in rows)

        # revoke
        res2 = revoke_apikey(api_key_id=ak_id, x_admin_token='admintoken', x_csrf_token=csrf, admin_csrf=csrf, session=session)
        assert res2['revoked'] == ak_id

        rows2 = session.exec(select(AuditLog).where(AuditLog.action == 'revoke_apikey')).all()
        assert any(r.target_id == p.id for r in rows2)


def test_audit_logs_for_project_and_checks(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'mdb_audit2.sqlite'}"
    os.environ['ADMIN_TOKEN'] = 'admintoken'

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import AuditLog
    from src.routers.projects import create_project, update_project_webhooks, set_project_maintenance, ProjectCreate, WebhookUpdate, MaintenanceWindow
    from src.routers.checks import create_check, update_check, set_check_maintenance, delete_check, CheckCreate, CheckUpdate

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        res = create_project(ProjectCreate(name="proj_audit2"), x_admin_token='admintoken', session=session)
        proj_id = res["project"].id

        create_check(
            project_id=proj_id,
            payload=CheckCreate(name="chk1"),
            x_admin_token='admintoken',
            _rl=None,
            session=session,
        )

        update_project_webhooks(
            project_id=proj_id,
            payload=WebhookUpdate(discord_webhook_url="https://discord.test/hook"),
            x_admin_token='admintoken',
            _rl=None,
            session=session,
        )

        set_project_maintenance(
            project_id=proj_id,
            payload=MaintenanceWindow(maintenance_starts_at=None, maintenance_ends_at=None),
            x_admin_token='admintoken',
            _rl=None,
            session=session,
        )

        from src.models import Check as CheckModel
        chk = session.exec(select(CheckModel).where(CheckModel.project_id == proj_id)).first()

        update_check(
            project_id=proj_id,
            check_id=chk.id,
            payload=CheckUpdate(name="chk1-renamed"),
            x_admin_token='admintoken',
            session=session,
        )

        set_check_maintenance(
            project_id=proj_id,
            check_id=chk.id,
            payload=MaintenanceWindow(maintenance_starts_at=None, maintenance_ends_at=None),
            x_admin_token='admintoken',
            session=session,
        )

        delete_check(
            project_id=proj_id,
            check_id=chk.id,
            x_admin_token='admintoken',
            session=session,
        )

        actions = [r.action for r in session.exec(select(AuditLog)).all()]
        for action in [
            "create_project",
            "create_check",
            "update_project_webhooks",
            "set_project_maintenance",
            "update_check",
            "set_check_maintenance",
            "delete_check",
        ]:
            assert action in actions
