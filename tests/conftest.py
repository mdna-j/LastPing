import os
import sys

import pytest


@pytest.fixture(autouse=True)
def isolate_database_engine():
    yield
    db_module = sys.modules.get("src.db")
    if db_module is not None:
        dispose_engine = getattr(db_module, "dispose_engine", None)
        if callable(dispose_engine):
            dispose_engine()
    runtime_metrics_module = sys.modules.get("src.runtime_metrics")
    if runtime_metrics_module is not None:
        reset_request_metrics = getattr(runtime_metrics_module, "reset_request_metrics", None)
        if callable(reset_request_metrics):
            reset_request_metrics()
    for env_name in (
        "DATABASE_URL",
        "BASE_URL",
        "PAGERDUTY_WEBHOOK_SECRET",
        "JIRA_WEBHOOK_SECRET",
        "ADMIN_TOKEN",
        "OTEL_SERVICE_NAME",
        "LASTPING_SERVICE_NAME",
        "OTEL_SERVICE_NAMESPACE",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
        "OTEL_EXPORTER_OTLP_TIMEOUT",
        "OTEL_METRIC_EXPORT_INTERVAL",
        "ENV",
        "LASTPING_ENV",
    ):
        os.environ.pop(env_name, None)
