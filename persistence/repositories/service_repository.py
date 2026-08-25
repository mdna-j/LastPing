"""Repository for Service persistence operations."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from persistence.models import Service, utc_now


class ServiceRepository:
    """Provides database operations for monitored services."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, service: Service) -> Service:
        """Create a new monitored service."""
        self.session.add(service)
        await self.session.commit()
        await self.session.refresh(service)

        return service

    async def get_by_id(
        self,
        service_id: UUID,
        include_deleted: bool = False,
    ) -> Service | None:
        """Retrieve a service by ID."""
        statement = select(Service).where(Service.id == service_id)

        if not include_deleted:
            statement = statement.where(Service.deleted_at.is_(None))

        result = await self.session.execute(statement)

        return result.scalar_one_or_none()

    async def list_active(self) -> list[Service]:
        """Return all services that have not been deleted."""
        statement = (
            select(Service)
            .where(Service.deleted_at.is_(None))
            .order_by(Service.created_at)
        )

        result = await self.session.execute(statement)

        return list(result.scalars().all())

    async def update(self, service: Service) -> Service:
        """Persist changes to an existing service."""
        service.updated_at = utc_now()

        self.session.add(service)
        await self.session.commit()
        await self.session.refresh(service)

        return service

    async def soft_delete(self, service_id: UUID) -> Service | None:
        """Soft-delete a service while preserving its history."""
        service = await self.get_by_id(service_id)

        if service is None:
            return None

        now = utc_now()
        service.deleted_at = now
        service.updated_at = now

        await self.session.commit()
        await self.session.refresh(service)

        return service

    async def restore(self, service_id: UUID) -> Service | None:
        """Restore a previously soft-deleted service."""
        service = await self.get_by_id(
            service_id,
            include_deleted=True,
        )

        if service is None:
            return None

        service.deleted_at = None
        service.updated_at = utc_now()

        await self.session.commit()
        await self.session.refresh(service)

        return service
