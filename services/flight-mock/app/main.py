"""
Mock airline servicing API.

Stands in for the "Flight Service APIs" in overview.md, which do not exist. It
reads the same PostgreSQL tables the rest of the platform uses, so cancelling a
booking here is visible everywhere else.

Its second job matters as much as its first: the chaos engine can break it on
demand, which is the only way the retry, backoff and remediation machinery built
in later phases can be exercised for real.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

import voiceops_telemetry as telemetry
from voiceops_telemetry import metrics

from app.chaos import ChaosMiddleware
from app.config import settings

telemetry.setup("flight-mock")
from app.db import close_pool, fetch_one, open_pool
from app.routers import admin, bookings, flights, live, refunds


@asynccontextmanager
async def lifespan(app: FastAPI):
    await open_pool()
    yield
    await close_pool()


app = FastAPI(
    title="VoiceOps Flight Service (mock)",
    description="Fake airline backend with an injectable chaos engine.",
    version="0.1.0",
    lifespan=lifespan,
)

telemetry.instrument_fastapi(app)
metrics.install(app)

app.add_middleware(ChaosMiddleware)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Every error leaves this service in one shape:

        {"error": {"code": ..., "message": ..., "retryable": bool}}

    A single envelope means the retry engine has exactly one thing to parse,
    rather than branching on whichever framework produced the failure.
    """
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    retryable = exc.status_code >= 500 or exc.status_code == 429
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "HTTP_" + str(exc.status_code),
                "message": str(exc.detail),
                "retryable": retryable,
            }
        },
    )


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness plus a real database round trip — never chaos-injected."""
    try:
        await fetch_one("SELECT 1 AS ok")
        database = "up"
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        database = f"down: {exc}"

    return {
        "service": "flight-mock",
        "status": "ok" if database == "up" else "degraded",
        "database": database,
    }


app.include_router(flights.router)
app.include_router(bookings.router)
app.include_router(refunds.router)
app.include_router(live.router)
app.include_router(admin.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.flight_mock_port,
        log_level=settings.log_level,
        reload=True,
    )
