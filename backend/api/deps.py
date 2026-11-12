"""FastAPI dependency providers injecting services configured for app DB."""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Optional

from fastapi import Request

from backend.agent.llm_client import LLMClient
from backend.agent.orchestrator import AgentOrchestrator
from backend.config import settings
from backend.inventory.search import InventorySearchService
from backend.memory.service import MemoryService
from backend.services.booking import BookingService
from backend.services.exceptions import ForbiddenError, UnauthorizedError
from backend.services.leads import LeadService
from backend.services.references import ReferenceResolutionService
from backend.services.sessions import SessionService
from backend.services.users import UserService


def _resolve_db_path(request: Request) -> Optional[Path | str]:
    return getattr(request.app.state, "db_path", None)


def _resolve_admin_api_key(request: Request) -> str:
    configured = getattr(request.app.state, "admin_api_key", None)
    if configured is None:
        configured = os.getenv("ADMIN_API_KEY", settings.ADMIN_API_KEY)
    return str(configured or "").strip()


def require_admin_access(request: Request) -> None:
    """Enforce header-based administrative API key on privileged routes (fail-closed)."""
    expected = _resolve_admin_api_key(request)
    if not expected or expected == "your-admin-api-key-here":
        raise ForbiddenError(
            "Administrative access is not configured.",
            error_code="admin_not_configured",
        )

    provided = request.headers.get("X-Admin-Key")
    if not provided:
        auth_header = request.headers.get("Authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            provided = token.strip()

    if not provided or not provided.strip():
        raise UnauthorizedError(
            "Administrative credentials are required.",
            error_code="admin_auth_required",
        )

    if not hmac.compare_digest(provided.strip().encode("utf-8"), expected.encode("utf-8")):
        raise ForbiddenError(
            "Invalid administrative credentials.",
            error_code="admin_forbidden",
        )


def get_inventory_service(request: Request) -> InventorySearchService:
    return InventorySearchService(_resolve_db_path(request))


def get_user_service(request: Request) -> UserService:
    return UserService(
        _resolve_db_path(request),
        exports_dir=getattr(request.app.state, "exports_dir", None),
    )


def get_memory_service(request: Request) -> MemoryService:
    return MemoryService(
        _resolve_db_path(request),
        exports_dir=getattr(request.app.state, "exports_dir", None),
    )


def get_session_service(request: Request) -> SessionService:
    return SessionService(_resolve_db_path(request))


def get_reference_service(request: Request) -> ReferenceResolutionService:
    return ReferenceResolutionService(_resolve_db_path(request))


def get_booking_service(request: Request) -> BookingService:
    return BookingService(
        _resolve_db_path(request),
        now_fn=getattr(request.app.state, "now_fn", None),
        exports_dir=getattr(request.app.state, "exports_dir", None),
    )


def get_lead_service(request: Request) -> LeadService:
    return LeadService(
        _resolve_db_path(request),
        exports_dir=getattr(request.app.state, "exports_dir", None),
    )


def get_agent_orchestrator(request: Request) -> AgentOrchestrator:
    llm_client: Optional[LLMClient] = getattr(request.app.state, "llm_client", None)
    return AgentOrchestrator(
        db_path=_resolve_db_path(request),
        llm_client=llm_client,
        now_fn=getattr(request.app.state, "now_fn", None),
        exports_dir=getattr(request.app.state, "exports_dir", None),
    )
