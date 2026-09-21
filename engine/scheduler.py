"""Scheduling for LastPing monitoring jobs."""

from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from engine.checkers.http_checker import HttpChecker
from persistence.database import AsyncSessionLocal
from persistence.models import Service
from persistence.repositories.check_result_repository import (
    CheckResultRepository,
)
from persistence.repositories.service_repository import ServiceRepository
from service_layer.check_runner import CheckRunner


class MonitoringScheduler:
    """Schedule recurring monitoring checks."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        """Start processing scheduled monitoring jobs."""
        if not self._scheduler.running:
            self._scheduler.start()

    def shutdown(self) -> None:
        """Stop the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def schedule_service(self, service: Service) -> None:
        """Schedule recurring checks for a service."""

        job_id = self._job_id(service.id)

        if service.is_paused or service.deleted_at is not None:
            self.remove_service(service.id)
            return

        self._scheduler.add_job(
            self._run_service_check,
            trigger="interval",
            seconds=service.interval_seconds,
            args=[service.id],
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    def remove_service(self, service_id: UUID) -> None:
        """Remove a service's monitoring job."""

        job_id = self._job_id(service_id)

        if self._scheduler.get_job(job_id) is not None:
            self._scheduler.remove_job(job_id)

    async def _run_service_check(self, service_id: UUID) -> None:
        """Run one scheduled monitoring check."""

        async with AsyncSessionLocal() as session:
            service_repository = ServiceRepository(session)
            check_result_repository = CheckResultRepository(session)

            service = await service_repository.get_by_id(service_id)

            if service is None or service.is_paused:
                return

            checker = HttpChecker()

            runner = CheckRunner(
                http_checker=checker,
                check_result_repository=check_result_repository,
                service_repository=service_repository,
            )

            await runner.run(service)

    @staticmethod
    def _job_id(service_id: UUID) -> str:
        """Return the scheduler job ID for a service."""
        return f"service:{service_id}"
