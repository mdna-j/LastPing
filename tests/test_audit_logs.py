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
