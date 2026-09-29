import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.groq import (
    GroqNotConfiguredError,
    GroqRequestError,
    GroqResponseError,
)
from app.groq import get_client as get_groq_client
from app.memory import (
    HindsightNotConfiguredError,
    HindsightRequestError,
    get_client,
)
from app.routes import router as incidents_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    # Both clients are cached, so their HTTP sessions have to be released
    # explicitly rather than left to garbage collection.
    try:
        await get_client().aclose()
    except HindsightNotConfiguredError:
        pass

    try:
        await get_groq_client().close()
    except GroqNotConfiguredError:
        pass


app = FastAPI(
    title="AI Incident Response Agent API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(incidents_router)


@app.exception_handler(HindsightNotConfiguredError)
async def handle_hindsight_not_configured(
    request: Request, exc: HindsightNotConfiguredError
) -> JSONResponse:
    """Credentials are missing, so the call is never attempted."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(HindsightRequestError)
async def handle_hindsight_request_failed(
    request: Request, exc: HindsightRequestError
) -> JSONResponse:
    """Hindsight is reachable in config but the upstream call failed."""
    logger.warning(
        "Hindsight %s failed: %s (status=%s)", exc.operation, exc.error_type, exc.status
    )
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(GroqNotConfiguredError)
async def handle_groq_not_configured(
    request: Request, exc: GroqNotConfiguredError
) -> JSONResponse:
    """No API key, so the model is never called."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(GroqRequestError)
async def handle_groq_request_failed(
    request: Request, exc: GroqRequestError
) -> JSONResponse:
    """Groq is configured but the upstream call failed."""
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.exception_handler(GroqResponseError)
async def handle_groq_bad_response(
    request: Request, exc: GroqResponseError
) -> JSONResponse:
    """Groq replied, but not with usable analysis."""
    logger.warning("Groq response rejected: %s", str(exc))
    return JSONResponse(status_code=502, content={"detail": str(exc)})


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}