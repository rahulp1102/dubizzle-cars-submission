"""Lead qualification, deterministic lead scoring, phone validation, and CSV export service."""

from __future__ import annotations

import csv
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.inventory.aliases import resolve_body_type, resolve_make, resolve_model
from backend.inventory.search import InventorySearchService
from backend.memory.repository import MemoryRepository
from backend.memory.service import convert_budget_to_aed
from backend.models.entities import Booking, BookingStatus, Lead, LeadStatus
from backend.models.schemas import LeadDetailResponse
from backend.services.booking_repository import BookingLeadRepository
from backend.services.exceptions import BadRequestError, ForbiddenError, NotFoundError

logger = logging.getLogger(__name__)
_CSV_EXPORT_LOCK = threading.Lock()

_PHONE_ALLOWED_RE = re.compile(r"^[\d+\-\s()]+$")
_UAE_MOBILE_LOCAL_RE = re.compile(r"^05\d{8}$")
_UAE_INTL_RE = re.compile(r"^\+971(?:5\d{8}|[234679]\d{7})$")
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")

CSV_COLUMNS = [
    "lead_id",
    "user_id",
    "name",
    "phone",
    "budget",
    "currency",
    "original_budget",
    "original_currency",
    "preferred_makes",
    "preferred_models",
    "preferred_body_type",
    "must_have_features",
    "purchase_timeline",
    "financing_interest",
    "listing_ids",
    "interested_vehicles",
    "booking_id",
    "booking_slot",
    "status",
    "created_at",
    "updated_at",
]


def mask_phone(phone: Optional[str]) -> Optional[str]:
    """Return a masked representation of a phone number safe for logs, traces, and buyer responses."""
    cleaned = (phone or "").strip()
    if not cleaned:
        return None
    if len(cleaned) <= 6:
        return "*" * len(cleaned)
    prefix = cleaned[:4]
    suffix = cleaned[-3:]
    stars = "*" * max(4, len(cleaned) - 7)
    return f"{prefix}{stars}{suffix}"


def mask_booking_for_buyer(booking: Optional[Booking]) -> Optional[Booking]:
    """Return a shallow copy of a booking with customer_phone masked for buyer responses."""
    if booking is None:
        return None
    return booking.model_copy(
        update={"customer_phone": mask_phone(booking.customer_phone)}
    )


def mask_lead_for_buyer(lead: Lead) -> Lead:
    """Return a shallow copy of a lead with phone masked for buyer responses."""
    return lead.model_copy(update={"phone": mask_phone(lead.phone)})


def validate_and_normalize_phone(raw_phone: str) -> str:
    """Deterministically validate and normalise UAE and E.164 phone numbers."""
    text = raw_phone.strip()
    if not text or not _PHONE_ALLOWED_RE.match(text):
        raise BadRequestError(
            "Invalid phone number. Provide a valid UAE number (e.g. 0501234567 or +971501234567) "
            "or international E.164 number.",
            error_code="invalid_phone",
        )

    compact = re.sub(r"[\s\-()]", "", text)
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    elif compact.startswith("971") and not compact.startswith("+"):
        compact = "+" + compact
    elif _UAE_MOBILE_LOCAL_RE.match(compact):
        compact = "+971" + compact[1:]

    if "+" in compact[1:]:
        raise BadRequestError("Invalid phone number format.", error_code="invalid_phone")

    if compact.startswith("+971"):
        if not _UAE_INTL_RE.match(compact):
            raise BadRequestError(
                "Invalid UAE phone number. Expected +971 followed by 8 or 9 digits (e.g. +971501234567).",
                error_code="invalid_phone",
            )
    elif not _E164_RE.match(compact):
        raise BadRequestError(
            "Invalid phone number. Provide a valid UAE number (05XXXXXXXX / +971XXXXXXXXX) "
            "or international E.164 number (+countrycode...).",
            error_code="invalid_phone",
        )

    # Reject trivial repeated digits in subscriber tail (e.g. +971000000000)
    subscriber_digits = compact[4:] if compact.startswith("+971") else compact[1:]
    if len(set(subscriber_digits)) <= 1:
        raise BadRequestError("Invalid phone number digits.", error_code="invalid_phone")

    return compact


def score_lead(
    *,
    budget: Optional[float],
    listing_ids: Optional[list[int]] = None,
    preferred_makes: Optional[list[str]] = None,
    preferred_models: Optional[list[str]] = None,
    preferred_body_type: Optional[str] = None,
    must_have_features: Optional[list[str]] = None,
    purchase_timeline: Optional[str] = None,
    financing_interest: Optional[str] = None,
    phone: Optional[str] = None,
    favourite_count: int = 0,
    has_proposed_booking: bool = False,
    has_confirmed_booking: bool = False,
) -> LeadStatus:
    """Deterministically score lead readiness without LLM involvement."""
    has_budget = budget is not None and budget > 0
    has_interest = (
        bool(listing_ids)
        or favourite_count > 0
        or has_proposed_booking
        or has_confirmed_booking
        or bool(preferred_makes)
        or bool(preferred_models)
        or bool(preferred_body_type and preferred_body_type.strip())
        or bool(must_have_features)
        or (
            bool(purchase_timeline and purchase_timeline.strip())
            and (
                bool(phone and phone.strip())
                or bool(financing_interest and financing_interest.strip())
            )
        )
    )
    if has_confirmed_booking and has_budget:
        return LeadStatus.HOT
    if has_budget and has_interest:
        return LeadStatus.WARM
    return LeadStatus.COLD


def _dedupe_strs(values: list[str]) -> Optional[list[str]]:
    seen: list[str] = []
    seen_low: set[str] = set()
    for val in values:
        cleaned = " ".join(val.strip().split())
        if cleaned and cleaned.lower() not in seen_low:
            seen.append(cleaned)
            seen_low.add(cleaned.lower())
    return seen or None


def _dedupe_ints(values: list[int]) -> Optional[list[int]]:
    seen: list[int] = []
    seen_set: set[int] = set()
    for val in values:
        if val > 0 and val not in seen_set:
            seen.append(val)
            seen_set.add(val)
    return seen or None


class LeadService:
    """Service layer managing lead upsert, scoring, qualification prompts, and CSV exports."""

    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        *,
        exports_dir: Optional[Path | str] = None,
    ) -> None:
        self.db_path = db_path
        self.repo = BookingLeadRepository(db_path)
        self.memory_repo = MemoryRepository(db_path)
        if exports_dir is not None:
            self.exports_dir = Path(exports_dir)
        elif db_path is not None:
            resolved_db = Path(db_path).resolve()
            if resolved_db != settings.database_path.resolve():
                self.exports_dir = resolved_db.parent / "exports"
            else:
                self.exports_dir = settings.exports_dir
        else:
            self.exports_dir = settings.exports_dir

    def get_lead(self, user_id: str) -> LeadDetailResponse:
        if self.memory_repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")
        lead = self.repo.get_lead_by_user(user_id)
        if lead is None:
            raise NotFoundError(
                f"Lead record for user '{user_id}' not found.",
                error_code="lead_not_found",
            )
        return LeadDetailResponse(
            lead=lead,
            next_question=self.get_next_qualification_question(lead),
        )

    def list_leads(self) -> list[Lead]:
        return self.repo.list_leads()

    def get_next_qualification_question(self, lead: Lead) -> Optional[str]:
        """Return at most ONE missing qualification question without inventing fields."""
        if lead.budget is None:
            return "What is your target budget (in AED or USD)?"
        if not lead.preferred_makes and not lead.preferred_body_type and not lead.listing_ids:
            return "Which car makes or body type (for example SUV or sedan) are you looking for?"
        if lead.purchase_timeline is None:
            return "What is your expected timeline for purchasing (e.g. this week, this month)?"
        if lead.financing_interest is None:
            return "Are you planning to pay in cash or explore bank financing?"
        if lead.name is None:
            return "May I take your name for our vehicle specialist?"
        if lead.phone is None:
            return "What is the best phone number (UAE or international) to reach you on?"
        return None

    def upsert_lead(
        self,
        *,
        user_id: str,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        budget: Optional[float] = None,
        currency: str = "AED",
        preferred_makes: Optional[list[str]] = None,
        preferred_models: Optional[list[str]] = None,
        preferred_body_type: Optional[str] = None,
        must_have_features: Optional[list[str]] = None,
        purchase_timeline: Optional[str] = None,
        financing_interest: Optional[str] = None,
        listing_ids: Optional[list[int]] = None,
        booking_id: Optional[int] = None,
    ) -> LeadDetailResponse:
        """Create or update a single lead record per user_id, score it, and refresh leads.csv."""
        user = self.memory_repo.get_user(user_id)
        if user is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")

        existing = self.repo.get_lead_by_user(user_id)
        prefs = self.memory_repo.get_preferences(user_id)
        pref_map = {p.preference_key: p for p in prefs}
        fav_ids = self.memory_repo.list_favourite_ids(user_id)
        bookings = self.repo.list_user_bookings(user_id)

        # Validate explicit booking_id ownership if supplied
        if booking_id is not None:
            target_booking = self.repo.get_booking_by_id(booking_id)
            if target_booking is None:
                raise NotFoundError(
                    f"Booking {booking_id} not found.", error_code="booking_not_found"
                )
            if target_booking.user_id != user_id:
                raise ForbiddenError(
                    "Booking does not belong to this user.",
                    error_code="booking_forbidden",
                )

        # Name
        cleaned_name = name.strip() if name and name.strip() else None
        final_name = cleaned_name or (existing.name if existing else None) or user.name

        # Phone validation (never store invalid numbers)
        final_phone = existing.phone if existing else None
        if phone is not None and phone.strip():
            final_phone = validate_and_normalize_phone(phone)

        # Budget + currency normalization
        final_budget = existing.budget if existing else None
        final_currency = existing.currency if existing else "AED"
        final_orig_budget = existing.original_budget if existing else None
        final_orig_currency = existing.original_currency if existing else "AED"

        if budget is not None:
            converted = convert_budget_to_aed(budget, currency)
            final_budget = float(converted["normalized_amount_aed"])
            final_currency = "AED"
            final_orig_budget = float(converted["original_amount"])
            final_orig_currency = str(converted["original_currency"])
        elif final_budget is None and "budget_max" in pref_map:
            budget_pref = pref_map["budget_max"]
            meta = budget_pref.metadata or {}
            if "normalized_amount_aed" in meta:
                final_budget = float(meta["normalized_amount_aed"])
                final_currency = "AED"
                final_orig_budget = float(meta.get("original_amount", final_budget))
                final_orig_currency = str(meta.get("original_currency", "AED"))
            else:
                try:
                    parsed = float(budget_pref.preference_value)
                    if parsed > 0:
                        final_budget = parsed
                        final_currency = "AED"
                        final_orig_budget = parsed
                        final_orig_currency = "AED"
                except ValueError:
                    pass

        # Makes, models, body type, features (strictly from explicit input / existing / saved prefs)
        make_candidates: list[str] = []
        if existing and existing.preferred_makes:
            make_candidates.extend(existing.preferred_makes)
        if "preferred_makes" in pref_map:
            make_candidates.extend(
                [p.strip() for p in pref_map["preferred_makes"].preference_value.split(",") if p.strip()]
            )
        if preferred_makes:
            for item in preferred_makes:
                resolved = resolve_make(item)
                make_candidates.append(resolved or item.strip())
        final_makes = _dedupe_strs(make_candidates)

        model_candidates: list[str] = []
        if existing and existing.preferred_models:
            model_candidates.extend(existing.preferred_models)
        if "preferred_models" in pref_map:
            model_candidates.extend(
                [p.strip() for p in pref_map["preferred_models"].preference_value.split(",") if p.strip()]
            )
        if preferred_models:
            for item in preferred_models:
                resolved = resolve_model(item)
                model_candidates.append(resolved or item.strip())
        final_models = _dedupe_strs(model_candidates)

        final_body_type = existing.preferred_body_type if existing else None
        if preferred_body_type and preferred_body_type.strip():
            final_body_type = resolve_body_type(preferred_body_type) or preferred_body_type.strip()
        elif final_body_type is None and "preferred_body_types" in pref_map:
            first_bt = pref_map["preferred_body_types"].preference_value.split(",")[0].strip()
            final_body_type = first_bt or None

        feature_candidates: list[str] = []
        if existing and existing.must_have_features:
            feature_candidates.extend(existing.must_have_features)
        if "must_have_features" in pref_map:
            feature_candidates.extend(
                [
                    p.strip()
                    for p in pref_map["must_have_features"].preference_value.split(",")
                    if p.strip()
                ]
            )
        if "preferred_colour" in pref_map:
            col_val = pref_map["preferred_colour"].preference_value.strip()
            if col_val and not any(col_val.lower() in f.lower() for f in feature_candidates):
                feature_candidates.append(f"colour: {col_val}")
        if must_have_features:
            feature_candidates.extend(must_have_features)
        final_features = _dedupe_strs(feature_candidates)

        cleaned_timeline = (
            purchase_timeline.strip()
            if purchase_timeline and purchase_timeline.strip()
            else None
        )
        final_timeline = cleaned_timeline or (existing.purchase_timeline if existing else None)

        cleaned_financing = (
            financing_interest.strip()
            if financing_interest and financing_interest.strip()
            else None
        )
        final_financing = cleaned_financing or (existing.financing_interest if existing else None)

        focused_ids = self.memory_repo.list_user_focused_listing_ids(user_id)
        listing_candidates: list[int] = []
        if existing and existing.listing_ids:
            listing_candidates.extend(existing.listing_ids)
        if listing_ids:
            listing_candidates.extend(listing_ids)
        listing_candidates.extend(fav_ids)
        listing_candidates.extend(focused_ids)
        for bk in bookings:
            if bk.status != BookingStatus.CANCELLED:
                listing_candidates.append(bk.listing_id)
        final_listing_ids = _dedupe_ints(listing_candidates)

        confirmed_bookings = [b for b in bookings if b.status == BookingStatus.CONFIRMED]
        proposed_bookings = [b for b in bookings if b.status == BookingStatus.PROPOSED]

        final_booking_id = booking_id
        if final_booking_id is None:
            if confirmed_bookings:
                final_booking_id = confirmed_bookings[0].booking_id
            elif proposed_bookings:
                final_booking_id = proposed_bookings[0].booking_id
            elif existing:
                final_booking_id = existing.booking_id

        lead_status = score_lead(
            budget=final_budget,
            listing_ids=final_listing_ids,
            preferred_makes=final_makes,
            preferred_models=final_models,
            preferred_body_type=final_body_type,
            must_have_features=final_features,
            purchase_timeline=final_timeline,
            financing_interest=final_financing,
            phone=final_phone,
            favourite_count=len(fav_ids),
            has_proposed_booking=bool(proposed_bookings),
            has_confirmed_booking=bool(confirmed_bookings),
        )

        lead = self.repo.upsert_lead(
            user_id=user_id,
            name=final_name,
            phone=final_phone,
            budget=final_budget,
            currency=final_currency,
            original_budget=final_orig_budget,
            original_currency=final_orig_currency,
            preferred_makes=final_makes,
            preferred_models=final_models,
            preferred_body_type=final_body_type,
            must_have_features=final_features,
            purchase_timeline=final_timeline,
            financing_interest=final_financing,
            listing_ids=final_listing_ids,
            booking_id=final_booking_id,
            status=lead_status,
        )

        logger.info(
            "Upserted lead lead_id=%s user_id=%s status=%s phone=%s",
            lead.lead_id,
            user_id,
            lead.status.value,
            mask_phone(lead.phone),
        )
        self.export_leads_csv()

        return LeadDetailResponse(
            lead=lead,
            next_question=self.get_next_qualification_question(lead),
        )

    def sync_user_lead(
        self,
        user_id: str,
        *,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        listing_id: Optional[int] = None,
        booking_id: Optional[int] = None,
    ) -> LeadDetailResponse:
        """Synchronise a user's lead record after booking or qualification actions."""
        return self.upsert_lead(
            user_id=user_id,
            name=name,
            phone=phone,
            listing_ids=[listing_id] if listing_id is not None else None,
            booking_id=booking_id,
        )

    def _format_booking_slot(self, lead: Lead) -> str:
        booking = (
            self.repo.get_booking_by_id(lead.booking_id)
            if lead.booking_id is not None
            else None
        )
        if booking is None:
            user_bookings = [
                b for b in self.repo.list_user_bookings(lead.user_id)
                if b.status != BookingStatus.CANCELLED
            ]
            if user_bookings:
                confirmed = [b for b in user_bookings if b.status == BookingStatus.CONFIRMED]
                booking = confirmed[0] if confirmed else user_bookings[0]
        if booking is None:
            return ""
        return f"{booking.booking_date} {booking.start_time}-{booking.end_time} ({booking.timezone})"

    def _format_interested_vehicles(self, listing_ids: Optional[list[int]]) -> str:
        if not listing_ids:
            return ""
        inv = InventorySearchService(self.db_path)
        summaries = {s.listing_id: s for s in inv.get_vehicle_summaries(listing_ids)}
        parts: list[str] = []
        for lid in listing_ids:
            s = summaries.get(lid)
            if s is not None:
                parts.append(f"#{s.listing_id} {s.year} {s.make} {s.model}".strip())
            else:
                parts.append(f"Listing #{lid}")
        return "; ".join(parts)

    def export_leads_csv(self) -> Path:
        """Write current SQLite leads table into data/exports/leads.csv cleanly."""
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        export_path = self.exports_dir / "leads.csv"
        with _CSV_EXPORT_LOCK:
            leads = self.repo.list_leads()
            fd, tmp_name = tempfile.mkstemp(
                prefix="leads_", suffix=".csv.tmp", dir=str(self.exports_dir)
            )
            tmp_path = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
                    writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
                    writer.writeheader()
                    for lead in leads:
                        writer.writerow(
                            {
                                "lead_id": lead.lead_id,
                                "user_id": lead.user_id,
                                "name": lead.name or "",
                                "phone": lead.phone or "",
                                "budget": lead.budget if lead.budget is not None else "",
                                "currency": lead.currency,
                                "original_budget": (
                                    lead.original_budget
                                    if lead.original_budget is not None
                                    else ""
                                ),
                                "original_currency": lead.original_currency,
                                "preferred_makes": (
                                    ", ".join(lead.preferred_makes) if lead.preferred_makes else ""
                                ),
                                "preferred_models": (
                                    ", ".join(lead.preferred_models)
                                    if lead.preferred_models
                                    else ""
                                ),
                                "preferred_body_type": lead.preferred_body_type or "",
                                "must_have_features": (
                                    ", ".join(lead.must_have_features)
                                    if lead.must_have_features
                                    else ""
                                ),
                                "purchase_timeline": lead.purchase_timeline or "",
                                "financing_interest": lead.financing_interest or "",
                                "listing_ids": (
                                    ",".join(str(x) for x in lead.listing_ids)
                                    if lead.listing_ids
                                    else ""
                                ),
                                "interested_vehicles": self._format_interested_vehicles(
                                    lead.listing_ids
                                ),
                                "booking_id": (
                                    lead.booking_id if lead.booking_id is not None else ""
                                ),
                                "booking_slot": self._format_booking_slot(lead),
                                "status": lead.status.value,
                                "created_at": (
                                    lead.created_at.isoformat() if lead.created_at else ""
                                ),
                                "updated_at": (
                                    lead.updated_at.isoformat() if lead.updated_at else ""
                                ),
                            }
                        )
                tmp_path.replace(export_path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
        return export_path
