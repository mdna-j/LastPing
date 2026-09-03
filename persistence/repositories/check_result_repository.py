"""Repository for CheckResult persistence operations."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from persistence.models import CheckResult


class CheckResultRepository:
    """Provides database operations for monitoring check results."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, check_result: CheckResult) -> CheckResult:
        """Store a monitoring check result."""
        self.session.add(check_result)
        await self.session.commit()
        await self.session.refresh(check_result)

        return check_result

    async def get_by_id(
        self,
        check_result_id: UUID,
    ) -> CheckResult | None:
        """Retrieve a check result by ID."""
        statement = select(CheckResult).where(CheckResult.id == check_result_id)

        result = await self.session.execute(statement)

        return result.scalar_one_or_none()

    async def list_for_service(
        self,
        service_id: UUID,
        limit: int = 100,
    ) -> list[CheckResult]:
        """Return recent check results for a service."""
        statement = (
            select(CheckResult)
            .where(CheckResult.service_id == service_id)
            .order_by(CheckResult.timestamp.desc())
            .limit(limit)
        )

        result = await self.session.execute(statement)

        return list(result.scalars().all())

    async def get_latest_for_service(
        self,
        service_id: UUID,
    ) -> CheckResult | None:
        """Return the most recent check result for a service."""
        statement = (
            select(CheckResult)
            .where(CheckResult.service_id == service_id)
            .order_by(CheckResult.timestamp.desc())
            .limit(1)
        )

        result = await self.session.execute(statement)

        return result.scalar_one_or_none()
