"""Deterministic vehicle reference resolution service.

Resolves conversational references ("the first one", "that car", "the Audi",
"the one I saved", "listing #87") strictly in Python using persisted session
state and authoritative database records—never LLM assumptions.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from backend.inventory.aliases import MAKE_ALIASES, MODEL_ALIASES
from backend.inventory.search import InventorySearchService
from backend.memory.repository import MemoryRepository
from backend.models.entities import ListingSummary
from backend.services.sessions import SessionService

_ORDINAL_WORDS: dict[str, int] = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
    "sixth": 6,
    "6th": 6,
    "seventh": 7,
    "7th": 7,
    "eighth": 8,
    "8th": 8,
    "ninth": 9,
    "9th": 9,
    "tenth": 10,
    "10th": 10,
}

_DEICTIC_RE = re.compile(
    r"^(?:that\s+(?:car|one|vehicle|listing)|this\s+(?:car|one|vehicle|listing)|"
    r"the\s+(?:current\s+)?(?:car|one|vehicle|listing)|current\s+(?:car|vehicle)|"
    r"its(?:\s+details|\s+specs?)?|it|that|this)$",
    re.IGNORECASE,
)

_PREVIOUS_RE = re.compile(
    r"\b(?:the\s+)?(?:previous|earlier|prior)\s+(?:car|vehicle|listing|one|option|result)\b",
    re.IGNORECASE,
)

_COMPARE_PAIR_RE = re.compile(r"\b(?:compare|vs\.?|versus|with|against)\b", re.IGNORECASE)

_SUBSTRING_DEICTIC_RE = re.compile(
    r"\b(?:this|that)\s+(?:car|one|vehicle|listing|suv|sedan|coupe)\b|\b(?:previous|earlier|prior)\s+(?:car|vehicle|listing|one)\b",
    re.IGNORECASE,
)

_EXPLICIT_ID_RE = re.compile(
    r"(?:listing\s*(?:#|id\s*[:#]?)?\s*|id\s*[:#]\s*|car\s*#\s*|#\s*)(\d+)\b",
    re.IGNORECASE,
)

_SAVED_RE = re.compile(r"\b(?:saved|favou?rite|bookmarked)\b", re.IGNORECASE)

_STOPWORDS = {
    "the",
    "a",
    "an",
    "car",
    "vehicle",
    "listing",
    "one",
    "that",
    "this",
    "please",
    "about",
    "show",
    "me",
    "tell",
    "what",
    "whats",
    "is",
    "on",
    "for",
    "saved",
    "favourite",
    "favorite",
    "bookmarked",
    "i",
    "my",
}


class ResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    NO_CONTEXT = "no_context"


class ReferenceResolutionResult(BaseModel):
    """Structured output of deterministic reference resolution."""

    status: ResolutionStatus
    listing_id: Optional[int] = None
    listing: Optional[ListingSummary] = None
    resolved_listings: list[ListingSummary] = Field(default_factory=list)
    candidate_ids: list[int] = Field(default_factory=list)
    resolution_method: str
    clarification_prompt: Optional[str] = None
    message: str


_ORDINAL_REGEX = re.compile(
    r"\b(?:"
    + "|".join(re.escape(w) for w in sorted(_ORDINAL_WORDS.keys(), key=len, reverse=True))
    + r"|last(?:\s+(?:one|car|vehicle|listing|result|option))?"
    + r"|(?:number|option|result|item)\s+\d+"
    + r"|\d+(?:st|nd|rd|th)"
    + r")\b",
    re.IGNORECASE,
)


def parse_ordinals(text: str) -> list[int]:
    """Extract all unique 1-based ordinal indices (-1 for 'last') in order of appearance."""
    norm = " ".join(text.strip().lower().split())
    seen: list[int] = []
    for m in _ORDINAL_REGEX.finditer(norm):
        token = m.group(0).strip()
        if token.startswith("last"):
            val = -1
        elif token in _ORDINAL_WORDS:
            val = _ORDINAL_WORDS[token]
        else:
            m_num = re.search(r"(\d+)", token)
            if not m_num:
                continue
            val = int(m_num.group(1))
            if val < 1:
                continue
        if val not in seen:
            seen.append(val)
    return seen


def _parse_ordinal(text: str) -> Optional[int]:
    """Parse first 1-based ordinal index or -1 for 'last' from natural language."""
    ords = parse_ordinals(text)
    return ords[0] if ords else None


def has_relative_reference(text: Optional[str]) -> bool:
    """Return True when user text relies on positional, saved, or deictic context rather than explicit #ID."""
    if not text or not text.strip():
        return False
    if _extract_explicit_id(text) is not None:
        return False
    return bool(
        parse_ordinals(text)
        or _PREVIOUS_RE.search(text)
        or _SAVED_RE.search(text)
        or _DEICTIC_RE.match(text.strip())
        or _SUBSTRING_DEICTIC_RE.search(text)
    )


def _extract_explicit_id(text: str) -> Optional[int]:
    m = _EXPLICIT_ID_RE.search(text.strip())
    if m:
        val = int(m.group(1))
        return val if val > 0 else None
    return None


def _format_candidate_options(candidates: list[ListingSummary], result_ids_order: list[int]) -> str:
    pos_map = {lid: i + 1 for i, lid in enumerate(result_ids_order)}
    parts: list[str] = []
    for item in candidates[:5]:
        pos_prefix = f"#{pos_map[item.listing_id]} " if item.listing_id in pos_map else ""
        dup_note = (
            f" [suspected duplicate group #{item.duplicate_group}]"
            if item.is_suspected_duplicate
            else ""
        )
        parts.append(
            f"{pos_prefix}(Listing ID {item.listing_id}: {item.year} {item.make} {item.model}{dup_note})"
        )
    return "; ".join(parts)


def _match_named_candidates(
    reference_text: str, candidates: list[ListingSummary]
) -> list[ListingSummary]:
    """Deterministically match make, model, trim, year, or colour within candidates."""
    raw_tokens = re.findall(r"[a-z0-9]+", reference_text.lower())
    query_tokens = [t for t in raw_tokens if t not in _STOPWORDS]
    if not query_tokens:
        return []

    required_groups: list[set[str]] = []
    for tok in query_tokens:
        alts = {tok}
        if tok in MAKE_ALIASES:
            target = MAKE_ALIASES[tok]
            alts.add(target)
        if tok in MODEL_ALIASES:
            target = MODEL_ALIASES[tok]
            alts.add(target)
        required_groups.append(alts)

    matches: list[ListingSummary] = []
    for item in candidates:
        haystack = " ".join(
            [
                str(item.year),
                item.make.lower(),
                item.model.lower(),
                item.trim.lower(),
                item.title.lower(),
                (item.colour or "").lower(),
                (item.spec_region or "").lower(),
            ]
        )
        hay_tokens = set(re.findall(r"[a-z0-9]+", haystack))
        # Every meaningful token group must match either token-wise or substring-wise in make/model
        all_match = True
        for group in required_groups:
            if not any(
                alt in hay_tokens or (len(alt) >= 3 and alt in haystack)
                for alt in group
            ):
                all_match = False
                break
        if all_match:
            matches.append(item)

    return matches


class ReferenceResolutionService:
    """Pure Python service resolving user references against persisted session state."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.sessions = SessionService(db_path)
        self.inventory = InventorySearchService(db_path)
        self.memory_repo = MemoryRepository(db_path)

    def resolve(
        self,
        *,
        session_id: str,
        user_id: str,
        reference_text: Optional[str] = None,
        ordinal: Optional[int] = None,
        explicit_listing_id: Optional[int] = None,
        update_focus: bool = True,
    ) -> ReferenceResolutionResult:
        """Resolve a vehicle reference deterministically using persisted session state."""
        session = self.sessions.require_user_session(session_id, user_id)

        # Load valid ordered summaries for session's last_result_ids (never unordered!)
        ordered_results = self.inventory.get_vehicle_summaries(session.last_result_ids)
        ordered_ids = [item.listing_id for item in ordered_results]

        # Validate current_vehicle against inventory (guard against stale IDs)
        focused_summary: Optional[ListingSummary] = None
        if session.current_vehicle is not None:
            found = self.inventory.get_vehicle_summaries([session.current_vehicle])
            focused_summary = found[0] if found else None

        text = (reference_text or "").strip()

        # 1. Explicit Listing ID (param or "#87" / "listing 87")
        target_id = explicit_listing_id or (_extract_explicit_id(text) if text else None)
        if target_id is not None:
            found = self.inventory.get_vehicle_summaries([target_id])
            if not found:
                return ReferenceResolutionResult(
                    status=ResolutionStatus.NOT_FOUND,
                    resolution_method="explicit_id",
                    clarification_prompt=f"Listing #{target_id} does not exist in inventory.",
                    message=f"Listing #{target_id} was not found.",
                )
            chosen = found[0]
            if update_focus:
                self.sessions.focus_vehicle(session_id, chosen.listing_id, user_id=user_id)
            return ReferenceResolutionResult(
                status=ResolutionStatus.RESOLVED,
                listing_id=chosen.listing_id,
                listing=chosen,
                resolved_listings=[chosen],
                candidate_ids=[chosen.listing_id],
                resolution_method="explicit_id",
                message=f"Resolved explicit listing #{chosen.listing_id} ({chosen.year} {chosen.make} {chosen.model}).",
            )

        # 1b. Previous / earlier vehicle reference ("previous car", "compare that with the previous vehicle")
        if text and _PREVIOUS_RE.search(text):
            focused_id = focused_summary.listing_id if focused_summary is not None else None
            prior_id: Optional[int] = None
            for msg in reversed(session.recent_messages):
                if len(msg.vehicle_cards) == 1:
                    cid = int(msg.vehicle_cards[0])
                    if cid != focused_id:
                        prior_id = cid
                        break
            if prior_id is None and focused_id is not None and focused_id in ordered_ids:
                idx_in_res = ordered_ids.index(focused_id)
                if idx_in_res > 0:
                    prior_id = ordered_ids[idx_in_res - 1]
            if prior_id is None:
                for msg in reversed(session.recent_messages):
                    for cid in msg.vehicle_cards or []:
                        if int(cid) != focused_id:
                            prior_id = int(cid)
                            break
                    if prior_id is not None:
                        break
            if prior_id is None:
                return ReferenceResolutionResult(
                    status=ResolutionStatus.NO_CONTEXT,
                    resolution_method="previous_vehicle",
                    clarification_prompt="I don't have an earlier vehicle to refer back to in this session yet.",
                    message="No previous vehicle found in session history.",
                )
            prev_found = self.inventory.get_vehicle_summaries([prior_id])
            if not prev_found:
                return ReferenceResolutionResult(
                    status=ResolutionStatus.NOT_FOUND,
                    resolution_method="previous_vehicle",
                    clarification_prompt=f"Previous listing #{prior_id} is no longer available.",
                    message=f"Previous listing #{prior_id} not found.",
                )
            prev_chosen = prev_found[0]
            if update_focus:
                self.sessions.focus_vehicle(session_id, prev_chosen.listing_id, user_id=user_id)
            if (
                focused_summary is not None
                and focused_summary.listing_id != prev_chosen.listing_id
                and _COMPARE_PAIR_RE.search(text)
            ):
                pair = [focused_summary, prev_chosen]
                return ReferenceResolutionResult(
                    status=ResolutionStatus.RESOLVED,
                    listing_id=prev_chosen.listing_id,
                    listing=prev_chosen,
                    resolved_listings=pair,
                    candidate_ids=[c.listing_id for c in pair],
                    resolution_method="previous_vehicle",
                    message=(
                        f"Resolved current listing #{focused_summary.listing_id} and previous listing "
                        f"#{prev_chosen.listing_id} ({prev_chosen.year} {prev_chosen.make} {prev_chosen.model})."
                    ),
                )
            return ReferenceResolutionResult(
                status=ResolutionStatus.RESOLVED,
                listing_id=prev_chosen.listing_id,
                listing=prev_chosen,
                resolved_listings=[prev_chosen],
                candidate_ids=[prev_chosen.listing_id],
                resolution_method="previous_vehicle",
                message=(
                    f"Resolved previous vehicle to listing #{prev_chosen.listing_id} "
                    f"({prev_chosen.year} {prev_chosen.make} {prev_chosen.model})."
                ),
            )

        # 2. Ordinal reference(s) ("first one", "second and third", "last one", ordinal=1)
        ord_list = [ordinal] if ordinal is not None else (parse_ordinals(text) if text else [])
        if ord_list:
            if not ordered_results:
                return ReferenceResolutionResult(
                    status=ResolutionStatus.NO_CONTEXT,
                    resolution_method="ordinal",
                    clarification_prompt="I don't have a recent search result list in this session yet. Please search for vehicles first.",
                    message="No previous search results available in session to resolve ordinal reference.",
                )
            resolved_items: list[ListingSummary] = []
            resolved_labels: list[str] = []
            for ord_idx in ord_list:
                if ord_idx == -1:
                    chosen_item = ordered_results[-1]
                    pos_label = "last"
                elif 1 <= ord_idx <= len(ordered_results):
                    chosen_item = ordered_results[ord_idx - 1]
                    pos_label = f"#{ord_idx}"
                else:
                    return ReferenceResolutionResult(
                        status=ResolutionStatus.NOT_FOUND,
                        candidate_ids=ordered_ids,
                        resolution_method="ordinal",
                        clarification_prompt=(
                            f"Only {len(ordered_results)} listing(s) were shown in the current results, "
                            f"so position #{ord_idx} is out of range."
                        ),
                        message=f"Ordinal #{ord_idx} out of range for {len(ordered_results)} result(s).",
                    )
                if chosen_item.listing_id not in [x.listing_id for x in resolved_items]:
                    resolved_items.append(chosen_item)
                    resolved_labels.append(pos_label)

            chosen = resolved_items[0]
            if len(resolved_items) == 1:
                if update_focus:
                    self.sessions.focus_vehicle(session_id, chosen.listing_id, user_id=user_id)
                return ReferenceResolutionResult(
                    status=ResolutionStatus.RESOLVED,
                    listing_id=chosen.listing_id,
                    listing=chosen,
                    resolved_listings=[chosen],
                    candidate_ids=[chosen.listing_id],
                    resolution_method="ordinal",
                    message=f"Resolved {resolved_labels[0]} result to listing #{chosen.listing_id} ({chosen.year} {chosen.make} {chosen.model}).",
                )

            desc = ", ".join(
                f"{lbl} -> Listing #{c.listing_id} ({c.year} {c.make} {c.model})"
                for lbl, c in zip(resolved_labels, resolved_items)
            )
            return ReferenceResolutionResult(
                status=ResolutionStatus.RESOLVED,
                listing_id=chosen.listing_id,
                listing=chosen,
                resolved_listings=resolved_items,
                candidate_ids=[c.listing_id for c in resolved_items],
                resolution_method="multi_ordinal",
                message=f"Resolved {len(resolved_items)} results: {desc}.",
            )

        # 3. Saved / favourite vehicle reference ("the one I saved")
        if text and _SAVED_RE.search(text):
            fav_ids = self.memory_repo.list_favourite_ids(user_id)
            fav_summaries = self.inventory.get_vehicle_summaries(fav_ids)
            if not fav_summaries:
                return ReferenceResolutionResult(
                    status=ResolutionStatus.NO_CONTEXT,
                    resolution_method="saved_vehicle",
                    clarification_prompt="You don't have any saved favourite vehicles yet.",
                    message="No saved vehicles found for user.",
                )
            # Allow narrowing by make/model if user said e.g. "the Honda I saved"
            narrowed = _match_named_candidates(text, fav_summaries)
            pool = narrowed if narrowed else fav_summaries
            if len(pool) == 1:
                chosen = pool[0]
                if update_focus:
                    self.sessions.focus_vehicle(session_id, chosen.listing_id, user_id=user_id)
                return ReferenceResolutionResult(
                    status=ResolutionStatus.RESOLVED,
                    listing_id=chosen.listing_id,
                    listing=chosen,
                    resolved_listings=[chosen],
                    candidate_ids=[chosen.listing_id],
                    resolution_method="saved_vehicle",
                    message=f"Resolved saved listing #{chosen.listing_id} ({chosen.year} {chosen.make} {chosen.model}).",
                )
            opts = _format_candidate_options(pool, ordered_ids)
            return ReferenceResolutionResult(
                status=ResolutionStatus.AMBIGUOUS,
                candidate_ids=[c.listing_id for c in pool],
                resolution_method="saved_vehicle",
                clarification_prompt=f"You have {len(pool)} saved vehicles: {opts}. Which one would you like?",
                message="Multiple saved vehicles match; clarification required.",
            )

        # 4. Deictic / focused vehicle ("that car", "this one", "it", or empty follow-up)
        if not text or _DEICTIC_RE.match(text):
            return self._resolve_focused_or_single(
                session_id=session_id,
                user_id=user_id,
                focused_summary=focused_summary,
                ordered_results=ordered_results,
                ordered_ids=ordered_ids,
                update_focus=update_focus,
            )

        # 5. Named make/model/trim reference ("the Audi", "the Mercedes", "the CR-V")
        #    Must resolve strictly against persisted session result context (or focused car).
        search_pool = list(ordered_results)
        if focused_summary is not None and focused_summary.listing_id not in ordered_ids:
            search_pool.insert(0, focused_summary)

        if not search_pool:
            return ReferenceResolutionResult(
                status=ResolutionStatus.NO_CONTEXT,
                resolution_method="name_match",
                clarification_prompt=(
                    f"I don't have a recent result list in this session to match '{text}'. "
                    "Would you like me to search the inventory for it?"
                ),
                message="No session result set available for named reference resolution.",
            )

        matched = _match_named_candidates(text, search_pool)
        if len(matched) == 1:
            chosen = matched[0]
            if update_focus:
                self.sessions.focus_vehicle(session_id, chosen.listing_id, user_id=user_id)
            return ReferenceResolutionResult(
                status=ResolutionStatus.RESOLVED,
                listing_id=chosen.listing_id,
                listing=chosen,
                resolved_listings=[chosen],
                candidate_ids=[chosen.listing_id],
                resolution_method="name_match",
                message=f"Resolved '{text}' to listing #{chosen.listing_id} ({chosen.year} {chosen.make} {chosen.model}).",
            )

        if len(matched) > 1:
            opts = _format_candidate_options(matched, ordered_ids)
            return ReferenceResolutionResult(
                status=ResolutionStatus.AMBIGUOUS,
                candidate_ids=[m.listing_id for m in matched],
                resolution_method="name_match",
                clarification_prompt=(
                    f"Multiple listings match '{text}': {opts}. "
                    "Please specify which position (e.g. first/second) or listing ID you mean."
                ),
                message=f"Ambiguous reference '{text}' matched {len(matched)} listings.",
            )

        # 5b. Full-sentence deictic fallback ("What is the mileage on this car?" -> focused/single)
        if _SUBSTRING_DEICTIC_RE.search(text) or re.search(r"\b(?:its|it)\b", text, re.IGNORECASE):
            return self._resolve_focused_or_single(
                session_id=session_id,
                user_id=user_id,
                focused_summary=focused_summary,
                ordered_results=ordered_results,
                ordered_ids=ordered_ids,
                update_focus=update_focus,
            )

        return ReferenceResolutionResult(
            status=ResolutionStatus.NOT_FOUND,
            candidate_ids=ordered_ids,
            resolution_method="name_match",
            clarification_prompt=(
                f"None of the {len(search_pool)} listing(s) in your current session context matched '{text}'."
            ),
            message=f"No match for '{text}' in current session results.",
        )

    def _resolve_focused_or_single(
        self,
        *,
        session_id: str,
        user_id: str,
        focused_summary: Optional[ListingSummary],
        ordered_results: list[ListingSummary],
        ordered_ids: list[int],
        update_focus: bool,
    ) -> ReferenceResolutionResult:
        if focused_summary is not None:
            return ReferenceResolutionResult(
                status=ResolutionStatus.RESOLVED,
                listing_id=focused_summary.listing_id,
                listing=focused_summary,
                resolved_listings=[focused_summary],
                candidate_ids=[focused_summary.listing_id],
                resolution_method="focused_vehicle",
                message=f"Resolved current focused listing #{focused_summary.listing_id} ({focused_summary.year} {focused_summary.make} {focused_summary.model}).",
            )
        if len(ordered_results) == 1:
            chosen = ordered_results[0]
            if update_focus:
                self.sessions.focus_vehicle(session_id, chosen.listing_id, user_id=user_id)
            return ReferenceResolutionResult(
                status=ResolutionStatus.RESOLVED,
                listing_id=chosen.listing_id,
                listing=chosen,
                resolved_listings=[chosen],
                candidate_ids=[chosen.listing_id],
                resolution_method="single_result",
                message=f"Resolved sole search result listing #{chosen.listing_id} ({chosen.year} {chosen.make} {chosen.model}).",
            )
        if len(ordered_results) > 1:
            opts = _format_candidate_options(ordered_results, ordered_ids)
            return ReferenceResolutionResult(
                status=ResolutionStatus.AMBIGUOUS,
                candidate_ids=ordered_ids,
                resolution_method="focused_vehicle",
                clarification_prompt=f"Multiple vehicles were shown ({opts}). Which one do you mean?",
                message="No focused vehicle set and multiple recent results exist.",
            )
        return ReferenceResolutionResult(
            status=ResolutionStatus.NO_CONTEXT,
            resolution_method="focused_vehicle",
            clarification_prompt="There is no active vehicle selected or recent search results yet. Which car are you looking for?",
            message="No focused vehicle or recent search results in session.",
        )
