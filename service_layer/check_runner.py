"""Orchestrates monitoring checks and persistence."""

from engine.checkers.http_checker import HttpChecker
from persistence.enums import MonitorType, ServiceStatus
from persistence.models import CheckResult, Service, utc_now
from persistence.repositories.check_result_repository import (
    CheckResultRepository,
)
from persistence.repositories.service_repository import ServiceRepository


class CheckRunner:
    """Run a monitoring check and persist its result."""

    def __init__(
        self,
        http_checker: HttpChecker,
        check_result_repository: CheckResultRepository,
        service_repository: ServiceRepository,
    ) -> None:
        self._http_checker = http_checker
        self._check_result_repository = check_result_repository
        self._service_repository = service_repository

    async def run(self, service: Service) -> CheckResult:
        """Run a check, save the result, and update service state."""

        if service.type not in {
            MonitorType.HTTP,
            MonitorType.HTTPS,
        }:
            raise ValueError(
                f"Unsupported monitor type: {service.type}"
            )

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

        saved_result = await self._check_result_repository.create(
            check_result
        )

        now = utc_now()

        service.last_check_at = now

        if outcome.success:
            service.current_status = ServiceStatus.HEALTHY
            service.consecutive_failures = 0
            service.last_success_at = now
        else:
            service.current_status = ServiceStatus.DOWN
            service.consecutive_failures += 1

        await self._service_repository.update(service)

        return saved_result

# Check Runner -> asks HTTP checker to perform a check -> recieves CheckOutcome -> converts it to CheckResult -> asks repo to save it
