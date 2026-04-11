import time
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles

from . import db as dbmod
from .db import create_db_and_tables
from .otel_runtime import configure_opentelemetry, finish_http_span, record_http_metrics, shutdown_opentelemetry, start_http_span
from .runtime_metrics import format_traceparent, generate_trace_context, record_request, record_trace
from .security_ops import audit_http_exception
from .routers.projects import router as projects_router
from .routers.checks import router as checks_router
from .routers.heartbeats import router as heartbeats_router
from .routers.alerts import router as alerts_router
from .routers.admin_apikeys import router as admin_apikeys_router
from .routers.admin_security import router as admin_security_router
from .routers.users import router as users_router
from .routers.metrics import router as metrics_router
from .routers.incidents import router as incidents_router, public_router as incidents_public_router
from .routers.ui import router as ui_router
from .routers.webhooks import router as webhooks_router
from .routers.pagerduty import router as pagerduty_router
from .routers.jira_webhook import router as jira_webhook_router
from .routers.analytics import router as analytics_router
from .routers.oncall import router as oncall_router
from .routers.orgs import router as orgs_router
from .routers.observability import router as observability_router
from .routers.remediation import router as remediation_router
from .deps import limit_public_requests


app = FastAPI(title="LastPing API")

# Serve static assets (JS/CSS) from ./static
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(HTTPException)
async def record_security_http_exception(request: Request, exc: HTTPException):
    audit_http_exception(request, exc)
    return await http_exception_handler(request, exc)


@app.middleware("http")
async def track_request_latency(request: Request, call_next):
    started = time.perf_counter()
    started_at = datetime.utcnow()
    otel_observation = start_http_span(request.method, request.url.path, request.headers)
    if otel_observation is not None:
        trace_context = {
            "trace_id": otel_observation["trace_id"],
            "span_id": otel_observation["span_id"],
            "parent_span_id": otel_observation["parent_span_id"],
            "trace_flags": otel_observation["trace_flags"],
        }
    else:
        trace_context = generate_trace_context(request.headers.get("traceparent"))
    request.state.trace_id = trace_context["trace_id"]
    request.state.span_id = trace_context["span_id"]
    request.state.parent_span_id = trace_context["parent_span_id"]
    request.state.traceparent = format_traceparent(trace_context["trace_id"], trace_context["span_id"], trace_context["trace_flags"])
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000.0
        record_request(request.url.path, request.method, 500, duration_ms)
        record_http_metrics(request.method, request.url.path, 500, duration_ms)
        record_trace(
            request.url.path,
            request.method,
            500,
            duration_ms,
            trace_id=trace_context["trace_id"],
            span_id=trace_context["span_id"],
            parent_span_id=trace_context["parent_span_id"],
            started_at=started_at,
        )
        finish_http_span(otel_observation, 500, duration_ms, error=exc)
        raise
    duration_ms = (time.perf_counter() - started) * 1000.0
    record_request(request.url.path, request.method, response.status_code, duration_ms)
    record_http_metrics(request.method, request.url.path, response.status_code, duration_ms)
    record_trace(
        request.url.path,
        request.method,
        response.status_code,
        duration_ms,
        trace_id=trace_context["trace_id"],
        span_id=trace_context["span_id"],
        parent_span_id=trace_context["parent_span_id"],
        started_at=started_at,
    )
    otel_trace = finish_http_span(otel_observation, response.status_code, duration_ms)
    if otel_trace is not None:
        request.state.traceparent = otel_trace["traceparent"]
        request.state.trace_id = otel_trace["trace_id"]
    response.headers["traceparent"] = request.state.traceparent
    response.headers["X-Trace-Id"] = trace_context["trace_id"]
    return response


@app.on_event("startup")
def on_startup():
    configure_opentelemetry()
    # Initialize database for local SQLite dev only; migrations handle Postgres.
    if dbmod.get_database_url().startswith("sqlite"):
        create_db_and_tables()


@app.on_event("shutdown")
def on_shutdown():
    shutdown_opentelemetry()


@app.get("/", dependencies=[Depends(limit_public_requests)])
async def root():
    return {"message": "LastPing is running"}


# Simple health endpoint
@app.get("/health", dependencies=[Depends(limit_public_requests)])
async def health():
    return {"status": "ok"}


# routers
app.include_router(projects_router)
app.include_router(checks_router)
app.include_router(heartbeats_router)
app.include_router(alerts_router)
app.include_router(admin_apikeys_router)
app.include_router(admin_security_router)
app.include_router(users_router)
app.include_router(metrics_router)
app.include_router(incidents_router)
app.include_router(incidents_public_router)
app.include_router(ui_router)
app.include_router(webhooks_router)
app.include_router(pagerduty_router)
app.include_router(jira_webhook_router)
app.include_router(analytics_router)
app.include_router(oncall_router)
app.include_router(orgs_router)
app.include_router(observability_router)
app.include_router(remediation_router)
