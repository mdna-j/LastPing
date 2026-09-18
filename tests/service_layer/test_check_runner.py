"""Tests for CheckRunner."""

from unittest.mock import AsyncMock

import pytest

from engine.checkers.http_checker import HttpChecker
from engine.models import CheckOutcome
from persistence.enums import MonitorType, ServiceStatus
from persistence.models import CheckResult, Service
from persistence.repositories.check_result_repository import (
    CheckResultRepository,
)
from persistence.repositories.service_repository import ServiceRepository
from service_layer.check_runner import CheckRunner


@pytest.mark.asyncio
async def test_check_runner_persists_successful_result() -> None:
    """A successful HTTP check should be persisted and update service state."""

    checker = AsyncMock(spec=HttpChecker)
    result_repository = AsyncMock(spec=CheckResultRepository)
    service_repository = AsyncMock(spec=ServiceRepository)

    checker.check.return_value = CheckOutcome(
        success=True,
        response_time_ms=125.5,
        status_code=200,
    )

    async def save_result(result: CheckResult) -> CheckResult:
        return result

    result_repository.create.side_effect = save_result

    runner = CheckRunner(
        http_checker=checker,
        check_result_repository=result_repository,
        service_repository=service_repository,
    )

    service = Service(
        name="Test Website",
        type=MonitorType.HTTPS,
        target="https://example.com",
        timeout_seconds=10,
    )

    result = await runner.run(service)

    assert result.service_id == service.id
    assert result.success is True
    assert result.status_code == 200
    assert result.response_time_ms == 125.5

    assert service.current_status == ServiceStatus.HEALTHY
    assert service.consecutive_failures == 0
    assert service.last_check_at is not None
    assert service.last_success_at is not None

    checker.check.assert_awaited_once_with(
        target="https://example.com",
        timeout_seconds=10,
    )

    result_repository.create.assert_awaited_once()
    service_repository.update.assert_awaited_once_with(service)


@pytest.mark.asyncio
async def test_check_runner_updates_failed_service_state() -> None:
    """A failed HTTP check should update the service health state."""

    checker = AsyncMock(spec=HttpChecker)
    result_repository = AsyncMock(spec=CheckResultRepository)
    service_repository = AsyncMock(spec=ServiceRepository)

    checker.check.return_value = CheckOutcome(
        success=False,
        response_time_ms=250.0,
        status_code=503,
        failure_type="http_status",
        message="HTTP request returned status 503",
    )

    async def save_result(result: CheckResult) -> CheckResult:
        return result

    result_repository.create.side_effect = save_result

    runner = CheckRunner(
        http_checker=checker,
        check_result_repository=result_repository,
        service_repository=service_repository,
    )

    service = Service(
        name="Failing Website",
        type=MonitorType.HTTPS,
        target="https://example.com",
    )

    result = await runner.run(service)

    assert result.success is False
    assert result.status_code == 503

    assert service.current_status == ServiceStatus.DOWN
    assert service.consecutive_failures == 1
    assert service.last_check_at is not None
    assert service.last_success_at is None

    result_repository.create.assert_awaited_once()
    service_repository.update.assert_awaited_once_with(service)


@pytest.mark.asyncio
async def test_check_runner_rejects_unsupported_monitor_type() -> None:
    """Unsupported monitor types should not use the HTTP checker."""

    checker = AsyncMock(spec=HttpChecker)
    result_repository = AsyncMock(spec=CheckResultRepository)
    service_repository = AsyncMock(spec=ServiceRepository)

    runner = CheckRunner(
        http_checker=checker,
        check_result_repository=result_repository,
        service_repository=service_repository,
    )

    service = Service(
        name="TCP Service",
        type=MonitorType.TCP,
        target="example.com:443",
    )

    with pytest.raises(ValueError, match="Unsupported monitor type"):
        await runner.run(service)

    checker.check.assert_not_awaited()
    result_repository.create.assert_not_awaited()
    service_repository.update.assert_not_awaited()
