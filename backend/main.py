"""FastAPI application factory, lifecycle ingestion, and global error handling."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from backend.agent.guardrails import redact_sensitive_text
from backend.agent.llm_client import LLMClient
from backend.api.bookings import router as bookings_router
from backend.api.chat import router as chat_router
from backend.api.inventory import router as inventory_router
from backend.api.leads import router as leads_router
from backend.api.sessions import router as sessions_router
from backend.api.users import router as users_router
from backend.config import settings
from backend.db import init_db
from backend.inventory.ingestion import ingest_inventory
from backend.inventory.search import InventorySearchService
from backend.models.schemas import ErrorResponse
from backend.services.exceptions import AppError

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Wire configured LOG_LEVEL into Python logging without exposing third-party HTTP wire logs."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=max(level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("backend").setLevel(level)
    for noisy_logger in ("httpx", "httpcore", "urllib3", "google", "google_genai"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def ensure_database_ready(
    db_path: Optional[Path | str] = None,
    *,
    auto_ingest: bool = True,
) -> int:
    """Initialise schema and idempotently ingest inventory if the database is empty.

    Returns:
        Current listing count in SQLite after initialisation.
    """
    init_db(db_path)
    service = InventorySearchService(db_path)
    count = service.get_inventory_count()
    if count == 0 and auto_ingest:
        report = ingest_inventory(db_path=db_path)
        logger.info("Auto-ingested %d inventory listings on startup.", report.total_rows)
        count = service.get_inventory_count()
    return count


def create_app(
    db_path: Optional[Path | str] = None,
    *,
    auto_ingest: bool = True,
    llm_client: Optional[LLMClient] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
    exports_dir: Optional[Path | str] = None,
    admin_api_key: Optional[str] = None,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    _configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        ensure_database_ready(app.state.db_path, auto_ingest=auto_ingest)
        yield

    app = FastAPI(
        title="dubizzle AI — Used-Car Discovery Assistant",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.db_path = db_path
    app.state.llm_client = llm_client
    app.state.now_fn = now_fn
    app.state.exports_dir = exports_dir
    app.state.admin_api_key = admin_api_key

    # ── Exception Handlers ──

    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        body = ErrorResponse(detail=exc.detail, error_code=exc.error_code)
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        messages = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        body = ErrorResponse(detail=messages, error_code="validation_error")
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(PydanticValidationError)
    async def handle_pydantic_validation(
        _request: Request, exc: PydanticValidationError
    ) -> JSONResponse:
        messages = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        body = ErrorResponse(detail=messages, error_code="validation_error")
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        safe_msg = redact_sensitive_text(str(exc))
        app_admin_key = str(getattr(request.app.state, "admin_api_key", "") or "").strip()
        if app_admin_key:
            safe_msg = safe_msg.replace(app_admin_key, "[REDACTED_API_KEY]")
        logger.error("Unhandled backend error: %s: %s", type(exc).__name__, safe_msg)
        body = ErrorResponse(detail="Internal server error", error_code="internal_error")
        return JSONResponse(status_code=500, content=body.model_dump())

    # ── Routers ──
    app.include_router(chat_router)
    app.include_router(inventory_router)
    app.include_router(users_router)
    app.include_router(sessions_router)
    app.include_router(bookings_router)
    app.include_router(leads_router)

    @app.get("/health")
    def health(request: Request) -> dict[str, object]:
        """Basic health, degraded-mode status, and inventory count."""
        service = InventorySearchService(getattr(request.app.state, "db_path", None))
        return {
            "status": "ok",
            "app_env": settings.APP_ENV,
            "gemini_configured": settings.gemini_available,
            "inventory_count": service.get_inventory_count(),
        }

    return app
