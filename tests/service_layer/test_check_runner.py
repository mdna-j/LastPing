"""Tests for CheckRunner."""

from unittest.mock import AsyncMock

import pytest

from engine.checkers.http_checker import HttpChecker
from engine.models import CheckOutcome
from persistence.enums import MonitorType
from persistence.models import CheckResult, Service
from persistence.repositories.check_result_repository import (
    CheckResultRepository,
)
from service_layer.check_runner import CheckRunner


@pytest.mark.asyncio
async def test_check_runner_persists_successful_result() -> None:
    """A successful HTTP check should be persisted."""

    checker = AsyncMock(spec=HttpChecker)
    repository = AsyncMock(spec=CheckResultRepository)

    checker.check.return_value = CheckOutcome(
        success=True,
        response_time_ms=125.5,
        status_code=200,
    )

    async def save_result(result: CheckResult) -> CheckResult:
        return result

    repository.create.side_effect = save_result

    runner = CheckRunner(
        http_checker=checker,
        check_result_repository=repository,
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

    checker.check.assert_awaited_once_with(
        target="https://example.com",
        timeout_seconds=10,
    )

    repository.create.assert_awaited_once()


# Since enum also contains TCP, Ping, and DNS, we dont want the current HTTP silently trying to use them
@pytest.mark.asyncio
async def test_check_runner_rejects_unsupported_monitor_type() -> None:
    """Unsupported monitor types should not use the HTTP checker."""

    checker = AsyncMock(spec=HttpChecker)
    repository = AsyncMock(spec=CheckResultRepository)

    runner = CheckRunner(
        http_checker=checker,
        check_result_repository=repository,
    )

    service = Service(
        name="TCP Service",
        type=MonitorType.TCP,
        target="example.com:443",
    )

    with pytest.raises(ValueError, match="Unsupported monitor type"):
        await runner.run(service)

    checker.check.assert_not_awaited()
    repository.create.assert_not_awaited()
