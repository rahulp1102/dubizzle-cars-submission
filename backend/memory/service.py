"""Long-term user memory service: preferences, provenance, currency conversion, search history, and Forget Me."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Optional

from backend.agent.guardrails import redact_sensitive_text
from backend.config import settings
from backend.inventory.aliases import (
    BODY_TYPE_ALIASES,
    COLOUR_ALIASES,
    MAKE_ALIASES,
    MODEL_ALIASES,
    SPEC_REGION_ALIASES,
    TRANSMISSION_ALIASES,
    resolve_body_type,
    resolve_colour,
    resolve_make,
    resolve_model,
    resolve_spec_region,
    resolve_transmission,
)
from backend.inventory.search import InventorySearchService
from backend.memory.repository import MemoryRepository
from backend.models.entities import (
    BookingStatus,
    ListingSummary,
    Preference,
    PreferenceHistoryEntry,
    ReturningUserSummary,
    SearchHistoryEntry,
    Session,
    User,
)
from backend.models.schemas import UserResponse, UserSnapshotResponse
from backend.services.exceptions import BadRequestError, ForbiddenError, NotFoundError

_KEY_ALIASES: dict[str, str] = {
    "budget": "budget_max",
    "max_budget": "budget_max",
    "budget_max": "budget_max",
    "make": "preferred_makes",
    "preferred_make": "preferred_makes",
    "preferred_makes": "preferred_makes",
    "model": "preferred_models",
    "preferred_model": "preferred_models",
    "preferred_models": "preferred_models",
    "body_type": "preferred_body_types",
    "preferred_body_type": "preferred_body_types",
    "preferred_body_types": "preferred_body_types",
    "spec": "spec_region",
    "spec_region": "spec_region",
    "mileage": "max_mileage_km",
    "max_mileage": "max_mileage_km",
    "max_mileage_km": "max_mileage_km",
    "min_year": "min_year",
    "transmission": "transmission",
    "warranty": "warranty_required",
    "warranty_required": "warranty_required",
    "must_have": "must_have_features",
    "must_have_features": "must_have_features",
    "excluded": "excluded_features",
    "excluded_features": "excluded_features",
    "colour": "preferred_colour",
    "color": "preferred_colour",
    "preferred_colour": "preferred_colour",
    "preferred_color": "preferred_colour",
    "preferred_colours": "preferred_colour",
    "preferred_colors": "preferred_colour",
    "name": "user_name",
    "user_name": "user_name",
}

_CURRENCY_ALIASES: dict[str, str] = {
    "aed": "AED",
    "dhs": "AED",
    "dh": "AED",
    "dirham": "AED",
    "dirhams": "AED",
    "usd": "USD",
    "$": "USD",
    "dollar": "USD",
    "dollars": "USD",
}


def canonicalize_preference_key(raw_key: str) -> str:
    cleaned = " ".join(raw_key.strip().lower().replace("-", "_").split())
    return _KEY_ALIASES.get(cleaned, cleaned)


def convert_budget_to_aed(amount: float, currency: str) -> dict[str, Any]:
    """Deterministically convert AED or USD budgets into AED using configured rate."""
    if amount <= 0:
        raise BadRequestError("Budget amount must be positive.", error_code="invalid_budget")

    curr_key = currency.strip().lower()
    norm_curr = _CURRENCY_ALIASES.get(curr_key)
    if norm_curr is None:
        raise BadRequestError(
            f"Unsupported currency '{currency}'. Supported currencies: AED, USD.",
            error_code="unsupported_currency",
        )

    if norm_curr == "AED":
        return {
            "original_amount": float(amount),
            "original_currency": "AED",
            "normalized_amount_aed": float(round(amount, 2)),
            "normalized_currency": "AED",
            "exchange_rate": 1.0,
            "is_approximate": False,
            "rate_note": "Native AED currency",
        }

    rate = settings.USD_TO_AED_RATE
    aed_val = round(amount * rate, 2)
    return {
        "original_amount": float(amount),
        "original_currency": "USD",
        "normalized_amount_aed": aed_val,
        "normalized_currency": "AED",
        "exchange_rate": rate,
        "is_approximate": True,
        "rate_note": f"Configured static reference rate (1 USD = {rate} AED; not a live market rate)",
    }


def _parse_budget_string(raw_val: str, currency_hint: Optional[str] = None) -> tuple[float, str]:
    """Parse amounts like '80k', '$20,000', '20,000 USD', '100000 AED'."""
    txt = raw_val.strip()
    detected_curr = currency_hint
    if detected_curr is None:
        if "$" in txt or re.search(r"\b(?:usd|dollars?)\b", txt, re.IGNORECASE):
            detected_curr = "USD"
        else:
            detected_curr = "AED"

    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?", txt, re.IGNORECASE)
    if not m:
        raise BadRequestError(
            f"Could not parse numeric budget from '{raw_val}'.",
            error_code="invalid_budget",
        )
    num = float(m.group(1).replace(",", ""))
    if m.group(2):
        num *= 1000.0
    return num, detected_curr


def _normalize_preference_value(
    key: str,
    raw_value: str,
    *,
    amount: Optional[float] = None,
    currency: Optional[str] = None,
) -> tuple[str, Optional[dict[str, Any]]]:
    """Normalize structured preference value and build optional metadata."""
    val = raw_value.strip()
    if not val and amount is None:
        raise BadRequestError("preference_value cannot be blank.", error_code="invalid_preference")

    if key == "budget_max":
        if amount is not None:
            parsed_amt, parsed_curr = amount, (currency or "AED")
        else:
            parsed_amt, parsed_curr = _parse_budget_string(val, currency)
        conv = convert_budget_to_aed(parsed_amt, parsed_curr)
        norm_str = str(int(round(conv["normalized_amount_aed"])))
        return norm_str, conv

    if key == "preferred_makes":
        items = [resolve_make(p) or p.strip().lower() for p in val.split(",") if p.strip()]
        return ", ".join(dict.fromkeys(items)), None

    if key == "preferred_models":
        items = [resolve_model(p) or p.strip().lower() for p in val.split(",") if p.strip()]
        return ", ".join(dict.fromkeys(items)), None

    if key == "preferred_body_types":
        items = [resolve_body_type(p) or p.strip().title() for p in val.split(",") if p.strip()]
        return ", ".join(dict.fromkeys(items)), None

    if key == "spec_region":
        return resolve_spec_region(val) or val, None

    if key == "transmission":
        return resolve_transmission(val) or val, None

    if key == "preferred_colour":
        items = [resolve_colour(p) or p.strip().title() for p in val.split(",") if p.strip()]
        return ", ".join(dict.fromkeys(items)), None

    if key == "max_mileage_km":
        amt, _ = _parse_budget_string(val, "AED")
        if amt < 0:
            raise BadRequestError("Mileage cannot be negative.", error_code="invalid_preference")
        return str(int(round(amt))), None

    if key == "min_year":
        m = re.search(r"\b(19\d{2}|20\d{2})\b", val)
        if not m:
            raise BadRequestError(f"Invalid year '{val}'.", error_code="invalid_preference")
        return m.group(1), None

    if key == "warranty_required":
        low = val.lower()
        if low in {"true", "yes", "1", "required"}:
            return "true", None
        if low in {"false", "no", "0", "not required"}:
            return "false", None
        raise BadRequestError(f"Invalid boolean for warranty_required: '{val}'.", error_code="invalid_preference")

    return val, None


def _text_supports_value(
    key: str,
    normalized_value: str,
    user_text_corpus: str,
    metadata: Optional[dict[str, Any]],
) -> bool:
    """Verify the normalized preference signal is grounded in the user's actual message text."""
    low_corpus = re.sub(r"\s+", " ", user_text_corpus.lower())
    corpus_digits = re.findall(r"\d+", low_corpus.replace(",", ""))

    if key == "budget_max" and metadata:
        orig_amt = int(round(metadata["original_amount"]))
        norm_amt = int(round(metadata["normalized_amount_aed"]))
        # Accept e.g. 20000, "20k", "20,000", or normalized AED digits
        candidates = {str(orig_amt), str(norm_amt)}
        if orig_amt >= 1000 and orig_amt % 1000 == 0:
            candidates.add(f"{orig_amt // 1000}k")
        if norm_amt >= 1000 and norm_amt % 1000 == 0:
            candidates.add(f"{norm_amt // 1000}k")
        return any(c in low_corpus.replace(",", "") for c in candidates)

    if key in {"max_mileage_km", "min_year"}:
        num = int(normalized_value)
        if str(num) in low_corpus.replace(",", ""):
            return True
        if num >= 1000 and num % 1000 == 0 and f"{num // 1000}k" in low_corpus:
            return True
        return str(num) in corpus_digits

    if key == "preferred_makes":
        for part in [p.strip().lower() for p in normalized_value.split(",")]:
            aliases = [k for k, v in MAKE_ALIASES.items() if v == part] + [part]
            if any(a in low_corpus for a in aliases):
                return True
        return False

    if key == "preferred_models":
        for part in [p.strip().lower() for p in normalized_value.split(",")]:
            aliases = [k for k, v in MODEL_ALIASES.items() if v == part] + [part, part.replace("-", " ")]
            if any(a in low_corpus for a in aliases):
                return True
        return False

    if key == "preferred_body_types":
        for part in [p.strip() for p in normalized_value.split(",")]:
            aliases = [k for k, v in BODY_TYPE_ALIASES.items() if v.lower() == part.lower()] + [part.lower()]
            if any(a in low_corpus for a in aliases):
                return True
        return False

    if key == "spec_region":
        aliases = [k for k, v in SPEC_REGION_ALIASES.items() if v.lower() == normalized_value.lower()] + [
            normalized_value.lower()
        ]
        return any(a in low_corpus for a in aliases)

    if key == "transmission":
        aliases = [k for k, v in TRANSMISSION_ALIASES.items() if v.lower() == normalized_value.lower()] + [
            normalized_value.lower()
        ]
        return any(a in low_corpus for a in aliases)

    if key == "preferred_colour":
        for part in [p.strip().lower() for p in normalized_value.split(",")]:
            aliases = [k for k, v in COLOUR_ALIASES.items() if v.lower() == part] + [part]
            if part == "grey":
                aliases.append("gray")
            if any(a in low_corpus for a in aliases):
                return True
        return False

    if key == "warranty_required":
        return "warrant" in low_corpus

    # Features / user_name / other: at least one meaningful word (>=2 chars) appears in user messages
    tokens = [t for t in re.findall(r"[a-z0-9]+", normalized_value.lower()) if len(t) >= 2]
    return bool(tokens) and any(t in low_corpus for t in tokens)


class MemoryService:
    """Service layer managing long-term preferences, provenance, history, and Forget Me."""

    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        *,
        exports_dir: Optional[Path | str] = None,
    ) -> None:
        self.db_path = db_path
        self.exports_dir = exports_dir
        self.repo = MemoryRepository(db_path)
        self.inventory = InventorySearchService(db_path)

    def save_preference(
        self,
        *,
        user_id: str,
        preference_key: str,
        preference_value: str,
        original_wording: Optional[str] = None,
        source_type: str = "explicit",
        session_id: Optional[str] = None,
        current_user_message: Optional[str] = None,
        amount: Optional[float] = None,
        currency: Optional[str] = None,
        source_turn: Optional[int] = None,
        source_message_id: Optional[int] = None,
        confidence: float = 1.0,
    ) -> Preference:
        """Validate provenance, normalize value/currency, and persist preference + history."""
        if self.repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")

        if source_type not in {"explicit", "ui", "inferred"}:
            raise BadRequestError(
                f"Invalid source_type '{source_type}'.", error_code="invalid_source_type"
            )

        canon_key = canonicalize_preference_key(preference_key)
        norm_value, metadata = _normalize_preference_value(
            canon_key,
            preference_value,
            amount=amount,
            currency=currency,
        )

        # Provenance validation for chat-originated saves (UI edits are already explicit user actions)
        resolved_msg_id = source_message_id
        if source_type != "ui":
            clean_wording = (original_wording or "").strip()
            if not clean_wording:
                raise BadRequestError(
                    "original_wording is required when saving a preference from conversation.",
                    error_code="missing_provenance",
                )

            # Verify session ownership before reading persisted messages for provenance
            if session_id:
                sess = self.repo.get_session(session_id)
                if sess is None:
                    raise NotFoundError(
                        f"Session '{session_id}' not found.",
                        error_code="session_not_found",
                    )
                if sess.user_id != user_id:
                    raise ForbiddenError(
                        f"Session '{session_id}' does not belong to user '{user_id}'.",
                        error_code="session_forbidden",
                    )

            # Verify wording against actual persisted user messages / current turn message
            user_msgs = (
                self.repo.get_last_user_messages(session_id, limit=8) if session_id else []
            )
            corpus_parts: list[str] = []
            if current_user_message:
                corpus_parts.append(current_user_message)
            for um in user_msgs:
                corpus_parts.append(um.content)
                if resolved_msg_id is None and clean_wording.lower() in um.content.lower():
                    resolved_msg_id = um.id
            if resolved_msg_id is None and user_msgs:
                resolved_msg_id = user_msgs[0].id

            corpus = "\n".join(corpus_parts)
            if not corpus.strip():
                raise BadRequestError(
                    "Cannot verify preference provenance without an actual user message.",
                    error_code="unsupported_preference",
                )

            norm_wording = re.sub(r"\s+", " ", clean_wording.lower())
            norm_corpus = re.sub(r"\s+", " ", corpus.lower())
            wording_tokens = [t for t in re.findall(r"[a-z0-9$]+", norm_wording) if len(t) >= 2]
            wording_supported = norm_wording in norm_corpus or (
                bool(wording_tokens)
                and sum(1 for t in wording_tokens if t in norm_corpus)
                >= max(1, len(wording_tokens) // 2)
            )
            value_supported = _text_supports_value(canon_key, norm_value, corpus, metadata)

            if not wording_supported or not value_supported:
                raise BadRequestError(
                    f"Rejected preference '{canon_key}={norm_value}': not supported by actual user message.",
                    error_code="unsupported_preference",
                )

        is_explicit = source_type in {"explicit", "ui"}
        status = "confirmed" if is_explicit else "inferred"
        eff_confidence = 1.0 if is_explicit else min(max(confidence, 0.1), 0.75)

        if canon_key == "user_name":
            self.repo.update_user_name(user_id, norm_value)

        safe_wording = redact_sensitive_text((original_wording or norm_value).strip())
        return self.repo.upsert_preference(
            user_id=user_id,
            preference_key=canon_key,
            preference_value=norm_value,
            original_wording=safe_wording,
            metadata=metadata,
            source_type=source_type,
            is_explicit=is_explicit,
            status=status,
            confidence=eff_confidence,
            source_turn=source_turn,
            source_message_id=resolved_msg_id,
        )

    def get_preferences(self, user_id: str) -> list[Preference]:
        if self.repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")
        return self.repo.get_preferences(user_id)

    def get_preference_history(
        self, user_id: str, preference_key: Optional[str] = None
    ) -> list[PreferenceHistoryEntry]:
        if self.repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")
        canon = canonicalize_preference_key(preference_key) if preference_key else None
        return self.repo.get_preference_history(user_id, canon)

    def delete_preference(self, user_id: str, preference_key: str) -> None:
        if self.repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")
        canon = canonicalize_preference_key(preference_key)
        if not self.repo.delete_preference(user_id, canon):
            raise NotFoundError(
                f"Preference '{canon}' not found for user '{user_id}'.",
                error_code="preference_not_found",
            )

    def record_search(
        self,
        *,
        user_id: str,
        query_text: Optional[str],
        filters: dict[str, Any],
        result_count: int,
        listing_ids: list[int],
    ) -> int:
        """Persist structured search history entry without mutating long-term preferences."""
        safe_query = redact_sensitive_text(query_text) if query_text else None
        return self.repo.record_search(
            user_id=user_id,
            query_text=safe_query,
            filters=filters,
            result_count=result_count,
            listing_ids=listing_ids[:20],
        )

    def get_search_history(self, user_id: str, limit: int = 15) -> list[SearchHistoryEntry]:
        if self.repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")
        return self.repo.get_search_history(user_id, limit=limit)

    def build_returning_user_summary(
        self,
        user_id: str,
        *,
        session_id: Optional[str] = None,
    ) -> ReturningUserSummary:
        """Build a concise, strictly grounded returning-user summary from stored memory."""
        user = self.repo.get_user(user_id)
        if user is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")

        prefs = self.repo.get_preferences(user_id)
        searches = self.repo.get_search_history(user_id, limit=5)
        fav_ids = self.repo.list_favourite_ids(user_id)
        bookings = self.repo.get_user_bookings_readonly(user_id)
        sess = (
            self.repo.get_session(session_id, include_messages=True)
            if session_id
            else None
        )
        if sess is not None and sess.user_id != user_id:
            raise ForbiddenError(
                f"Session '{session_id}' does not belong to user '{user_id}'.",
                error_code="session_forbidden",
            )
        return self.build_returning_summary_from_loaded(
            user=user,
            prefs=prefs,
            searches=searches,
            fav_ids=fav_ids,
            bookings=bookings,
            session=sess,
        )

    def build_returning_summary_from_loaded(
        self,
        *,
        user: User,
        prefs: list[Preference],
        searches: list[SearchHistoryEntry],
        fav_ids: list[int],
        bookings: list[Any],
        session: Optional[Session] = None,
        single_fav_summary: Optional[ListingSummary] = None,
    ) -> ReturningUserSummary:
        """Build returning-user summary directly from already-loaded memory objects."""
        active_bookings: list[dict[str, Any]] = []
        for bk in bookings:
            if isinstance(bk, dict):
                status_val = str(bk.get("status") or "")
                if status_val in {"confirmed", "proposed"}:
                    active_bookings.append(bk)
            else:
                status_val = bk.status.value if isinstance(bk.status, BookingStatus) else str(bk.status)
                if status_val in {"confirmed", "proposed"}:
                    active_bookings.append(
                        {
                            "listing_id": bk.listing_id,
                            "status": status_val,
                            "booking_date": bk.booking_date,
                            "start_time": bk.start_time,
                        }
                    )

        has_memory = bool(prefs or searches or fav_ids or active_bookings)
        if not has_memory:
            return ReturningUserSummary(
                is_returning=False,
                user_id=user.user_id,
                name=user.name,
                welcome_message=None,
            )

        # Avoid repetitive welcome messages within an already-active session
        already_welcomed = False
        if session is not None:
            if session.context_summary and "welcome_shown=true" in session.context_summary:
                already_welcomed = True
            elif any(m.role == "assistant" for m in session.recent_messages):
                already_welcomed = True

        greeting_name = f", {user.name}" if user.name else ""
        clauses: list[str] = []

        if prefs:
            pref_map = {p.preference_key: p for p in prefs}
            pref_bits: list[str] = []
            if "spec_region" in pref_map:
                pref_bits.append(f"{pref_map['spec_region'].preference_value}-spec")
            if "preferred_colour" in pref_map:
                pref_bits.append(pref_map["preferred_colour"].preference_value.lower())
            if "preferred_makes" in pref_map:
                pref_bits.append(pref_map["preferred_makes"].preference_value)
            if "preferred_models" in pref_map:
                pref_bits.append(pref_map["preferred_models"].preference_value)
            if "preferred_body_types" in pref_map:
                pref_bits.append(pref_map["preferred_body_types"].preference_value)
            if "budget_max" in pref_map:
                bp = pref_map["budget_max"]
                meta = bp.metadata or {}
                if meta.get("original_currency") == "USD":
                    pref_bits.append(
                        f"under USD {int(round(meta['original_amount'])):,} (~AED {int(bp.preference_value):,})"
                    )
                else:
                    pref_bits.append(f"under AED {int(bp.preference_value):,}")
            if "max_mileage_km" in pref_map:
                pref_bits.append(f"under {int(pref_map['max_mileage_km'].preference_value):,} km")
            if "min_year" in pref_map:
                pref_bits.append(f"{pref_map['min_year'].preference_value}+")
            if "must_have_features" in pref_map:
                pref_bits.append(f"with {pref_map['must_have_features'].preference_value}")
            if pref_bits:
                clauses.append(f"You have saved preferences for {' '.join(pref_bits)}")

        if searches:
            latest = searches[0]
            f = latest.filters
            desc_bits: list[str] = []
            if f.get("spec_region"):
                desc_bits.append(f"{f['spec_region']}-spec")
            if f.get("colour"):
                desc_bits.append(str(f["colour"]).lower())
            if f.get("make"):
                desc_bits.append(str(f["make"]))
            if f.get("model"):
                desc_bits.append(str(f["model"]))
            if f.get("body_type"):
                desc_bits.append(str(f["body_type"]) + "s")
            if f.get("max_price"):
                desc_bits.append(f"under AED {int(round(float(f['max_price']))):,}")
            if desc_bits:
                clauses.append(f"Last time you explored {' '.join(desc_bits)}")
            elif latest.query_text:
                clauses.append(f"Last time you searched for '{latest.query_text}'")

        if fav_ids:
            if len(fav_ids) == 1:
                fs = single_fav_summary
                if fs is None:
                    fav_summaries = self.inventory.get_vehicle_summaries(fav_ids)
                    fs = fav_summaries[0] if fav_summaries else None
                if fs is not None:
                    clauses.append(
                        f"You also saved a {fs.year} {fs.make} {fs.model} (#{fs.listing_id})"
                    )
            else:
                clauses.append(f"You've also saved {len(fav_ids)} vehicles")

        if active_bookings:
            latest_bk = active_bookings[0]
            bk_lid = int(latest_bk["listing_id"])
            bk_status = str(latest_bk.get("status") or "proposed")
            clauses.append(
                f"You have a {bk_status} viewing for listing #{bk_lid} on "
                f"{latest_bk['booking_date']} at {latest_bk['start_time']}"
            )

        welcome: Optional[str] = None
        if not already_welcomed and clauses:
            welcome = f"Welcome back{greeting_name}! " + ". ".join(clauses) + ". Would you like to continue?"

        return ReturningUserSummary(
            is_returning=True,
            user_id=user.user_id,
            name=user.name,
            welcome_message=welcome,
            preferences=prefs,
            recent_searches=searches[:5],
            favourite_count=len(fav_ids),
        )

    def get_user_snapshot(
        self,
        user_id: str,
        *,
        session_id: Optional[str] = None,
        name: Optional[str] = None,
        search_limit: int = 10,
    ) -> UserSnapshotResponse:
        """Load consolidated user profile, session, memory, bookings, and listing cards cleanly."""
        clean_uid = user_id.strip()
        if not clean_uid:
            raise BadRequestError("user_id cannot be blank", error_code="invalid_user_id")

        raw = self.repo.get_user_snapshot_raw(
            user_id=clean_uid,
            session_id=session_id,
            name=name,
            new_session_id=str(uuid.uuid4()),
            search_limit=search_limit,
        )
        user: User = raw["user"]
        session: Session = raw["session"]
        prefs: list[Preference] = raw["preferences"]
        pref_history: list[PreferenceHistoryEntry] = raw["preference_history"]
        searches: list[SearchHistoryEntry] = raw["searches"]
        fav_ids: list[int] = raw["fav_ids"]
        bookings = raw["bookings"]

        needed_ids: list[int] = list(fav_ids)
        for msg in session.recent_messages:
            for lid in msg.vehicle_cards or []:
                if lid not in needed_ids:
                    needed_ids.append(lid)
        if session.current_vehicle and session.current_vehicle not in needed_ids:
            needed_ids.append(int(session.current_vehicle))
        for bk in bookings:
            if bk.listing_id and bk.listing_id not in needed_ids:
                needed_ids.append(int(bk.listing_id))

        hydrated_listings = (
            self.inventory.get_vehicle_summaries(needed_ids) if needed_ids else []
        )
        by_id = {item.listing_id: item for item in hydrated_listings}
        favourites = [by_id[lid] for lid in fav_ids if lid in by_id]

        single_fav = favourites[0] if len(fav_ids) == 1 and favourites else None
        returning_summary = self.build_returning_summary_from_loaded(
            user=user,
            prefs=prefs,
            searches=searches,
            fav_ids=fav_ids,
            bookings=bookings,
            session=session,
            single_fav_summary=single_fav,
        )
        user_resp = UserResponse(
            user_id=user.user_id,
            name=user.name,
            preferences=prefs,
            favourite_count=len(fav_ids),
            booking_count=len(bookings),
        )
        return UserSnapshotResponse(
            user=user_resp,
            session=session,
            preferences=prefs,
            preference_history=pref_history,
            favourites=favourites,
            searches=searches,
            bookings=bookings,
            returning_summary=returning_summary,
            hydrated_listings=hydrated_listings,
            health={"status": "ok", "gemini_configured": settings.gemini_available},
        )

    def forget_user(self, user_id: str, *, confirm: bool = False) -> None:
        """Complete transactional deletion of all user data and stale CSV exports after explicit confirmation."""
        if not confirm:
            raise BadRequestError(
                "Explicit confirmation (confirm=True) is required before deleting all user data.",
                error_code="confirmation_required",
            )
        clean_uid = user_id.strip()
        if not clean_uid:
            raise BadRequestError("user_id cannot be blank", error_code="invalid_user_id")
        deleted = self.repo.delete_user(clean_uid)
        if not deleted:
            raise NotFoundError(f"User '{clean_uid}' not found.", error_code="user_not_found")

        # Keep leads.csv synchronized so deleted user PII never lingers on disk.
        from backend.services.leads import LeadService

        lead_svc = LeadService(self.db_path, exports_dir=self.exports_dir)
        if (lead_svc.exports_dir / "leads.csv").exists():
            lead_svc.export_leads_csv()
