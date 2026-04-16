from __future__ import annotations

import importlib.util
import os
from collections.abc import Mapping as AbcMapping
from typing import Callable, Mapping, Optional


_OTEL_AVAILABLE = importlib.util.find_spec("opentelemetry.sdk") is not None

if _OTEL_AVAILABLE:
    from opentelemetry.metrics import Observation
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import SpanKind, Status, StatusCode
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


_trace_provider = None
_metric_provider = None
_trace_exporter = None
_metric_exporter = None
_tracer = None
_meter = None
_request_counter = None
_request_duration = None
_notification_queue_depth_gauge = None
_notification_queue_oldest_pending_age_gauge = None
_notification_queue_retry_rate_gauge = None
_notification_queue_dead_letter_gauge = None
_notification_queue_success_rate_gauge = None
_notification_queue_latency_gauge = None
_notification_queue_metrics: dict[int, dict] = {}
_runtime_state = {
    "enabled": False,
    "available": _OTEL_AVAILABLE,
    "service_name": (os.environ.get("OTEL_SERVICE_NAME") or os.environ.get("LASTPING_SERVICE_NAME") or "lastping-api").strip(),
    "service_namespace": (os.environ.get("OTEL_SERVICE_NAMESPACE") or "lastping").strip(),
    "traces_enabled": False,
    "metrics_enabled": False,
    "trace_endpoint": None,
    "metric_endpoint": None,
    "headers_configured": False,
    "export_interval_ms": None,
    "transport": "otlp/http",
    "error": None,
}


def _parse_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in str(raw or "").split(","):
        part = item.strip()
        if not part or "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            headers[key] = value
    return headers


def _normalize_otlp_endpoint(endpoint: str, suffix: str) -> Optional[str]:
    raw = str(endpoint or "").strip()
    if not raw:
        return None
    if raw.endswith("/"):
        raw = raw[:-1]
    if raw.endswith(suffix):
        return raw
    if raw.endswith("/v1/traces") or raw.endswith("/v1/metrics"):
        return raw
    return f"{raw}{suffix}"


def _export_timeout_seconds() -> int:
    raw = (os.environ.get("OTEL_EXPORTER_OTLP_TIMEOUT") or "").strip()
    try:
        value = int(raw) if raw else 10
    except ValueError:
        return 10
    return max(value, 1)


def _export_interval_millis() -> int:
    raw = (os.environ.get("OTEL_METRIC_EXPORT_INTERVAL") or "").strip()
    try:
        value = int(raw) if raw else 15000
    except ValueError:
        return 15000
    return max(value, 1000)


def _service_name() -> str:
    return (os.environ.get("OTEL_SERVICE_NAME") or os.environ.get("LASTPING_SERVICE_NAME") or "lastping-api").strip()


def _service_namespace() -> str:
    return (os.environ.get("OTEL_SERVICE_NAMESPACE") or "lastping").strip()


def _resource() -> "Resource":
    return Resource.create(
        {
            "service.name": _service_name(),
            "service.namespace": _service_namespace(),
            "deployment.environment": (os.environ.get("ENV") or os.environ.get("LASTPING_ENV") or "development").strip(),
        }
    )


def _base_headers() -> dict[str, str]:
    return _parse_headers(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS") or "")


def _trace_headers() -> dict[str, str]:
    headers = _base_headers()
    headers.update(_parse_headers(os.environ.get("OTEL_EXPORTER_OTLP_TRACES_HEADERS") or ""))
    return headers


def _metric_headers() -> dict[str, str]:
    headers = _base_headers()
    headers.update(_parse_headers(os.environ.get("OTEL_EXPORTER_OTLP_METRICS_HEADERS") or ""))
    return headers


def _desired_trace_endpoint() -> Optional[str]:
    explicit = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if explicit:
        return _normalize_otlp_endpoint(explicit, "/v1/traces")
    return _normalize_otlp_endpoint(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "", "/v1/traces")


def _desired_metric_endpoint() -> Optional[str]:
    explicit = os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
    if explicit:
        return _normalize_otlp_endpoint(explicit, "/v1/metrics")
    return _normalize_otlp_endpoint(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "", "/v1/metrics")


def get_otel_runtime_state() -> dict:
    return dict(_runtime_state)


def _set_runtime_state(**updates) -> dict:
    global _runtime_state
    next_state = dict(_runtime_state)
    next_state.update(updates)
    _runtime_state = next_state
    return dict(_runtime_state)


def otlp_configured() -> bool:
    return bool(_desired_trace_endpoint() or _desired_metric_endpoint())


def otlp_enabled() -> bool:
    return bool(_runtime_state.get("enabled"))


def _create_trace_exporter(endpoint: str, headers: Mapping[str, str], timeout: int):
    return OTLPSpanExporter(endpoint=endpoint, headers=dict(headers), timeout=timeout)


def _create_metric_exporter(endpoint: str, headers: Mapping[str, str], timeout: int):
    return OTLPMetricExporter(endpoint=endpoint, headers=dict(headers), timeout=timeout)


def _notification_queue_base_attributes(project_id: int, snapshot: Mapping[str, object]) -> dict[str, object]:
    return {
        "project_id": int(project_id),
        "state": str(snapshot.get("state") or "unknown"),
    }


def _observe_notification_queue_depth(_options):
    observations = []
    for project_id, snapshot in sorted(_notification_queue_metrics.items()):
        base = _notification_queue_base_attributes(project_id, snapshot)
        counts = {
            "all": int(snapshot.get("depth") or 0),
            "queued": int(snapshot.get("queued") or 0),
            "retrying": int(snapshot.get("retrying") or 0),
            "processing": int(snapshot.get("processing") or 0),
        }
        for status, value in counts.items():
            observations.append(Observation(value, {**base, "status": status}))
    return observations


def _observe_notification_queue_oldest_pending_age(_options):
    observations = []
    for project_id, snapshot in sorted(_notification_queue_metrics.items()):
        observations.append(
            Observation(
                float(snapshot.get("oldest_pending_seconds") or 0.0),
                _notification_queue_base_attributes(project_id, snapshot),
            )
        )
    return observations


def _observe_notification_queue_retry_rate(_options):
    observations = []
    for project_id, snapshot in sorted(_notification_queue_metrics.items()):
        observations.append(
            Observation(
                float(snapshot.get("retry_rate") or 0.0),
                _notification_queue_base_attributes(project_id, snapshot),
            )
        )
    return observations


def _observe_notification_queue_dead_letters(_options):
    observations = []
    for project_id, snapshot in sorted(_notification_queue_metrics.items()):
        observations.append(
            Observation(
                int(snapshot.get("dead_letters") or 0),
                _notification_queue_base_attributes(project_id, snapshot),
            )
        )
    return observations


def _observe_notification_queue_success_rate(_options):
    observations = []
    for project_id, snapshot in sorted(_notification_queue_metrics.items()):
        base = _notification_queue_base_attributes(project_id, snapshot)
        per_channel = snapshot.get("per_channel_success") or {}
        if not isinstance(per_channel, AbcMapping):
            continue
        for channel, channel_snapshot in sorted(per_channel.items()):
            if not isinstance(channel_snapshot, AbcMapping):
                continue
            observations.append(
                Observation(
                    float(channel_snapshot.get("success_rate") or 0.0),
                    {**base, "channel": str(channel or "unknown")},
                )
            )
    return observations


def _observe_notification_queue_latency(_options):
    observations = []
    for project_id, snapshot in sorted(_notification_queue_metrics.items()):
        base = _notification_queue_base_attributes(project_id, snapshot)
        avg_ms = snapshot.get("avg_delivery_latency_ms")
        p95_ms = snapshot.get("p95_delivery_latency_ms")
        if avg_ms is not None:
            observations.append(Observation(float(avg_ms), {**base, "stat": "avg"}))
        if p95_ms is not None:
            observations.append(Observation(float(p95_ms), {**base, "stat": "p95"}))
    return observations


def record_notification_queue_metrics(project_id: int, snapshot: Mapping[str, object]) -> None:
    _notification_queue_metrics[int(project_id)] = dict(snapshot or {})


def shutdown_opentelemetry() -> None:
    global _trace_provider, _metric_provider, _trace_exporter, _metric_exporter, _tracer, _meter, _request_counter, _request_duration
    global _notification_queue_depth_gauge, _notification_queue_oldest_pending_age_gauge, _notification_queue_retry_rate_gauge
    global _notification_queue_dead_letter_gauge, _notification_queue_success_rate_gauge, _notification_queue_latency_gauge
    for provider in (_metric_provider, _trace_provider):
        if provider is None:
            continue
        try:
            provider.force_flush()
        except Exception:
            pass
        try:
            provider.shutdown()
        except Exception:
            pass
    _trace_provider = None
    _metric_provider = None
    _trace_exporter = None
    _metric_exporter = None
    _tracer = None
    _meter = None
    _request_counter = None
    _request_duration = None
    _notification_queue_depth_gauge = None
    _notification_queue_oldest_pending_age_gauge = None
    _notification_queue_retry_rate_gauge = None
    _notification_queue_dead_letter_gauge = None
    _notification_queue_success_rate_gauge = None
    _notification_queue_latency_gauge = None
    _notification_queue_metrics.clear()
    _set_runtime_state(
        enabled=False,
        traces_enabled=False,
        metrics_enabled=False,
        trace_endpoint=None,
        metric_endpoint=None,
        headers_configured=False,
        export_interval_ms=None,
        error=None,
        service_name=_service_name(),
        service_namespace=_service_namespace(),
    )


def configure_opentelemetry(
    *,
    trace_exporter_factory: Optional[Callable[[str, Mapping[str, str], int], object]] = None,
    metric_exporter_factory: Optional[Callable[[str, Mapping[str, str], int], object]] = None,
) -> dict:
    global _trace_provider, _metric_provider, _trace_exporter, _metric_exporter, _tracer, _meter, _request_counter, _request_duration
    global _notification_queue_depth_gauge, _notification_queue_oldest_pending_age_gauge, _notification_queue_retry_rate_gauge
    global _notification_queue_dead_letter_gauge, _notification_queue_success_rate_gauge, _notification_queue_latency_gauge
    shutdown_opentelemetry()

    trace_endpoint = _desired_trace_endpoint()
    metric_endpoint = _desired_metric_endpoint()
    if not trace_endpoint and not metric_endpoint:
        return _set_runtime_state(
            available=_OTEL_AVAILABLE,
            enabled=False,
            service_name=_service_name(),
            service_namespace=_service_namespace(),
            transport="otlp/http",
        )

    if not _OTEL_AVAILABLE:
        raise RuntimeError("OpenTelemetry OTLP export is configured but the OpenTelemetry SDK packages are not installed")

    timeout = _export_timeout_seconds()
    metric_interval = _export_interval_millis()
    trace_headers = _trace_headers()
    metric_headers = _metric_headers()
    trace_exporter_factory = trace_exporter_factory or _create_trace_exporter
    metric_exporter_factory = metric_exporter_factory or _create_metric_exporter
    resource = _resource()

    if trace_endpoint:
        _trace_exporter = trace_exporter_factory(trace_endpoint, trace_headers, timeout)
        _trace_provider = TracerProvider(resource=resource)
        _trace_provider.add_span_processor(BatchSpanProcessor(_trace_exporter))
        _tracer = _trace_provider.get_tracer("lastping.runtime", "1")

    if metric_endpoint:
        _metric_exporter = metric_exporter_factory(metric_endpoint, metric_headers, timeout)
        reader = PeriodicExportingMetricReader(_metric_exporter, export_interval_millis=metric_interval)
        _metric_provider = MeterProvider(resource=resource, metric_readers=[reader])
        _meter = _metric_provider.get_meter("lastping.runtime", "1")
        _request_counter = _meter.create_counter(
            "lastping.http.server.requests",
            unit="1",
            description="HTTP requests handled by the LastPing API.",
        )
        _request_duration = _meter.create_histogram(
            "lastping.http.server.duration",
            unit="ms",
            description="HTTP request duration for the LastPing API.",
        )
        _notification_queue_depth_gauge = _meter.create_observable_gauge(
            "lastping.notification.queue.depth",
            callbacks=[_observe_notification_queue_depth],
            unit="1",
            description="Notification queue depth by project and queue status.",
        )
        _notification_queue_oldest_pending_age_gauge = _meter.create_observable_gauge(
            "lastping.notification.queue.oldest_pending_age",
            callbacks=[_observe_notification_queue_oldest_pending_age],
            unit="s",
            description="Age in seconds of the oldest pending notification delivery.",
        )
        _notification_queue_retry_rate_gauge = _meter.create_observable_gauge(
            "lastping.notification.queue.retry_rate",
            callbacks=[_observe_notification_queue_retry_rate],
            unit="1",
            description="Recent notification delivery retry rate.",
        )
        _notification_queue_dead_letter_gauge = _meter.create_observable_gauge(
            "lastping.notification.queue.dead_letters",
            callbacks=[_observe_notification_queue_dead_letters],
            unit="1",
            description="Recent notification deliveries moved to dead-letter state.",
        )
        _notification_queue_success_rate_gauge = _meter.create_observable_gauge(
            "lastping.notification.queue.channel_success_rate",
            callbacks=[_observe_notification_queue_success_rate],
            unit="1",
            description="Per-channel recent notification delivery success rate.",
        )
        _notification_queue_latency_gauge = _meter.create_observable_gauge(
            "lastping.notification.queue.delivery_latency",
            callbacks=[_observe_notification_queue_latency],
            unit="ms",
            description="Recent delivered notification latency by project.",
        )

    return _set_runtime_state(
        available=True,
        enabled=True,
        service_name=_service_name(),
        service_namespace=_service_namespace(),
        traces_enabled=bool(trace_endpoint),
        metrics_enabled=bool(metric_endpoint),
        trace_endpoint=trace_endpoint,
        metric_endpoint=metric_endpoint,
        headers_configured=bool(trace_headers or metric_headers),
        export_interval_ms=metric_interval if metric_endpoint else None,
        transport="otlp/http",
        error=None,
    )


def start_http_span(method: str, path: str, headers: Mapping[str, str]):
    if _tracer is None or not _OTEL_AVAILABLE:
        return None
    carrier = {str(key): str(value) for key, value in headers.items()}
    context = TraceContextTextMapPropagator().extract(carrier=carrier)
    context_manager = _tracer.start_as_current_span(f"{method} {path}", context=context, kind=SpanKind.SERVER)
    span = context_manager.__enter__()
    span.set_attribute("http.method", method)
    span.set_attribute("http.route", path)
    span_context = span.get_span_context()
    parent_context = span.parent
    trace_flags = getattr(span_context.trace_flags, "sampled", False)
    return {
        "context_manager": context_manager,
        "span": span,
        "trace_id": format(span_context.trace_id, "032x"),
        "span_id": format(span_context.span_id, "016x"),
        "parent_span_id": format(parent_context.span_id, "016x") if parent_context is not None else None,
        "trace_flags": "01" if trace_flags else "00",
    }


def finish_http_span(observation, status_code: int, duration_ms: float, error: Exception | None = None):
    if observation is None:
        return None
    span = observation["span"]
    span.set_attribute("http.status_code", int(status_code))
    span.set_attribute("lastping.duration_ms", max(float(duration_ms), 0.0))
    exc_info = (None, None, None)
    if error is not None:
        span.record_exception(error)
        span.set_status(Status(StatusCode.ERROR, str(error)))
        exc_info = (type(error), error, error.__traceback__)
    elif int(status_code) >= 500:
        span.set_status(Status(StatusCode.ERROR))
    else:
        span.set_status(Status(StatusCode.OK))
    observation["context_manager"].__exit__(*exc_info)
    return {
        "trace_id": observation["trace_id"],
        "span_id": observation["span_id"],
        "parent_span_id": observation["parent_span_id"],
        "trace_flags": observation["trace_flags"],
        "traceparent": f'00-{observation["trace_id"]}-{observation["span_id"]}-{observation["trace_flags"]}',
    }


def record_http_metrics(method: str, path: str, status_code: int, duration_ms: float) -> None:
    if _request_counter is None or _request_duration is None:
        return
    attributes = {
        "http.method": method,
        "http.route": path,
        "http.status_code": int(status_code),
    }
    _request_counter.add(1, attributes=attributes)
    _request_duration.record(max(float(duration_ms), 0.0), attributes=attributes)
