"""Pydantic request/response schemas for the REST API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.models.entities import (
    Booking,
    Lead,
    Listing,
    ListingSummary,
    Preference,
    PreferenceHistoryEntry,
    ReturningUserSummary,
    SearchHistoryEntry,
    Session,
)

# ── Chat ──


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @field_validator("session_id", "user_id", "message")
    @classmethod
    def strip_chat_fields(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank")
        return cleaned

    @field_validator("request_id")
    @classmethod
    def strip_request_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("request_id cannot be blank")
        return cleaned


class ChatResponse(BaseModel):
    reply: str
    vehicle_cards: list[ListingSummary] = Field(default_factory=list)
    session_id: str
    degraded_mode: bool = False
    tools_called: list[str] = Field(default_factory=list)


# ── Inventory ──


class SearchFilters(BaseModel):
    make: Optional[str] = None
    model: Optional[str] = None
    min_year: Optional[int] = Field(default=None, ge=1900, le=2100)
    max_year: Optional[int] = Field(default=None, ge=1900, le=2100)
    min_price: Optional[float] = Field(default=None, gt=0)  # confirmed cash price only
    max_price: Optional[float] = Field(default=None, gt=0)  # confirmed cash price only
    min_mileage: Optional[float] = Field(default=None, ge=0)
    max_mileage: Optional[float] = Field(default=None, ge=0)
    spec_region: Optional[str] = None
    body_type: Optional[str] = None
    transmission: Optional[str] = None
    colour: Optional[str] = None
    warranty: Optional[bool] = None
    free_text: Optional[str] = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "SearchFilters":
        if self.min_year is not None and self.max_year is not None and self.min_year > self.max_year:
            raise ValueError("min_year cannot exceed max_year")
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price cannot exceed max_price")
        if (
            self.min_mileage is not None
            and self.max_mileage is not None
            and self.min_mileage > self.max_mileage
        ):
            raise ValueError("min_mileage cannot exceed max_mileage")
        return self


class SearchResult(BaseModel):
    results: list[ListingSummary]
    total_matches: int  # total matching individual listings (all records preserved)
    distinct_listing_groups: int = 0  # unique ungrouped listings + suspected duplicate groups
    suspected_duplicate_groups: int = 0  # count of suspected near-duplicate groups in results
    related_duplicate_listings: int = 0  # additional group listings sharing similar ad text
    excluded_unknown_price: int = 0  # listings excluded due to unknown confirmed cash price
    excluded_unknown_attributes: dict[str, int] = Field(default_factory=dict)
    filters_applied: SearchFilters
    message: Optional[str] = None


class InventoryDetailResponse(BaseModel):
    listing: Listing


class MakeCountItem(BaseModel):
    make: str
    count: int


# ── Users ──


class CreateUserRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    name: Optional[str] = Field(default=None, max_length=128)

    @field_validator("user_id")
    @classmethod
    def strip_user_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("user_id cannot be blank")
        return cleaned


class UserResponse(BaseModel):
    user_id: str
    name: Optional[str] = None
    preferences: list[Preference] = Field(default_factory=list)
    favourite_count: int = 0
    booking_count: int = 0


class DeleteResponse(BaseModel):
    deleted: bool
    id: str


class SavePreferenceRequest(BaseModel):
    preference_key: str = Field(min_length=1, max_length=64)
    preference_value: str = Field(min_length=1, max_length=500)
    original_wording: Optional[str] = Field(default=None, max_length=500)
    currency: Optional[str] = Field(default=None, max_length=10)
    amount: Optional[float] = Field(default=None, gt=0)
    source_type: str = Field(default="ui", pattern="^(explicit|ui|inferred)$")


class PreferenceListResponse(BaseModel):
    user_id: str
    preferences: list[Preference] = Field(default_factory=list)
    history: list[PreferenceHistoryEntry] = Field(default_factory=list)


class SearchHistoryResponse(BaseModel):
    user_id: str
    searches: list[SearchHistoryEntry] = Field(default_factory=list)


class ReturningSummaryResponse(BaseModel):
    summary: ReturningUserSummary


class UserSnapshotResponse(BaseModel):
    user: UserResponse
    session: Session
    preferences: list[Preference] = Field(default_factory=list)
    preference_history: list[PreferenceHistoryEntry] = Field(default_factory=list)
    favourites: list[ListingSummary] = Field(default_factory=list)
    searches: list[SearchHistoryEntry] = Field(default_factory=list)
    bookings: list[Booking] = Field(default_factory=list)
    returning_summary: ReturningUserSummary
    hydrated_listings: list[ListingSummary] = Field(default_factory=list)
    health: dict[str, Any] = Field(default_factory=dict)


# ── Favourites ──


class AddFavouriteRequest(BaseModel):
    listing_id: int = Field(gt=0)


class FavouriteResponse(BaseModel):
    user_id: str
    listing_id: int
    saved: bool


# ── Sessions ──


class CreateSessionRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    session_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @field_validator("user_id")
    @classmethod
    def strip_session_user_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("user_id cannot be blank")
        return cleaned


class FocusVehicleRequest(BaseModel):
    listing_id: int = Field(gt=0)
    user_id: str = Field(min_length=1, max_length=128)

    @field_validator("user_id")
    @classmethod
    def strip_focus_user_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("user_id cannot be blank")
        return cleaned


class ResolveReferenceRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    reference_text: Optional[str] = None
    ordinal: Optional[int] = Field(default=None, ge=1)
    explicit_listing_id: Optional[int] = Field(default=None, ge=1)
    update_focus: bool = True

    @field_validator("user_id")
    @classmethod
    def strip_resolve_user_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("user_id cannot be blank")
        return cleaned


class SessionSearchRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: Optional[int] = Field(default=None, ge=1, le=100)

    @field_validator("user_id")
    @classmethod
    def strip_session_search_user_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("user_id cannot be blank")
        return cleaned


class SessionResponse(BaseModel):
    session: Session


# ── Bookings ──


class BookingAlternative(BaseModel):
    booking_date: str
    start_time: str
    end_time: str
    display: str


class ProposeBookingRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    listing_id: int = Field(gt=0)
    requested_date: str = Field(min_length=1, max_length=100)
    requested_time: str = Field(min_length=1, max_length=100)
    session_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    customer_name: Optional[str] = Field(default=None, max_length=128)
    customer_phone: Optional[str] = Field(default=None, max_length=32)

    @field_validator("user_id", "requested_date", "requested_time")
    @classmethod
    def strip_booking_fields(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field cannot be blank")
        return cleaned


class ProposeBookingResponse(BaseModel):
    success: bool
    booking: Optional[Booking] = None
    message: str
    alternatives: list[BookingAlternative] = Field(default_factory=list)


class ConfirmBookingRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    confirm: bool = False
    confirmation_text: Optional[str] = Field(default=None, max_length=200)
    customer_name: Optional[str] = Field(default=None, max_length=128)
    customer_phone: Optional[str] = Field(default=None, max_length=32)

    @field_validator("user_id")
    @classmethod
    def strip_confirm_uid(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("user_id cannot be blank")
        return cleaned


class ConfirmBookingResponse(BaseModel):
    success: bool
    booking: Optional[Booking] = None
    message: str
    alternatives: list[BookingAlternative] = Field(default_factory=list)


# ── Leads ──


class UpsertLeadRequest(BaseModel):
    user_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    name: Optional[str] = Field(default=None, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=32)
    budget: Optional[float] = Field(default=None, gt=0)
    currency: str = Field(default="AED", max_length=10)
    preferred_makes: Optional[list[str]] = None
    preferred_models: Optional[list[str]] = None
    preferred_body_type: Optional[str] = Field(default=None, max_length=64)
    must_have_features: Optional[list[str]] = None
    purchase_timeline: Optional[str] = Field(default=None, max_length=128)
    financing_interest: Optional[str] = Field(default=None, max_length=128)
    listing_ids: Optional[list[int]] = None
    booking_id: Optional[int] = Field(default=None, gt=0)

    @field_validator("user_id")
    @classmethod
    def strip_lead_uid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("user_id cannot be blank")
        return cleaned


class LeadDetailResponse(BaseModel):
    lead: Lead
    next_question: Optional[str] = None


class LeadResponse(BaseModel):
    leads: list[Lead]
    total: int


# ── Error ──


class ErrorResponse(BaseModel):
    detail: str
    error_code: Optional[str] = None
