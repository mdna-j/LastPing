from fastapi import FastAPI

app = FastAPI(title="LastPing API")


@app.get("/")
async def root():
    return {"message": "LastPing is running"}


# Simple health endpoint
@app.get("/health")
async def health():
    return {"status": "ok"}
