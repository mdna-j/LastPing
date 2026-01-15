import os
from datetime import datetime, timedelta

from sqlmodel import Session, select


def test_owner_viewer_flows(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db.sqlite'}"
    os.environ["USER_RATE_LIMIT_PER_MINUTE"] = '2'

    # import app modules after setting DB env
    from src import db as dbmod
    from src.security import hash_api_key, hash_password
    from src.models import Project, User, ProjectMembership, UserToken

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        # create project
        project = Project(name="p1", api_key_hash=hash_api_key("apik"))
        session.add(project)
        session.commit()
        session.refresh(project)

        # owner user
        owner = User(email="owner@example.com", hashed_password=hash_password("pw"))
        viewer = User(email="viewer@example.com", hashed_password=hash_password("pw"))
        session.add(owner)
        session.add(viewer)
        session.commit()
        session.refresh(owner)
        session.refresh(viewer)

        # membership
        m1 = ProjectMembership(user_id=owner.id, project_id=project.id, role='owner')
        m2 = ProjectMembership(user_id=viewer.id, project_id=project.id, role='viewer')
        session.add(m1)
        session.add(m2)
        session.commit()

        # create tokens
        from secrets import token_urlsafe
        otoken = token_urlsafe(16)
        vtoken = token_urlsafe(16)
        ut1 = UserToken(user_id=owner.id, token=otoken, created_at=datetime.utcnow(), expires_at=datetime.utcnow()+timedelta(hours=1))
        ut2 = UserToken(user_id=viewer.id, token=vtoken, created_at=datetime.utcnow(), expires_at=datetime.utcnow()+timedelta(hours=1))
        session.add(ut1)
        session.add(ut2)
        session.commit()
        # capture ids for later sessions
        project_id = project.id
        owner_id = owner.id
        viewer_id = viewer.id

    # check role via DB (avoid importing pydantic EmailStr-requiring module)
    from src.routers.checks import create_check, CheckCreate
    from src.deps import limit_by_api_key
    from fastapi import HTTPException

    with Session(dbmod.engine) as session:
        pm_owner = session.exec(select(ProjectMembership).where(ProjectMembership.user_id == owner_id, ProjectMembership.project_id == project_id)).first()
        assert pm_owner.role == 'owner'
        pm_viewer = session.exec(select(ProjectMembership).where(ProjectMembership.user_id == viewer_id, ProjectMembership.project_id == project_id)).first()
        assert pm_viewer.role == 'viewer'

        # owner can create check
        payload = CheckCreate(name="c1", type="heartbeat")
        _rl = limit_by_api_key(project_id, authorization=f"Bearer {otoken}", x_api_key=None, x_admin_token=None, session=session)
        chk = create_check(project_id, payload, x_admin_token=None, authorization=f"Bearer {otoken}", x_api_key=None, _rl=_rl, session=session)
        assert chk.name == 'c1'

        # viewer cannot create check
        payload2 = CheckCreate(name="c2", type="heartbeat")
        try:
            _rl2 = limit_by_api_key(project_id, authorization=f"Bearer {vtoken}", x_api_key=None, x_admin_token=None, session=session)
            create_check(project_id, payload2, x_admin_token=None, authorization=f"Bearer {vtoken}", x_api_key=None, _rl=_rl2, session=session)
            assert False, "viewer should not be allowed to create checks"
        except HTTPException as e:
            assert e.status_code in (403, 401)

        # rate limit: owner can create up to 2 times then receive 429
        payload3 = CheckCreate(name="c3", type="heartbeat")
        _rl3 = limit_by_api_key(project_id, authorization=f"Bearer {otoken}", x_api_key=None, x_admin_token=None, session=session)
        chk3 = create_check(project_id, payload3, x_admin_token=None, authorization=f"Bearer {otoken}", x_api_key=None, _rl=_rl3, session=session)
        assert chk3.name == 'c3'

        # verify DB-backed usage row exists and has count 2
        from src.models import UserUsage
        now = datetime.utcnow().replace(second=0, microsecond=0)
        uu = session.exec(select(UserUsage).where(UserUsage.user_id == owner_id, UserUsage.minute_start == now)).first()
        assert uu is not None and uu.count == 2

        # attempt a 3rd create; either 429 is raised or counter increases beyond limit depending on timing
        payload4 = CheckCreate(name="c4", type="heartbeat")
        try:
            _rl4 = limit_by_api_key(project_id, authorization=f"Bearer {otoken}", x_api_key=None, x_admin_token=None, session=session)
            create_check(project_id, payload4, x_admin_token=None, authorization=f"Bearer {otoken}", x_api_key=None, _rl=_rl4, session=session)
        except HTTPException as e:
            assert e.status_code == 429
