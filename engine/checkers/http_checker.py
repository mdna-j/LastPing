"""HTTP and HTTPS monitoring checker."""

from time import perf_counter

import httpx

from engine.models import CheckOutcome


class HttpChecker:
    """Perform HTTP and HTTPS availability checks."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def check(
        self,
        target: str,
        timeout_seconds: int,
    ) -> CheckOutcome:
        """Check an HTTP target and return the outcome."""

        owns_client = self._client is None

        client = self._client or httpx.AsyncClient(
            follow_redirects=True,
        )

        started_at = perf_counter()

        try:
            response = await client.get(
                target,
                timeout=timeout_seconds,
            )

            response_time_ms = (perf_counter() - started_at) * 1000

            success = 200 <= response.status_code < 400

            if success:
                return CheckOutcome(
                    success=True,
                    response_time_ms=response_time_ms,
                    status_code=response.status_code,
                )

            return CheckOutcome(
                success=False,
                response_time_ms=response_time_ms,
                status_code=response.status_code,
                failure_type="http_status",
                message=f"HTTP request returned status {response.status_code}",
            )

        except httpx.TimeoutException:
            response_time_ms = (perf_counter() - started_at) * 1000

            return CheckOutcome(
                success=False,
                response_time_ms=response_time_ms,
                failure_type="timeout",
                message="HTTP request timed out",
            )

        except httpx.RequestError as exc:
            response_time_ms = (perf_counter() - started_at) * 1000

            return CheckOutcome(
                success=False,
                response_time_ms=response_time_ms,
                failure_type="request_error",
                message=str(exc),
            )

        finally:
            if owns_client:
                await client.aclose()


# Flow is: Start timer -> send HTTP request -> |200-399 success| |400/500 failure| |exception failure| -> Calculate response time -> Return CheckOutcome
# Checker knows nothing about PostgresSQL
