"""Integration tests for CheckResultRepository."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from config.settings import get_settings
from persistence.enums import MonitorType
from persistence.models import CheckResult, Service
from persistence.repositories.check_result_repository import (
    CheckResultRepository,
)

settings = get_settings()
test_database_url = settings.test_database_url

if make_url(test_database_url).database != "lastping_test":
    raise RuntimeError("TEST_DATABASE_URL must point to the lastping_test database.")


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    """Create a clean PostgreSQL database session for each test."""

    engine = create_async_engine(test_database_url)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.drop_all)
        await connection.run_sync(SQLModel.metadata.create_all)

    async with session_factory() as session:
        yield session

    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.drop_all)

    await engine.dispose()


@pytest.mark.asyncio
async def test_check_result_repository(
    session: AsyncSession,
) -> None:
    """Create and retrieve monitoring check results."""

    service = Service(
        name="Test Website",
        type=MonitorType.HTTPS,
        target="https://example.com",
    )

    session.add(service)
    await session.commit()
    await session.refresh(service)

    repository = CheckResultRepository(session)

    check_result = CheckResult(
        service_id=service.id,
        success=True,
        response_time_ms=125.5,
        status_code=200,
    )

    created = await repository.create(check_result)

    assert created.id is not None
    assert created.service_id == service.id
    assert created.success is True
    assert created.status_code == 200

    retrieved = await repository.get_by_id(created.id)

    assert retrieved is not None
    assert retrieved.id == created.id

    results = await repository.list_for_service(service.id)

    assert len(results) == 1
    assert results[0].id == created.id

    latest = await repository.get_latest_for_service(service.id)

    assert latest is not None
    assert latest.id == created.id
