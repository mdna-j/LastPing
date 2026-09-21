"""Tests for the monitoring scheduler."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from engine.scheduler import MonitoringScheduler
from persistence.enums import MonitorType
from persistence.models import Service


def make_service(**overrides) -> Service:
    """Create a service for scheduler tests."""

    values = {
        "name": "Test Website",
        "type": MonitorType.HTTPS,
        "target": "https://example.com",
        "interval_seconds": 60,
    }

    values.update(overrides)

    return Service(**values)


def test_schedule_service_adds_interval_job() -> None:
    """An active service should receive a recurring scheduler job."""

    scheduler = MonitoringScheduler()
    mock_scheduler = MagicMock()
    scheduler._scheduler = mock_scheduler

    service = make_service()

    scheduler.schedule_service(service)

    mock_scheduler.add_job.assert_called_once()

    _, kwargs = mock_scheduler.add_job.call_args

    assert kwargs["trigger"] == "interval"
    assert kwargs["seconds"] == 60
    assert kwargs["args"] == [service.id]
    assert kwargs["id"] == f"service:{service.id}"
    assert kwargs["replace_existing"] is True
    assert kwargs["max_instances"] == 1
    assert kwargs["coalesce"] is True


def test_paused_service_is_not_scheduled() -> None:
    """A paused service should not receive a monitoring job."""

    scheduler = MonitoringScheduler()
    mock_scheduler = MagicMock()
    scheduler._scheduler = mock_scheduler

    service = make_service(is_paused=True)

    scheduler.schedule_service(service)

    mock_scheduler.add_job.assert_not_called()
    mock_scheduler.get_job.assert_called_once_with(f"service:{service.id}")


def test_deleted_service_is_not_scheduled() -> None:
    """A soft-deleted service should not receive a monitoring job."""

    scheduler = MonitoringScheduler()
    mock_scheduler = MagicMock()
    scheduler._scheduler = mock_scheduler

    service = make_service(
        deleted_at=datetime.now(timezone.utc),
    )

    scheduler.schedule_service(service)

    mock_scheduler.add_job.assert_not_called()
    mock_scheduler.get_job.assert_called_once_with(f"service:{service.id}")


def test_rescheduling_service_uses_same_job_id() -> None:
    """Scheduling a service again should replace its existing job."""

    scheduler = MonitoringScheduler()
    mock_scheduler = MagicMock()
    scheduler._scheduler = mock_scheduler

    service = make_service()

    scheduler.schedule_service(service)
    scheduler.schedule_service(service)

    assert mock_scheduler.add_job.call_count == 2

    first_call = mock_scheduler.add_job.call_args_list[0]
    second_call = mock_scheduler.add_job.call_args_list[1]

    assert first_call.kwargs["id"] == f"service:{service.id}"
    assert second_call.kwargs["id"] == f"service:{service.id}"

    assert first_call.kwargs["replace_existing"] is True
    assert second_call.kwargs["replace_existing"] is True


def test_remove_service_removes_existing_job() -> None:
    """Removing a scheduled service should remove its job."""

    scheduler = MonitoringScheduler()
    mock_scheduler = MagicMock()
    scheduler._scheduler = mock_scheduler

    service = make_service()

    mock_scheduler.get_job.return_value = object()

    scheduler.remove_service(service.id)

    job_id = f"service:{service.id}"

    mock_scheduler.get_job.assert_called_once_with(job_id)
    mock_scheduler.remove_job.assert_called_once_with(job_id)
