import os
from datetime import datetime, timedelta
from secrets import token_urlsafe

from sqlmodel import Session


def test_auth_helpers_reuse_session_cache(tmp_path, monkeypatch):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_deps_cache.sqlite'}"

    from src import db as dbmod
    from src.deps import get_audit_context, get_current_user, require_admin_or_owner, require_project_role
    from src.models import Project, ProjectMembership, User, UserToken
    from src.security import hash_api_key, hash_password

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="deps-cache-project", api_key_hash=hash_api_key("apik"))
        user = User(email="owner@example.com", hashed_password=hash_password("pw"))
        session.add(project)
        session.add(user)
        session.commit()
        session.refresh(project)
        session.refresh(user)

        membership = ProjectMembership(user_id=user.id, project_id=project.id, role="owner")
        token = token_urlsafe(16)
        user_token = UserToken(
            user_id=user.id,
            token=token,
            created_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        session.add(membership)
        session.add(user_token)
        session.commit()

        counts = {"exec": 0, "get": 0}
        session_type = type(session)
        orig_exec = session_type.exec
        orig_get = session_type.get

        def counting_exec(self, *args, **kwargs):
            if self is session:
                counts["exec"] += 1
            return orig_exec(self, *args, **kwargs)

        def counting_get(self, *args, **kwargs):
            if self is session:
                counts["get"] += 1
            return orig_get(self, *args, **kwargs)

        monkeypatch.setattr(session_type, "exec", counting_exec)
        monkeypatch.setattr(session_type, "get", counting_get)

        authorization = f"Bearer {token}"
        current_user = get_current_user(authorization=authorization, session=session)
        assert current_user.id == user.id
        assert counts == {"exec": 1, "get": 1}

        project_obj = require_admin_or_owner(project.id, authorization=authorization, session=session)
        assert project_obj.id == project.id
        assert counts == {"exec": 2, "get": 2}

        membership_obj = require_project_role(project.id, "owner", current_user=current_user, session=session)
        assert membership_obj.role == "owner"
        assert counts == {"exec": 2, "get": 2}

        actor, actor_ip, user_agent = get_audit_context(None, authorization, None, session)
        assert actor == f"user:{user.id}"
        assert actor_ip is None
        assert user_agent is None
        assert counts == {"exec": 2, "get": 2}
