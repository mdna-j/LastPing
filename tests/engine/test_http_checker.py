"""Tests for the HTTP monitoring checker."""

import httpx
import pytest

from engine.checkers.http_checker import HttpChecker


@pytest.mark.asyncio
async def test_http_checker_returns_success_for_200() -> None:
    """A successful HTTP response should produce a successful outcome."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            request=request,
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        checker = HttpChecker(client)

        outcome = await checker.check(
            "https://example.com",
            timeout_seconds=10,
        )

    assert outcome.success is True
    assert outcome.status_code == 200
    assert outcome.failure_type is None
    assert outcome.response_time_ms >= 0


@pytest.mark.asyncio
async def test_http_checker_returns_failure_for_503() -> None:
    """A server error should produce a failed outcome."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=503,
            request=request,
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        checker = HttpChecker(client)

        outcome = await checker.check(
            "https://example.com",
            timeout_seconds=10,
        )

    assert outcome.success is False
    assert outcome.status_code == 503
    assert outcome.failure_type == "http_status"


@pytest.mark.asyncio
async def test_http_checker_handles_timeout() -> None:
    """A timeout should produce a failed outcome."""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            "Request timed out",
            request=request,
        )

    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as client:
        checker = HttpChecker(client)

        outcome = await checker.check(
            "https://example.com",
            timeout_seconds=10,
        )

    assert outcome.success is False
    assert outcome.status_code is None
    assert outcome.failure_type == "timeout"
