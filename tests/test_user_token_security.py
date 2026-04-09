import os

from fastapi.testclient import TestClient


def test_login_stores_hashed_user_token_and_me_still_works(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_user_token_hashed.sqlite'}"

    from sqlmodel import Session, select

    from src import db as dbmod
    from src.main import app
    from src.models import User, UserToken
    from src.security import hash_password, verify_api_key

    dbmod.create_db_and_tables()
    client = TestClient(app)

    with Session(dbmod.engine) as session:
        user = User(email="token-user@example.com", hashed_password=hash_password("strong-password"))
        session.add(user)
        session.commit()

    login_res = client.post(
        "/users/login",
        json={"email": "token-user@example.com", "password": "strong-password"},
    )
    assert login_res.status_code == 200
    access_token = login_res.json()["access_token"]
    assert access_token

    me_res = client.get("/users/me", headers={"Authorization": f"Bearer {access_token}"})
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "token-user@example.com"

    with Session(dbmod.engine) as session:
        stored = session.exec(select(UserToken)).first()
        assert stored is not None
        assert stored.token != access_token
        assert stored.token_fingerprint is not None
        assert verify_api_key(access_token, stored.token) is True


def test_plaintext_legacy_user_token_is_upgraded_on_access(tmp_path):
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'db_user_token_legacy.sqlite'}"

    from sqlmodel import Session, select

    from src import db as dbmod
    from src.main import app
    from src.models import User, UserToken
    from src.security import fingerprint_token, hash_password

    dbmod.create_db_and_tables()
    client = TestClient(app)

    legacy_token = "legacy-plaintext-token"

    with Session(dbmod.engine) as session:
        user = User(email="legacy-user@example.com", hashed_password=hash_password("strong-password"))
        session.add(user)
        session.commit()
        session.refresh(user)
        session.exec(
            UserToken.__table__.insert().values(
                user_id=user.id,
                token=legacy_token,
                token_fingerprint=None,
            )
        )
        session.commit()

    me_res = client.get("/users/me", headers={"Authorization": f"Bearer {legacy_token}"})
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "legacy-user@example.com"

    with Session(dbmod.engine) as session:
        stored = session.exec(select(UserToken)).first()
        assert stored is not None
        assert stored.token != legacy_token
        assert stored.token_fingerprint == fingerprint_token(legacy_token)
