"""Integration tests for ServiceRepository."""

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
from persistence.models import Service
from persistence.repositories.service_repository import ServiceRepository

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
async def test_service_repository_lifecycle(
    session: AsyncSession,
) -> None:
    """Create, retrieve, update, delete, and restore a service."""

    repository = ServiceRepository(session)

    service = Service(
        name="LastPing Test Site",
        type=MonitorType.HTTPS,
        target="https://example.com",
    )

    # Create
    created = await repository.create(service)

    assert created.id is not None
    assert created.name == "LastPing Test Site"

    # Retrieve
    retrieved = await repository.get_by_id(created.id)

    assert retrieved is not None
    assert retrieved.id == created.id

    # List
    services = await repository.list_active()

    assert len(services) == 1
    assert services[0].id == created.id

    # Update
    retrieved.name = "Updated Test Site"

    updated = await repository.update(retrieved)

    assert updated.name == "Updated Test Site"

    # Soft delete
    deleted = await repository.soft_delete(created.id)

    assert deleted is not None
    assert deleted.deleted_at is not None

    # Deleted services should not appear normally
    hidden = await repository.get_by_id(created.id)

    assert hidden is None

    services = await repository.list_active()

    assert services == []

    # Restore
    restored = await repository.restore(created.id)

    assert restored is not None
    assert restored.deleted_at is None

    visible_again = await repository.get_by_id(created.id)

    assert visible_again is not None
    assert visible_again.id == created.id
