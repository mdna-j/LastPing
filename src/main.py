from fastapi import FastAPI

from .db import create_db_and_tables
from .routers.projects import router as projects_router
from .routers.checks import router as checks_router
from .routers.heartbeats import router as heartbeats_router


app = FastAPI(title="LastPing API")


@app.on_event("startup")
def on_startup():
    # Initialize database (creates tables if they don't exist)
    create_db_and_tables()


@app.get("/")
async def root():
    return {"message": "LastPing is running"}


# Simple health endpoint
@app.get("/health")
async def health():
    return {"status": "ok"}


# routers
app.include_router(projects_router)
app.include_router(checks_router)
app.include_router(heartbeats_router)
