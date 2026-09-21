"""Application-level orchestration for LastPing."""

from engine.scheduler import MonitoringScheduler
from persistence.repositories.service_repository import ServiceRepository


class ApplicationService:
    """Coordinate application startup and shutdown."""

    def __init__(
        self,
        service_repository: ServiceRepository,
        scheduler: MonitoringScheduler,
    ) -> None:
        self._service_repository = service_repository
        self._scheduler = scheduler

    async def start(self) -> None:
        """Load existing services and start monitoring."""

        services = await self._service_repository.list_active()

        for service in services:
            self._scheduler.schedule_service(service)

        self._scheduler.start()

    def shutdown(self) -> None:
        """Stop background monitoring."""

        self._scheduler.shutdown()
