"""ai-dev-platform FastAPI application entry point.

Wires together all middleware, routers, and lifecycle hooks.

Startup sequence:
1. Validate required secrets (warn in development, fatal in production).
2. Initialise the PostgreSQL database (create tables if needed).
3. Initialise distributed tracing (OTLP or no-op).
4. Build the LangGraph orchestration graph and store it on ``app.state``.

Shutdown sequence:
1. Close database connections.
2. Drain and close the Redis message-bus connection.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.middleware.input_sanitizer import InputSanitizerMiddleware
from api.routers import auth, plans, projects
from api.schemas.models import HealthResponse
from config import settings, validate_secrets
from db.connection import close_db, init_db
from messaging.message_bus import close_message_bus
from observability import configure_logging, get_logger, get_metrics, init_tracing
from orchestrator.checkpointer import get_checkpointer
from orchestrator.graph import build_graph

# ---------------------------------------------------------------------------
# Logging — delegate entirely to observability/logger.py
# ---------------------------------------------------------------------------

configure_logging()
logger = get_logger("api.main")

# ---------------------------------------------------------------------------
# CORS origins — read CORS_ORIGINS env var (comma-separated list or "*")
# ---------------------------------------------------------------------------

_raw_cors = os.getenv("CORS_ORIGINS", "")
if _raw_cors.strip():
    _CORS_ORIGINS: list[str] = [o.strip() for o in _raw_cors.split(",") if o.strip()]
elif settings.environment == "development":
    _CORS_ORIGINS = ["*"]
else:
    _CORS_ORIGINS = ["http://localhost:3000"]


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown of shared resources.

    Stores the compiled LangGraph and checkpointer on ``app.state`` so that
    route handlers can access them via ``request.app.state``.
    """
    # ── Startup ─────────────────────────────────────────────────────────────
    logger.info("startup_begin", environment=settings.environment)

    # 1. Validate secrets — fatal in production, warning in development
    try:
        validate_secrets()
    except RuntimeError as exc:
        if settings.environment == "production":
            logger.critical("startup_secrets_invalid", error=str(exc))
            raise
        logger.warning("startup_secrets_warning", error=str(exc))

    # 2. Initialise PostgreSQL (creates tables on first run)
    await init_db()
    logger.info("startup_db_ready")

    # 3. Initialise distributed tracing (no-op if OTel SDK not installed)
    init_tracing("ai-dev-platform")
    logger.info("startup_tracing_ready")

    # 4. Build LangGraph and attach to app state
    checkpointer = get_checkpointer()
    graph = build_graph(checkpointer)
    app.state.graph = graph
    app.state.checkpointer = checkpointer
    logger.info("startup_graph_ready", checkpointer_backend=settings.checkpointer)

    logger.info("startup_complete", cors_origins=_CORS_ORIGINS)

    yield  # ← application runs here

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("shutdown_begin")

    await close_db()
    logger.info("shutdown_db_closed")

    await close_message_bus()
    logger.info("shutdown_message_bus_closed")

    logger.info("shutdown_complete")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ai-dev-platform",
    description=(
        "Multi-agent AI development platform. "
        "Orchestrates LLM agents to plan, implement, test, review, and deliver code."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware — registered in reverse call order (last registered = outermost)
# ---------------------------------------------------------------------------

# CORS — must be outermost to handle preflight OPTIONS requests correctly
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prompt-injection scanner + sanitizer — runs on every POST/PUT/PATCH body
app.add_middleware(InputSanitizerMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(plans.router)

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["health"],
    summary="Service liveness probe",
)
async def health() -> HealthResponse:
    """Return a liveness response.

    No authentication required.  Suitable for use as a Kubernetes liveness
    probe or load-balancer health check.

    Returns:
        :class:`~api.schemas.models.HealthResponse` with ``status="ok"``,
        current UTC timestamp, version string, and active environment.
    """
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
        version="1.0.0",
        environment=settings.environment,
        metrics=get_metrics(),
    )


# ---------------------------------------------------------------------------
# Global exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions.

    Logs the error with structlog and returns a generic 500 so that internal
    details are never exposed to clients.
    """
    logger.exception(
        "unhandled_exception",
        path=str(request.url.path),
        method=request.method,
        error=str(exc),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again later."},
    )
