"""Models produced by the monitoring engine."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """Outcome produced by a monitoring check."""

    success: bool
    response_time_ms: float
    status_code: int | None = None
    failure_type: str | None = None
    message: str | None = None


# CheckOutcome -> what did the monitoring engine observe
# CheckResult -> what did we permantently record in the database

# Eventually it'll be  HTTPChecker -> CheckOutcome -> Monitoring Service -> CheckResult -> CheckResultRepository
# Keeps every layer separated
