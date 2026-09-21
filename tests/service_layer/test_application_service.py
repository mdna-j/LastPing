"""Tests for ApplicationService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.scheduler import MonitoringScheduler
from persistence.enums import MonitorType
from persistence.models import Service
from persistence.repositories.service_repository import ServiceRepository
from service_layer.application_service import ApplicationService


@pytest.mark.asyncio
async def test_start_loads_and_schedules_services() -> None:
    """Existing services should be scheduled when LastPing starts."""

    service_repository = AsyncMock(spec=ServiceRepository)
    scheduler = MagicMock(spec=MonitoringScheduler)

    first_service = Service(
        name="Website One",
        type=MonitorType.HTTPS,
        target="https://example.com",
    )

    second_service = Service(
        name="Website Two",
        type=MonitorType.HTTP,
        target="http://example.org",
    )

    service_repository.list_active.return_value = [
        first_service,
        second_service,
    ]

    application = ApplicationService(
        service_repository=service_repository,
        scheduler=scheduler,
    )

    await application.start()

    service_repository.list_active.assert_awaited_once()

    assert scheduler.schedule_service.call_count == 2

    scheduler.schedule_service.assert_any_call(first_service)
    scheduler.schedule_service.assert_any_call(second_service)

    scheduler.start.assert_called_once()


def test_shutdown_stops_scheduler() -> None:
    """Application shutdown should stop background monitoring."""

    service_repository = AsyncMock(spec=ServiceRepository)
    scheduler = MagicMock(spec=MonitoringScheduler)

    application = ApplicationService(
        service_repository=service_repository,
        scheduler=scheduler,
    )

    application.shutdown()

    scheduler.shutdown.assert_called_once()
