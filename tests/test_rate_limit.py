import os
from datetime import datetime

import pytest


def test_api_key_rate_limit(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_rl.sqlite'}"

    from sqlmodel import Session, select
    from src import db as dbmod
    from src.models import Project, ApiKey
    from src.security import generate_api_key, hash_api_key
    from src.deps import limit_by_api_key

    dbmod.create_db_and_tables()

    with Session(dbmod.engine) as session:
        project = Project(name="proj_rl")
        session.add(project)
        session.commit()
        session.refresh(project)

        # create an ApiKey with rate_limit_per_minute=1
        plain = generate_api_key()
        ak = ApiKey(project_id=project.id, key_hash=hash_api_key(plain), rate_limit_per_minute=1)
        session.add(ak)
        session.commit()
        session.refresh(ak)

        # first call should succeed
        res = limit_by_api_key(project.id, authorization=f"Bearer {plain}", x_api_key=None, x_admin_token=None, session=session)
        assert isinstance(res, ApiKey)

        # second call in same minute should raise 429
        with pytest.raises(Exception) as exc:
            limit_by_api_key(project.id, authorization=f"Bearer {plain}", x_api_key=None, x_admin_token=None, session=session)
        # HTTPException has status_code attribute; ensure it's 429
        from fastapi import HTTPException
        assert isinstance(exc.value, HTTPException) and exc.value.status_code == 429
