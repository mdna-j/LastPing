from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles

from .db import create_db_and_tables, DATABASE_URL
from .routers.projects import router as projects_router
from .routers.checks import router as checks_router
from .routers.heartbeats import router as heartbeats_router
from .routers.alerts import router as alerts_router
from .routers.admin_apikeys import router as admin_apikeys_router
from .routers.users import router as users_router
from .routers.metrics import router as metrics_router
from .routers.incidents import router as incidents_router, public_router as incidents_public_router
from .routers.ui import router as ui_router
from .routers.webhooks import router as webhooks_router
from .routers.analytics import router as analytics_router
from .routers.oncall import router as oncall_router
from .routers.remediation import router as remediation_router
from .deps import limit_public_requests


app = FastAPI(title="LastPing API")

# Serve static assets (JS/CSS) from ./static
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
def on_startup():
    # Initialize database for local SQLite dev only; migrations handle Postgres.
    if DATABASE_URL.startswith("sqlite"):
        create_db_and_tables()


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
app.include_router(users_router)
app.include_router(metrics_router)
app.include_router(incidents_router)
app.include_router(incidents_public_router)
app.include_router(ui_router)
app.include_router(webhooks_router)
app.include_router(analytics_router)
app.include_router(oncall_router)
app.include_router(remediation_router)
