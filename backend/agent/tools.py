"""Pydantic-validated agent tools wrapping backend services (no direct SQL)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

from backend.agent.llm_client import ToolSchema
from backend.inventory.search import InventorySearchService
from backend.memory.service import MemoryService
from backend.models.entities import Booking, Lead, ListingSummary, Preference
from backend.models.schemas import BookingAlternative, SearchFilters
from backend.services.booking import (
    BookingService,
    contains_vague_time_phrase,
    has_explicit_clock_time,
)
from backend.services.exceptions import AppError
from backend.services.leads import LeadService, mask_phone
from backend.services.references import (
    ReferenceResolutionService,
    ResolutionStatus,
    has_relative_reference,
)
from backend.services.sessions import SessionService
from backend.services.users import UserService

# ── Pydantic Tool Argument Schemas ──


class SearchInventoryArgs(BaseModel):
    make: Optional[str] = Field(default=None, description="Vehicle make, e.g. 'mercedes-benz', 'honda', 'audi'")
    model: Optional[str] = Field(default=None, description="Vehicle model, e.g. 'glc-class', 'cr-v', 'q8'")
    min_year: Optional[int] = Field(default=None, ge=1900, le=2100, description="Minimum model year")
    max_year: Optional[int] = Field(default=None, ge=1900, le=2100, description="Maximum model year")
    min_price: Optional[float] = Field(
        default=None, gt=0, description="Minimum confirmed cash price in AED"
    )
    max_price: Optional[float] = Field(
        default=None, gt=0, description="Maximum confirmed cash price in AED"
    )
    min_mileage: Optional[float] = Field(default=None, ge=0, description="Minimum mileage in km")
    max_mileage: Optional[float] = Field(default=None, ge=0, description="Maximum mileage in km")
    spec_region: Optional[str] = Field(
        default=None, description="Specification region, e.g. 'GCC', 'Non-GCC', 'US / American', 'Japanese', 'Korean'"
    )
    body_type: Optional[str] = Field(
        default=None, description="Body type, e.g. 'SUV', 'Sedan', 'Coupe', 'Convertible', 'Pickup', 'Van', 'Commercial'"
    )
    transmission: Optional[str] = Field(default=None, description="'Automatic' or 'Manual'")
    colour: Optional[str] = Field(
        default=None, description="Confirmed exterior colour, e.g. 'White', 'Black', 'Silver', 'Blue', 'Red', 'Grey'"
    )
    warranty: Optional[bool] = Field(default=None, description="True for confirmed active warranty only")
    free_text: Optional[str] = Field(default=None, description="Optional keywords for FTS5 ranking")
    limit: int = Field(default=8, ge=1, le=20, description="Maximum listings to return")

    @model_validator(mode="after")
    def validate_ranges(self) -> "SearchInventoryArgs":
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


class GetListingDetailsArgs(BaseModel):
    listing_id: int = Field(gt=0, description="Exact numeric listing ID from search or reference resolution")


class ResolveListingReferenceArgs(BaseModel):
    reference_text: Optional[str] = Field(
        default=None,
        description="User reference phrase such as 'the first one', 'that car', 'the Audi', or 'the one I saved'",
    )
    ordinal: Optional[int] = Field(
        default=None, ge=1, description="1-based position in the last displayed result set"
    )
    explicit_listing_id: Optional[int] = Field(
        default=None, ge=1, description="Explicit listing ID mentioned by the user"
    )


class SaveFavouriteArgs(BaseModel):
    listing_id: Optional[int] = Field(
        default=None, gt=0, description="Listing ID to save (if already resolved)"
    )
    reference_text: Optional[str] = Field(
        default=None,
        description="Reference phrase such as 'this car' or 'the first one' when listing_id is not yet specified",
    )


class SavePreferenceArgs(BaseModel):
    preference_key: str = Field(
        min_length=1,
        max_length=64,
        description="Canonical preference key: 'budget_max', 'preferred_makes', 'preferred_models', 'preferred_body_types', 'preferred_colour', 'spec_region', 'max_mileage_km', 'min_year', 'transmission', 'warranty_required', 'must_have_features', 'excluded_features', or 'user_name'",
    )
    preference_value: str = Field(
        min_length=1,
        max_length=300,
        description="Normalized preference value",
    )
    original_wording: str = Field(
        min_length=1,
        max_length=400,
        description="Exact quote from the user's message expressing this preference",
    )
    amount: Optional[float] = Field(
        default=None, gt=0, description="Optional numeric budget amount when saving budget_max"
    )
    currency: Optional[str] = Field(
        default=None, description="Optional budget currency ('AED' or 'USD')"
    )
    source_type: str = Field(
        default="explicit",
        pattern="^(explicit|inferred)$",
        description="Must be 'explicit' when directly stated by the user, or 'inferred' if implied",
    )


class GetUserPreferencesArgs(BaseModel):
    """Takes no user_id; scoped strictly to active user."""


class GetSearchHistoryArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=25)


class GetSavedVehiclesArgs(BaseModel):
    """Takes no user_id; scoped strictly to active user."""


class GetUserContextArgs(BaseModel):
    """Takes no external user_id; always scoped to the authenticated/active user and session."""


class ProposeBookingArgs(BaseModel):
    listing_id: Optional[int] = Field(
        default=None, gt=0, description="Listing ID to view (if already resolved)"
    )
    reference_text: Optional[str] = Field(
        default=None,
        description="Reference phrase like 'the first one' or 'this car' when listing_id is omitted",
    )
    requested_date: str = Field(
        min_length=1,
        max_length=100,
        description="Requested viewing date (e.g. '2026-11-12', 'tomorrow', 'Saturday')",
    )
    requested_time: str = Field(
        min_length=1,
        max_length=100,
        description="Exact requested start time (e.g. '10:00' or '2:00 PM' between 08:00 and 19:00 Asia/Dubai)",
    )
    customer_name: Optional[str] = Field(default=None, max_length=128)
    customer_phone: Optional[str] = Field(default=None, max_length=32)


class ConfirmBookingArgs(BaseModel):
    booking_id: Optional[int] = Field(
        default=None, gt=0, description="Proposed booking ID to confirm (defaults to session draft)"
    )
    confirm: bool = Field(
        default=False,
        description="Must be true ONLY after the user explicitly confirms booking this exact slot",
    )
    confirmation_text: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Exact user confirmation phrase quoted from the current message",
    )
    customer_name: Optional[str] = Field(default=None, max_length=128)
    customer_phone: Optional[str] = Field(default=None, max_length=32)


class GetBookingDetailsArgs(BaseModel):
    booking_id: int = Field(gt=0, description="Numeric booking ID to inspect")


class GetUserBookingsArgs(BaseModel):
    """Takes no external user_id; scoped strictly to active user."""


class UpdateLeadInfoArgs(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128, description="Customer name")
    phone: Optional[str] = Field(default=None, max_length=32, description="UAE or international E.164 phone number")
    budget: Optional[float] = Field(default=None, gt=0, description="Target numeric budget")
    currency: str = Field(default="AED", max_length=10, description="Budget currency ('AED' or 'USD')")
    preferred_makes: Optional[list[str]] = Field(default=None, description="Preferred car makes")
    preferred_models: Optional[list[str]] = Field(default=None, description="Preferred car models")
    preferred_body_type: Optional[str] = Field(default=None, max_length=64, description="Preferred body type (e.g. 'SUV', 'Sedan')")
    must_have_features: Optional[list[str]] = Field(default=None, description="Must-have features or exterior colour")
    purchase_timeline: Optional[str] = Field(default=None, max_length=128, description="Expected purchase timeline (e.g. 'this month', 'next week')")
    financing_interest: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Payment preference explicitly stated by the user, e.g. 'cash' or 'bank financing'",
    )
    listing_ids: Optional[list[int]] = Field(default=None, description="Specific listing IDs of interest")


# ── Tool Execution Context ──


@dataclass
class ToolContext:
    user_id: str
    session_id: str
    inventory: InventorySearchService
    sessions: SessionService
    references: ReferenceResolutionService
    users: UserService
    memory: MemoryService
    bookings: BookingService
    leads: LeadService
    current_user_message: Optional[str] = None
    search_card_ids: list[int] = field(default_factory=list)
    detail_card_ids: list[int] = field(default_factory=list)
    resolved_this_turn: bool = False
    resolved_listing_ids: list[int] = field(default_factory=list)


def _serialize_summary(item: ListingSummary, position: Optional[int] = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "listing_id": item.listing_id,
        "year": item.year,
        "make": item.make,
        "model": item.model,
        "trim": item.trim,
        "title": item.title,
        "confirmed_cash_price_aed": (
            item.cash_price
            if item.cash_price is not None and item.cash_price_status and item.cash_price_status.value == "confirmed"
            else None
        ),
        "monthly_instalment_aed": item.monthly_instalment,
        "mileage_km": item.mileage_km,
        "spec_region": item.spec_region,
        "warranty": item.warranty,
        "warranty_status": item.warranty_status.value if item.warranty_status else None,
        "body_type": item.body_type,
        "body_type_status": item.body_type_status.value if item.body_type_status else None,
        "transmission": item.transmission,
        "colour": item.colour,
        "is_suspected_duplicate": item.is_suspected_duplicate,
        "duplicate_group": item.duplicate_group,
        "suspected_duplicate_ids": item.suspected_duplicate_ids,
        "match_reason": item.match_reason,
    }
    if position is not None:
        payload["position"] = position
    return payload


def _serialize_preference(pref: Preference) -> dict[str, Any]:
    return {
        "key": pref.preference_key,
        "value": pref.preference_value,
        "original_wording": pref.original_wording,
        "metadata": pref.metadata,
        "source_type": pref.source_type,
        "is_explicit": pref.is_explicit,
        "status": pref.status,
        "source_message_id": pref.source_message_id,
        "updated_at": pref.updated_at.isoformat() if pref.updated_at else None,
    }


def _serialize_booking(booking: Optional[Booking]) -> Optional[dict[str, Any]]:
    if booking is None:
        return None
    return {
        "booking_id": booking.booking_id,
        "user_id": booking.user_id,
        "session_id": booking.session_id,
        "listing_id": booking.listing_id,
        "booking_date": booking.booking_date,
        "start_time": booking.start_time,
        "end_time": booking.end_time,
        "duration_minutes": booking.duration_minutes,
        "status": booking.status.value,
        "timezone": booking.timezone,
        "customer_name": booking.customer_name,
        "customer_phone_masked": mask_phone(booking.customer_phone),
        "created_at": booking.created_at.isoformat() if booking.created_at else None,
        "confirmed_at": booking.confirmed_at.isoformat() if booking.confirmed_at else None,
    }


def _serialize_alternative(alt: BookingAlternative) -> dict[str, Any]:
    return {
        "booking_date": alt.booking_date,
        "start_time": alt.start_time,
        "end_time": alt.end_time,
        "display": alt.display,
    }


def _serialize_lead(lead: Lead) -> dict[str, Any]:
    return {
        "lead_id": lead.lead_id,
        "user_id": lead.user_id,
        "name": lead.name,
        "phone_masked": mask_phone(lead.phone),
        "budget": lead.budget,
        "currency": lead.currency,
        "original_budget": lead.original_budget,
        "original_currency": lead.original_currency,
        "preferred_makes": lead.preferred_makes,
        "preferred_models": lead.preferred_models,
        "preferred_body_type": lead.preferred_body_type,
        "must_have_features": lead.must_have_features,
        "purchase_timeline": lead.purchase_timeline,
        "financing_interest": lead.financing_interest,
        "listing_ids": lead.listing_ids,
        "booking_id": lead.booking_id,
        "status": lead.status.value,
    }


# ── Tool Implementations (Thin Service Wrappers) ──


def _tool_search_inventory(args: SearchInventoryArgs, ctx: ToolContext) -> dict[str, Any]:
    filters = SearchFilters(
        make=args.make,
        model=args.model,
        min_year=args.min_year,
        max_year=args.max_year,
        min_price=args.min_price,
        max_price=args.max_price,
        min_mileage=args.min_mileage,
        max_mileage=args.max_mileage,
        spec_region=args.spec_region,
        body_type=args.body_type,
        transmission=args.transmission,
        colour=args.colour,
        warranty=args.warranty,
        free_text=args.free_text,
    )
    res = ctx.sessions.search_in_session(
        ctx.session_id,
        ctx.user_id,
        filters,
        limit=args.limit,
        query_text=ctx.current_user_message,
    )
    ctx.search_card_ids = [item.listing_id for item in res.results]
    return {
        "total_matches": res.total_matches,
        "distinct_listing_groups": res.distinct_listing_groups,
        "suspected_duplicate_groups": res.suspected_duplicate_groups,
        "related_duplicate_listings": res.related_duplicate_listings,
        "excluded_unknown_price": res.excluded_unknown_price,
        "excluded_unknown_attributes": res.excluded_unknown_attributes,
        "filters_applied": res.filters_applied.model_dump(exclude_none=True),
        "message": res.message,
        "listings": [
            _serialize_summary(item, position=idx + 1)
            for idx, item in enumerate(res.results)
        ],
    }


def _tool_get_listing_details(args: GetListingDetailsArgs, ctx: ToolContext) -> dict[str, Any]:
    target_id = args.listing_id
    if ctx.current_user_message and has_relative_reference(ctx.current_user_message):
        if not ctx.resolved_this_turn:
            res = ctx.references.resolve(
                session_id=ctx.session_id,
                user_id=ctx.user_id,
                reference_text=ctx.current_user_message,
                update_focus=True,
            )
            if res.status != ResolutionStatus.RESOLVED:
                return {
                    "found": False,
                    "needs_clarification": True,
                    "status": res.status.value,
                    "candidate_ids": res.candidate_ids,
                    "clarification_prompt": res.clarification_prompt,
                    "message": res.message,
                }
            ctx.resolved_this_turn = True
            ctx.resolved_listing_ids = [
                item.listing_id for item in res.resolved_listings
            ] or ([res.listing_id] if res.listing_id is not None else [])
        if ctx.resolved_listing_ids:
            if len(ctx.resolved_listing_ids) == 1:
                target_id = ctx.resolved_listing_ids[0]
            elif target_id not in ctx.resolved_listing_ids:
                remaining = [lid for lid in ctx.resolved_listing_ids if lid not in ctx.detail_card_ids]
                target_id = remaining[0] if remaining else ctx.resolved_listing_ids[0]

    listing = ctx.inventory.get_vehicle_details(target_id)
    if listing is None:
        return {"found": False, "listing_id": target_id, "error": f"Listing {target_id} not found."}

    ctx.sessions.focus_vehicle(ctx.session_id, listing.listing_id, user_id=ctx.user_id)
    if listing.listing_id not in ctx.detail_card_ids:
        ctx.detail_card_ids.append(listing.listing_id)

    attrs_dict = {
        a.attribute_name: {
            "value": a.attribute_value,
            "numeric_value": a.numeric_value,
            "status": a.status.value,
            "evidence": a.evidence,
            "extraction_method": a.extraction_method.value,
        }
        for a in listing.attributes
    }
    return {
        "found": True,
        "listing_id": listing.listing_id,
        "year": listing.year,
        "make": listing.make,
        "model": listing.model,
        "trim": listing.trim,
        "title": listing.title,
        "language": listing.language.value,
        "duplicate_group": listing.duplicate_group,
        "description_display": listing.description_display[:1200],
        "attributes": attrs_dict,
    }


def _tool_resolve_listing_reference(
    args: ResolveListingReferenceArgs, ctx: ToolContext
) -> dict[str, Any]:
    res = ctx.references.resolve(
        session_id=ctx.session_id,
        user_id=ctx.user_id,
        reference_text=args.reference_text,
        ordinal=args.ordinal,
        explicit_listing_id=args.explicit_listing_id,
        update_focus=True,
    )
    if res.status == ResolutionStatus.RESOLVED:
        ctx.resolved_this_turn = True
        resolved_ids = [
            item.listing_id for item in res.resolved_listings
        ] or ([res.listing_id] if res.listing_id is not None else [])
        for lid in resolved_ids:
            if lid not in ctx.resolved_listing_ids:
                ctx.resolved_listing_ids.append(lid)
            if lid not in ctx.detail_card_ids:
                ctx.detail_card_ids.append(lid)
    payload: dict[str, Any] = {
        "status": res.status.value,
        "listing_id": res.listing_id,
        "listing": _serialize_summary(res.listing) if res.listing else None,
        "candidate_ids": res.candidate_ids,
        "resolution_method": res.resolution_method,
        "clarification_prompt": res.clarification_prompt,
        "message": res.message,
    }
    if len(res.resolved_listings) > 1:
        payload["resolved_listings"] = [_serialize_summary(item) for item in res.resolved_listings]
    return payload


def _tool_save_favourite(args: SaveFavouriteArgs, ctx: ToolContext) -> dict[str, Any]:
    target_id = args.listing_id
    if target_id is None:
        resolved = ctx.references.resolve(
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            reference_text=args.reference_text or "current car",
            update_focus=True,
        )
        if resolved.status != ResolutionStatus.RESOLVED or resolved.listing_id is None:
            return {
                "saved": False,
                "status": resolved.status.value,
                "clarification_prompt": resolved.clarification_prompt,
                "message": resolved.message,
            }
        target_id = resolved.listing_id

    fav = ctx.users.add_favourite(ctx.user_id, target_id)
    ctx.leads.sync_user_lead(ctx.user_id, listing_id=target_id)
    summaries = ctx.inventory.get_vehicle_summaries([target_id])
    if target_id not in ctx.detail_card_ids:
        ctx.detail_card_ids.append(target_id)
    return {
        "saved": fav.saved,
        "listing_id": fav.listing_id,
        "vehicle": _serialize_summary(summaries[0]) if summaries else None,
    }


def _tool_save_or_update_preference(args: SavePreferenceArgs, ctx: ToolContext) -> dict[str, Any]:
    pref = ctx.memory.save_preference(
        user_id=ctx.user_id,
        preference_key=args.preference_key,
        preference_value=args.preference_value,
        original_wording=args.original_wording,
        source_type=args.source_type,
        session_id=ctx.session_id,
        current_user_message=ctx.current_user_message,
        amount=args.amount,
        currency=args.currency,
    )
    ctx.leads.sync_user_lead(ctx.user_id)
    return {"saved": True, "preference": _serialize_preference(pref)}


def _tool_get_user_preferences(_args: GetUserPreferencesArgs, ctx: ToolContext) -> dict[str, Any]:
    prefs = ctx.memory.get_preferences(ctx.user_id)
    return {
        "user_id": ctx.user_id,
        "preferences": [_serialize_preference(p) for p in prefs],
    }


def _tool_get_search_history(args: GetSearchHistoryArgs, ctx: ToolContext) -> dict[str, Any]:
    searches = ctx.memory.get_search_history(ctx.user_id, limit=args.limit)
    return {
        "user_id": ctx.user_id,
        "searches": [
            {
                "id": s.id,
                "query_text": s.query_text,
                "filters": s.filters,
                "result_count": s.result_count,
                "listing_ids": s.listing_ids,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in searches
        ],
    }


def _tool_get_saved_vehicles(_args: GetSavedVehiclesArgs, ctx: ToolContext) -> dict[str, Any]:
    favs = ctx.users.list_favourites(ctx.user_id)
    if favs:
        for item in favs:
            if item.listing_id not in ctx.detail_card_ids:
                ctx.detail_card_ids.append(item.listing_id)
    return {
        "user_id": ctx.user_id,
        "count": len(favs),
        "saved_vehicles": [_serialize_summary(item) for item in favs],
    }


def _tool_get_user_context(_args: GetUserContextArgs, ctx: ToolContext) -> dict[str, Any]:
    profile = ctx.users.get_user_profile(ctx.user_id)
    favourites = ctx.users.list_favourites(ctx.user_id)
    searches = ctx.memory.get_search_history(ctx.user_id, limit=5)
    bookings = ctx.users.get_bookings_readonly(ctx.user_id)
    session = ctx.sessions.get_session(ctx.session_id, user_id=ctx.user_id)
    returning = ctx.memory.build_returning_user_summary(ctx.user_id, session_id=ctx.session_id)
    return {
        "user_id": profile.user_id,
        "name": profile.name,
        "is_returning": returning.is_returning,
        "returning_welcome_message": returning.welcome_message,
        "preferences": [_serialize_preference(p) for p in profile.preferences],
        "favourite_count": profile.favourite_count,
        "favourites": [_serialize_summary(item) for item in favourites],
        "recent_searches": [
            {
                "query_text": s.query_text,
                "filters": s.filters,
                "result_count": s.result_count,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in searches
        ],
        "booking_history": bookings,
        "session": {
            "session_id": session.session_id,
            "current_vehicle": session.current_vehicle,
            "last_result_ids": session.last_result_ids,
            "active_filters": session.active_filters,
            "booking_draft": session.booking_draft,
            "context_summary": session.context_summary,
        },
    }


def _tool_propose_booking(args: ProposeBookingArgs, ctx: ToolContext) -> dict[str, Any]:
    if ctx.current_user_message and not has_explicit_clock_time(ctx.current_user_message):
        reason = (
            "vague time expression"
            if contains_vague_time_phrase(ctx.current_user_message)
            else "missing explicit clock time"
        )
        return {
            "success": False,
            "booking": None,
            "message": (
                f"Cannot propose a viewing from a {reason}. Please specify an exact start time "
                "between 08:00 and 19:00 Asia/Dubai (for example '14:00', '2 PM', or '2:30 PM')."
            ),
            "alternatives": [],
        }

    target_id = args.listing_id
    if target_id is None:
        resolved = ctx.references.resolve(
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            reference_text=args.reference_text or "current car",
            update_focus=True,
        )
        if resolved.status != ResolutionStatus.RESOLVED or resolved.listing_id is None:
            return {
                "success": False,
                "status": resolved.status.value,
                "clarification_prompt": resolved.clarification_prompt,
                "message": resolved.message,
                "alternatives": [],
            }
        target_id = resolved.listing_id

    if target_id not in ctx.detail_card_ids:
        ctx.detail_card_ids.append(target_id)

    res = ctx.bookings.propose_booking(
        user_id=ctx.user_id,
        listing_id=target_id,
        requested_date=args.requested_date,
        requested_time=args.requested_time,
        session_id=ctx.session_id,
        customer_name=args.customer_name,
        customer_phone=args.customer_phone,
    )
    return {
        "success": res.success,
        "booking": _serialize_booking(res.booking),
        "message": res.message,
        "alternatives": [_serialize_alternative(a) for a in res.alternatives],
    }


def _tool_confirm_booking(args: ConfirmBookingArgs, ctx: ToolContext) -> dict[str, Any]:
    target_booking_id = args.booking_id
    if target_booking_id is None:
        session = ctx.sessions.get_session(ctx.session_id, user_id=ctx.user_id)
        if session.booking_draft and session.booking_draft.get("booking_id"):
            target_booking_id = int(session.booking_draft["booking_id"])
        else:
            pending = [
                b for b in ctx.bookings.get_user_bookings(ctx.user_id) if b.status.value == "proposed"
            ]
            if len(pending) == 1 and pending[0].booking_id is not None:
                target_booking_id = pending[0].booking_id

    if target_booking_id is None:
        return {
            "success": False,
            "booking": None,
            "message": (
                "There is no proposed booking selected yet. Please propose a viewing date "
                "and exact time first before confirming."
            ),
            "alternatives": [],
        }

    res = ctx.bookings.confirm_booking(
        booking_id=target_booking_id,
        user_id=ctx.user_id,
        confirm=args.confirm,
        confirmation_text=args.confirmation_text or ctx.current_user_message,
        customer_name=args.customer_name,
        customer_phone=args.customer_phone,
    )
    if res.booking and res.booking.listing_id not in ctx.detail_card_ids:
        ctx.detail_card_ids.append(res.booking.listing_id)

    return {
        "success": res.success,
        "booking": _serialize_booking(res.booking),
        "message": res.message,
        "alternatives": [_serialize_alternative(a) for a in res.alternatives],
    }


def _tool_get_booking_details(args: GetBookingDetailsArgs, ctx: ToolContext) -> dict[str, Any]:
    booking = ctx.bookings.get_booking(args.booking_id, user_id=ctx.user_id)
    summaries = ctx.inventory.get_vehicle_summaries([booking.listing_id])
    if booking.listing_id not in ctx.detail_card_ids:
        ctx.detail_card_ids.append(booking.listing_id)
    return {
        "found": True,
        "booking": _serialize_booking(booking),
        "vehicle": _serialize_summary(summaries[0]) if summaries else None,
    }


def _tool_get_user_bookings(_args: GetUserBookingsArgs, ctx: ToolContext) -> dict[str, Any]:
    bookings = ctx.bookings.get_user_bookings(ctx.user_id)
    return {
        "user_id": ctx.user_id,
        "count": len(bookings),
        "bookings": [_serialize_booking(b) for b in bookings],
    }


def _extract_explicit_financing(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    low = text.lower()
    if re.search(r"\b(?:in\s+cash|pay(?:ing)?\s+(?:in\s+)?cash|cash\s+(?:purchase|buyer|deal))\b", low):
        return "cash"
    if re.search(r"\b(?:bank\s+financ(?:e|ing)|auto\s+loan|monthly\s+instal+ments?)\b", low):
        return "bank financing"
    return None


def _tool_update_lead_information(args: UpdateLeadInfoArgs, ctx: ToolContext) -> dict[str, Any]:
    financing = args.financing_interest or _extract_explicit_financing(ctx.current_user_message)
    res = ctx.leads.upsert_lead(
        user_id=ctx.user_id,
        name=args.name,
        phone=args.phone,
        budget=args.budget,
        currency=args.currency,
        preferred_makes=args.preferred_makes,
        preferred_models=args.preferred_models,
        preferred_body_type=args.preferred_body_type,
        must_have_features=args.must_have_features,
        purchase_timeline=args.purchase_timeline,
        financing_interest=financing,
        listing_ids=args.listing_ids,
    )
    return {
        "saved": True,
        "lead": _serialize_lead(res.lead),
        "next_question": res.next_question,
    }


# ── Registry ──


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[Any, ToolContext], dict[str, Any]]


TOOL_SPECS: dict[str, ToolSpec] = {
    "search_inventory": ToolSpec(
        name="search_inventory",
        description=(
            "Search the dubizzle vehicle inventory using structured filters (make, model, year range, "
            "max confirmed cash price in AED, mileage in km, spec region, body type, transmission, "
            "confirmed warranty) and optional FTS5 keywords. Updates the session's ordered result list."
        ),
        args_model=SearchInventoryArgs,
        handler=_tool_search_inventory,
    ),
    "get_listing_details": ToolSpec(
        name="get_listing_details",
        description=(
            "Retrieve complete database details, display-safe description, and extracted attributes "
            "with source evidence and confirmed/inferred status for a specific listing ID."
        ),
        args_model=GetListingDetailsArgs,
        handler=_tool_get_listing_details,
    ),
    "resolve_listing_reference": ToolSpec(
        name="resolve_listing_reference",
        description=(
            "Deterministically resolve conversational vehicle references such as 'the first one', "
            "'the second car', 'last one', 'that car', 'this one', 'the Audi', 'the one I saved', "
            "or explicit listing IDs against persisted session state."
        ),
        args_model=ResolveListingReferenceArgs,
        handler=_tool_resolve_listing_reference,
    ),
    "save_favourite": ToolSpec(
        name="save_favourite",
        description=(
            "Save a listing to the current user's favourites list. Accepts either an explicit listing_id "
            "or a reference_text like 'this car' / 'the first one' to resolve deterministically first."
        ),
        args_model=SaveFavouriteArgs,
        handler=_tool_save_favourite,
    ),
    "save_preference": ToolSpec(
        name="save_preference",
        description=(
            "Save a durable vehicle or budget preference explicitly stated by the user. "
            "Requires quoting the user's exact original_wording. Supports AED and USD budgets."
        ),
        args_model=SavePreferenceArgs,
        handler=_tool_save_or_update_preference,
    ),
    "update_preference": ToolSpec(
        name="update_preference",
        description=(
            "Update an existing durable user preference when the user changes or contradicts a "
            "previous preference (e.g. changing budget or spec). Preserves preference history."
        ),
        args_model=SavePreferenceArgs,
        handler=_tool_save_or_update_preference,
    ),
    "get_user_preferences": ToolSpec(
        name="get_user_preferences",
        description="Retrieve the active user's persisted preferences with provenance and currency metadata.",
        args_model=GetUserPreferencesArgs,
        handler=_tool_get_user_preferences,
    ),
    "get_search_history": ToolSpec(
        name="get_search_history",
        description="Retrieve the active user's past search queries, normalized filters, and result counts.",
        args_model=GetSearchHistoryArgs,
        handler=_tool_get_search_history,
    ),
    "get_saved_vehicles": ToolSpec(
        name="get_saved_vehicles",
        description="Retrieve the active user's saved favourite vehicles from SQLite.",
        args_model=GetSavedVehiclesArgs,
        handler=_tool_get_saved_vehicles,
    ),
    "get_user_context": ToolSpec(
        name="get_user_context",
        description=(
            "Retrieve the active user's complete profile, preferences, saved favourites, search history, "
            "booking history, and current session context."
        ),
        args_model=GetUserContextArgs,
        handler=_tool_get_user_context,
    ),
    "propose_booking": ToolSpec(
        name="propose_booking",
        description=(
            "Deterministically validate a viewing slot (Mon-Sat 08:00-19:00 Asia/Dubai, >=2h notice) "
            "and create a non-binding 'proposed' viewing booking. Suggests nearby valid alternatives "
            "if invalid or busy. Does NOT confirm or reserve the slot."
        ),
        args_model=ProposeBookingArgs,
        handler=_tool_propose_booking,
    ),
    "confirm_booking": ToolSpec(
        name="confirm_booking",
        description=(
            "Confirm a previously proposed viewing booking ONLY after explicit user confirmation. "
            "Enforces availability atomically and rejects vague confirmation phrases."
        ),
        args_model=ConfirmBookingArgs,
        handler=_tool_confirm_booking,
    ),
    "get_booking_details": ToolSpec(
        name="get_booking_details",
        description="Retrieve details for a specific viewing booking owned by the active user.",
        args_model=GetBookingDetailsArgs,
        handler=_tool_get_booking_details,
    ),
    "get_user_bookings": ToolSpec(
        name="get_user_bookings",
        description="List all proposed and confirmed viewing bookings for the active user.",
        args_model=GetUserBookingsArgs,
        handler=_tool_get_user_bookings,
    ),
    "update_lead_information": ToolSpec(
        name="update_lead_information",
        description=(
            "Record or update customer qualification details (name, phone, budget, makes, timeline, "
            "cash vs financing) and deterministically compute lead score."
        ),
        args_model=UpdateLeadInfoArgs,
        handler=_tool_update_lead_information,
    ),
}


def get_tool_schemas() -> list[ToolSchema]:
    """Export provider-agnostic ToolSchema declarations for all exposed tools."""
    return [
        ToolSchema(
            name=spec.name,
            description=spec.description,
            parameters_json_schema=spec.args_model.model_json_schema(),
        )
        for spec in TOOL_SPECS.values()
    ]


def execute_tool(
    tool_name: str,
    raw_args: dict[str, Any],
    ctx: ToolContext,
) -> tuple[bool, dict[str, Any]]:
    """Validate arguments with Pydantic and execute the tool via the service layer."""
    spec = TOOL_SPECS.get(tool_name)
    if spec is None:
        return False, {"error": f"Unknown tool '{tool_name}'. Available tools: {list(TOOL_SPECS)}."}

    try:
        validated = spec.args_model.model_validate(raw_args or {})
    except ValidationError as exc:
        err_msg = "; ".join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        return False, {"error": f"Invalid arguments for {tool_name}: {err_msg}"}

    try:
        result = spec.handler(validated, ctx)
        return True, result
    except AppError as exc:
        return False, {"error": exc.detail, "error_code": exc.error_code}
    except Exception as exc:
        return False, {"error": f"Tool execution error: {type(exc).__name__}"}


def build_tool_context(
    *,
    user_id: str,
    session_id: str,
    db_path: Optional[Path | str] = None,
    current_user_message: Optional[str] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
    exports_dir: Optional[Path | str] = None,
) -> ToolContext:
    """Factory creating a ToolContext wired to backend services."""
    return ToolContext(
        user_id=user_id,
        session_id=session_id,
        inventory=InventorySearchService(db_path),
        sessions=SessionService(db_path),
        references=ReferenceResolutionService(db_path),
        users=UserService(db_path),
        memory=MemoryService(db_path),
        bookings=BookingService(db_path, now_fn=now_fn, exports_dir=exports_dir),
        leads=LeadService(db_path, exports_dir=exports_dir),
        current_user_message=current_user_message,
    )
