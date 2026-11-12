"""FastAPI routes for persistent session state, search context, and reference resolution."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Response, status

from backend.api.deps import get_reference_service, get_session_service
from backend.models.schemas import (
    CreateSessionRequest,
    FocusVehicleRequest,
    ResolveReferenceRequest,
    SearchResult,
    SessionResponse,
    SessionSearchRequest,
)
from backend.services.references import ReferenceResolutionResult, ReferenceResolutionService
from backend.services.sessions import SessionService

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: CreateSessionRequest,
    response: Response,
    service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    """Create or resume a user session."""
    session, created = service.create_session(payload.user_id, payload.session_id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return SessionResponse(session=session)


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: str = Path(min_length=1),
    user_id: str = Query(..., min_length=1),
    service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    """Retrieve persisted session state after mandatory ownership verification."""
    return SessionResponse(session=service.get_session(session_id, user_id=user_id))


@router.post("/{session_id}/focus", response_model=SessionResponse)
def focus_vehicle(
    payload: FocusVehicleRequest,
    session_id: str = Path(min_length=1),
    service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    """Set the active focused vehicle in session state."""
    session = service.focus_vehicle(session_id, payload.listing_id, user_id=payload.user_id)
    return SessionResponse(session=session)


@router.post("/{session_id}/search", response_model=SearchResult)
def search_in_session(
    payload: SessionSearchRequest,
    session_id: str = Path(min_length=1),
    service: SessionService = Depends(get_session_service),
) -> SearchResult:
    """Run inventory search and persist ordered result IDs + active filters in session."""
    return service.search_in_session(
        session_id,
        payload.user_id,
        payload.filters,
        limit=payload.limit,
    )


@router.post("/{session_id}/resolve", response_model=ReferenceResolutionResult)
def resolve_reference(
    payload: ResolveReferenceRequest,
    session_id: str = Path(min_length=1),
    service: ReferenceResolutionService = Depends(get_reference_service),
) -> ReferenceResolutionResult:
    """Deterministically resolve a vehicle reference against persisted session state."""
    return service.resolve(
        session_id=session_id,
        user_id=payload.user_id,
        reference_text=payload.reference_text,
        ordinal=payload.ordinal,
        explicit_listing_id=payload.explicit_listing_id,
        update_focus=payload.update_focus,
    )
