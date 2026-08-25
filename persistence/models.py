# Database models for LastPing

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel

from persistence.enums import MonitorType, ServiceStatus


def utc_now():
    # Get the current UTC time
    return datetime.now(timezone.utc)


class Service(SQLModel, table=True):
    # A service monitored by LastPing
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
    )

    name: str = Field(
        min_length=1,
        max_length=100,
        index=True,
    )

    type: MonitorType = Field(
        sa_column=Column(
            SAEnum(
                MonitorType,
                name="monitor_type",
                values_callable=lambda enum: [item.value for item in enum],
            ),
            nullable=False,
        )
    )

    target: str = Field(
        min_length=1,
        max_length=2048,
    )

    interval_seconds: int = Field(
        default=60,
        ge=10,
    )

    timeout_seconds: int = Field(
        default=10,
        ge=1,
    )

    retry_count: int = Field(
        default=0,
        ge=0,
    )

    is_paused: bool = False

    current_status: ServiceStatus = Field(
        default=ServiceStatus.UNKNOWN,
        sa_column=Column(
            SAEnum(
                ServiceStatus,
                name="service_status",
                values_callable=lambda enum: [item.value for item in enum],
            ),
            nullable=False,
        ),
    )

    consecutive_failures: int = Field(
        default=0,
        ge=0,
    )

    last_check_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    last_success_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
