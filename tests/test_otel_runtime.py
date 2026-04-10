import io


def test_configure_opentelemetry_supports_otlp_runtime_with_console_exporters(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.internal:4318")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "lastping-otel-test")

    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    from src.otel_runtime import (
        configure_opentelemetry,
        finish_http_span,
        get_otel_runtime_state,
        record_http_metrics,
        shutdown_opentelemetry,
        start_http_span,
    )

    try:
        state = configure_opentelemetry(
            trace_exporter_factory=lambda endpoint, headers, timeout: ConsoleSpanExporter(out=io.StringIO()),
            metric_exporter_factory=lambda endpoint, headers, timeout: ConsoleMetricExporter(out=io.StringIO()),
        )
        assert state["enabled"] is True
        assert state["traces_enabled"] is True
        assert state["metrics_enabled"] is True
        assert state["trace_endpoint"] == "http://collector.internal:4318/v1/traces"
        assert state["metric_endpoint"] == "http://collector.internal:4318/v1/metrics"

        observation = start_http_span(
            "GET",
            "/health",
            {"traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"},
        )
        assert observation is not None
        trace_headers = finish_http_span(observation, 200, 12.5)
        assert trace_headers["trace_id"] == "0123456789abcdef0123456789abcdef"
        assert trace_headers["parent_span_id"] == "0123456789abcdef"

        record_http_metrics("GET", "/health", 200, 12.5)
        runtime_state = get_otel_runtime_state()
        assert runtime_state["service_name"] == "lastping-otel-test"
    finally:
        shutdown_opentelemetry()
