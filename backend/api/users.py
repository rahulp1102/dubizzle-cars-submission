"""FastAPI routes for user profiles, long-term preferences, search history, and favourites."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Path, Query, Response, status

from backend.api.deps import get_lead_service, get_memory_service, get_user_service
from backend.memory.service import MemoryService
from backend.models.entities import ListingSummary, Preference
from backend.models.schemas import (
    AddFavouriteRequest,
    CreateUserRequest,
    DeleteResponse,
    FavouriteResponse,
    PreferenceListResponse,
    ReturningSummaryResponse,
    SavePreferenceRequest,
    SearchHistoryResponse,
    UserResponse,
    UserSnapshotResponse,
)
from backend.services.leads import LeadService, mask_booking_for_buyer
from backend.services.users import UserService

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_or_get_user(
    payload: CreateUserRequest,
    response: Response,
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    """Create a new user or return existing profile (201 on create, 200 on existing)."""
    profile, created = service.create_or_get_user(payload.user_id, payload.name)
    if not created:
        response.status_code = status.HTTP_200_OK
    return profile


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: str = Path(min_length=1),
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    """Fetch user profile, remembered preferences, and counts."""
    return service.get_user_profile(user_id)


@router.delete("/{user_id}", response_model=DeleteResponse)
def delete_user(
    user_id: str = Path(min_length=1),
    confirm: bool = Query(default=False, description="Must be true to confirm deletion."),
    service: UserService = Depends(get_user_service),
) -> DeleteResponse:
    """Forget Me: remove user and all associated data after explicit confirmation."""
    service.delete_user(user_id, confirm=confirm)
    return DeleteResponse(deleted=True, id=user_id)


@router.get("/{user_id}/preferences", response_model=PreferenceListResponse)
def list_preferences(
    user_id: str = Path(min_length=1),
    memory: MemoryService = Depends(get_memory_service),
) -> PreferenceListResponse:
    """Return user's stored preferences with provenance and change history."""
    return PreferenceListResponse(
        user_id=user_id,
        preferences=memory.get_preferences(user_id),
        history=memory.get_preference_history(user_id),
    )


@router.put("/{user_id}/preferences", response_model=Preference)
def upsert_preference(
    payload: SavePreferenceRequest,
    user_id: str = Path(min_length=1),
    memory: MemoryService = Depends(get_memory_service),
    leads: LeadService = Depends(get_lead_service),
) -> Preference:
    """Save or update an explicit user preference (e.g. from UI sidebar)."""
    pref = memory.save_preference(
        user_id=user_id,
        preference_key=payload.preference_key,
        preference_value=payload.preference_value,
        original_wording=payload.original_wording,
        source_type=payload.source_type,
        amount=payload.amount,
        currency=payload.currency,
    )
    leads.sync_user_lead(user_id)
    return pref


@router.delete("/{user_id}/preferences/{preference_key}", response_model=DeleteResponse)
def delete_preference(
    user_id: str = Path(min_length=1),
    preference_key: str = Path(min_length=1),
    memory: MemoryService = Depends(get_memory_service),
) -> DeleteResponse:
    """Delete a stored preference for a user."""
    memory.delete_preference(user_id, preference_key)
    return DeleteResponse(deleted=True, id=preference_key)


@router.get("/{user_id}/searches", response_model=SearchHistoryResponse)
def list_search_history(
    user_id: str = Path(min_length=1),
    limit: int = Query(default=15, ge=1, le=50),
    memory: MemoryService = Depends(get_memory_service),
) -> SearchHistoryResponse:
    """Retrieve a user's persisted search history."""
    return SearchHistoryResponse(
        user_id=user_id,
        searches=memory.get_search_history(user_id, limit=limit),
    )


@router.get("/{user_id}/returning-summary", response_model=ReturningSummaryResponse)
def get_returning_summary(
    user_id: str = Path(min_length=1),
    session_id: Optional[str] = Query(default=None),
    memory: MemoryService = Depends(get_memory_service),
) -> ReturningSummaryResponse:
    """Retrieve grounded returning-user welcome summary from stored memory."""
    return ReturningSummaryResponse(
        summary=memory.build_returning_user_summary(user_id, session_id=session_id)
    )


@router.get("/{user_id}/snapshot", response_model=UserSnapshotResponse)
def get_user_snapshot(
    user_id: str = Path(min_length=1),
    session_id: Optional[str] = Query(default=None),
    name: Optional[str] = Query(default=None),
    search_limit: int = Query(default=10, ge=1, le=50),
    memory: MemoryService = Depends(get_memory_service),
) -> UserSnapshotResponse:
    """Return consolidated profile, session, preferences, searches, favourites, bookings, and hydrated listings."""
    snap = memory.get_user_snapshot(
        user_id,
        session_id=session_id,
        name=name,
        search_limit=search_limit,
    )
    masked_bookings = [
        masked
        for b in snap.bookings
        if (masked := mask_booking_for_buyer(b)) is not None
    ]
    return snap.model_copy(update={"bookings": masked_bookings})


@router.get("/{user_id}/favourites", response_model=list[ListingSummary])
def list_favourites(
    user_id: str = Path(min_length=1),
    service: UserService = Depends(get_user_service),
) -> list[ListingSummary]:
    """List a user's saved favourite vehicles."""
    return service.list_favourites(user_id)


@router.post(
    "/{user_id}/favourites",
    response_model=FavouriteResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_favourite(
    payload: AddFavouriteRequest,
    user_id: str = Path(min_length=1),
    service: UserService = Depends(get_user_service),
    leads: LeadService = Depends(get_lead_service),
) -> FavouriteResponse:
    """Save a listing to user's favourites."""
    res = service.add_favourite(user_id, payload.listing_id)
    leads.sync_user_lead(user_id, listing_id=payload.listing_id)
    return res


@router.delete("/{user_id}/favourites/{listing_id}", response_model=DeleteResponse)
def remove_favourite(
    user_id: str = Path(min_length=1),
    listing_id: int = Path(ge=1),
    service: UserService = Depends(get_user_service),
) -> DeleteResponse:
    """Remove a listing from user's favourites."""
    service.remove_favourite(user_id, listing_id)
    return DeleteResponse(deleted=True, id=str(listing_id))
