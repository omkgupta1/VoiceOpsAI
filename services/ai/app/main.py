"""
VoiceOps AI service.

Owns the voice pipeline: speech in, an LLM choosing and running backend tools,
speech out. Providers for each layer are selected by environment variable and
implement the protocols in `app/providers/base.py`, so swapping a local model for
a hosted one is configuration rather than code.

Phase 3 exposes the text path (`POST /v1/turn`). Phase 4 adds the audio one.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

# Importing the tools module is what registers them.
import app.tools.flight  # noqa: F401
from app.api.routes import router
from app.config import settings
from app.core import persistence
from app.providers.registry import active
from app.tools.base import registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    await persistence.open_pool()
    yield
    await persistence.close_pool()


app = FastAPI(
    title="VoiceOps AI service",
    description="STT, LLM tool orchestration and TTS for voice support.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Same error envelope as the flight service, so callers parse one shape."""
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": str(exc.detail),
                "retryable": exc.status_code >= 500 or exc.status_code == 429,
            }
        },
    )


@app.get("/health", tags=["meta"])
async def health() -> dict:
    try:
        await persistence.ping()
        database = "up"
    except Exception as exc:  # noqa: BLE001 - health reports, never raises
        database = f"down: {exc}"

    return {
        "service": "ai",
        "status": "ok" if database == "up" else "degraded",
        "database": database,
        "providers": active(),
        "tools": sorted(registry.tools),
    }


app.include_router(router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=settings.ai_port,
        log_level=settings.log_level, reload=True,
    )
