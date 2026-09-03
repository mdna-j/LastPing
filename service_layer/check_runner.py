"""Orchestrates monitoring checks and persistence."""

from engine.checkers.http_checker import HttpChecker
from persistence.enums import MonitorType
from persistence.models import CheckResult, Service
from persistence.repositories.check_result_repository import (
    CheckResultRepository,
)


class CheckRunner:
    """Run a monitoring check and persist its result."""

    def __init__(
        self,
        http_checker: HttpChecker,
        check_result_repository: CheckResultRepository,
    ) -> None:
        self._http_checker = http_checker
        self._check_result_repository = check_result_repository

    async def run(self, service: Service) -> CheckResult:
        """Run a check for a service and save the result."""

        if service.type not in {
            MonitorType.HTTP,
            MonitorType.HTTPS,
        }:
            raise ValueError(f"Unsupported monitor type: {service.type}")

        outcome = await self._http_checker.check(
            target=service.target,
            timeout_seconds=service.timeout_seconds,
        )

        check_result = CheckResult(
            service_id=service.id,
            success=outcome.success,
            response_time_ms=outcome.response_time_ms,
            status_code=outcome.status_code,
            failure_type=outcome.failure_type,
            message=outcome.message,
        )

        return await self._check_result_repository.create(check_result)


# Check Runner -> asks HTTP checker to perform a check -> recieves CheckOutcome -> converts it to CheckResult -> asks repo to save it
