"""Pydantic entity models for dubizzle AI domain objects."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

# ── Enums ──


class ExtractionMethod(str, Enum):
    REGEX = "regex"
    RULE = "rule"
    GEMINI = "gemini"
    MANUAL = "manual"


class ExtractionStatus(str, Enum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    REJECTED = "rejected"


class ListingLanguage(str, Enum):
    EN = "en"
    AR = "ar"
    MIXED = "mixed"


class BookingStatus(str, Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class LeadStatus(str, Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


# ── Listing entities ──


class ListingAttribute(BaseModel):
    """A single extracted attribute for a listing."""

    attribute_name: str
    attribute_value: Optional[str] = None
    numeric_value: Optional[float] = None
    evidence: Optional[str] = None
    extraction_method: ExtractionMethod = ExtractionMethod.REGEX
    status: ExtractionStatus = ExtractionStatus.CONFIRMED
    confidence: float = 1.0


class Listing(BaseModel):
    """A vehicle listing from the inventory."""

    listing_id: int
    make: str
    model: str
    trim: str
    year: int
    title: str
    description_raw: str
    description_display: str
    photo_url: str
    language: ListingLanguage = ListingLanguage.EN
    duplicate_group: Optional[int] = None
    content_hash: Optional[str] = None
    attributes: list[ListingAttribute] = Field(default_factory=list)


class ListingSummary(BaseModel):
    """Compact listing representation for search results."""

    listing_id: int
    make: str
    model: str
    trim: str
    year: int
    title: str
    photo_url: str
    language: ListingLanguage = ListingLanguage.EN
    duplicate_group: Optional[int] = None
    is_suspected_duplicate: bool = False
    suspected_duplicate_ids: list[int] = Field(default_factory=list)

    # Key extracted attributes (may be None if not available)
    cash_price: Optional[float] = None
    cash_price_status: Optional[ExtractionStatus] = None
    monthly_instalment: Optional[float] = None
    mileage_km: Optional[float] = None
    mileage_km_status: Optional[ExtractionStatus] = None
    spec_region: Optional[str] = None
    warranty: Optional[str] = None
    warranty_status: Optional[ExtractionStatus] = None
    body_type: Optional[str] = None
    body_type_status: Optional[ExtractionStatus] = None
    transmission: Optional[str] = None
    colour: Optional[str] = None

    # Search metadata
    match_reason: Optional[str] = None
    fts_rank: Optional[float] = None


# ── User entities ──


class User(BaseModel):
    user_id: str
    name: Optional[str] = None
    created_at: Optional[datetime] = None


class Preference(BaseModel):
    user_id: Optional[str] = None
    preference_key: str
    preference_value: str
    original_wording: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    source_type: str = "explicit"  # 'explicit', 'ui', 'inferred'
    is_explicit: bool = True
    status: str = "confirmed"  # 'confirmed', 'inferred'
    confidence: float = 1.0
    source_turn: Optional[int] = None
    source_message_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PreferenceHistoryEntry(BaseModel):
    id: Optional[int] = None
    user_id: str
    preference_key: str
    old_value: Optional[str] = None
    new_value: str
    original_wording: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    source_type: str = "explicit"
    source_message_id: Optional[int] = None
    changed_at: Optional[datetime] = None


class SearchHistoryEntry(BaseModel):
    id: Optional[int] = None
    user_id: str
    query_text: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    result_count: int = 0
    listing_ids: list[int] = Field(default_factory=list)
    created_at: Optional[datetime] = None


class ReturningUserSummary(BaseModel):
    is_returning: bool
    user_id: str
    name: Optional[str] = None
    welcome_message: Optional[str] = None
    preferences: list[Preference] = Field(default_factory=list)
    recent_searches: list[SearchHistoryEntry] = Field(default_factory=list)
    favourite_count: int = 0


# ── Session entities ──


class SessionMessage(BaseModel):
    """A persisted conversational turn belonging to a session."""

    id: Optional[int] = None
    session_id: str
    role: str  # 'user', 'assistant', 'system'
    content: str
    vehicle_cards: list[int] = Field(default_factory=list)
    created_at: Optional[datetime] = None


class Session(BaseModel):
    session_id: str
    user_id: str
    last_result_ids: list[int] = Field(default_factory=list)
    current_vehicle: Optional[int] = None
    active_filters: Optional[dict] = None
    booking_draft: Optional[dict] = None
    context_summary: Optional[str] = None
    recent_messages: list[SessionMessage] = Field(default_factory=list)


# ── Booking entities ──


class Booking(BaseModel):
    booking_id: Optional[int] = None
    user_id: str
    session_id: Optional[str] = None
    listing_id: int
    booking_date: str  # YYYY-MM-DD
    start_time: str  # HH:MM
    end_time: str  # HH:MM
    duration_minutes: int = 60
    status: BookingStatus = BookingStatus.PROPOSED
    timezone: str = "Asia/Dubai"
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    created_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None


# ── Lead entities ──


class Lead(BaseModel):
    lead_id: Optional[int] = None
    user_id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    budget: Optional[float] = None
    currency: str = "AED"
    original_budget: Optional[float] = None
    original_currency: str = "AED"
    preferred_makes: Optional[list[str]] = None
    preferred_models: Optional[list[str]] = None
    preferred_body_type: Optional[str] = None
    must_have_features: Optional[list[str]] = None
    purchase_timeline: Optional[str] = None
    financing_interest: Optional[str] = None
    listing_ids: Optional[list[int]] = None
    booking_id: Optional[int] = None
    status: LeadStatus = LeadStatus.COLD
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
