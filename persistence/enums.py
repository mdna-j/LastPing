# Enumerations used by LastPing's persistence model

from enum import StrEnum


class MonitorType(StrEnum):
    # Supported monitoring protocols
    HTTP = "http"
    TCP = "tcp"
    PING = "ping"
    DNS = "dns"


class ServiceStatus(StrEnum):
    # Possible health statuses for monitored services
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    PAUSED = "paused"
