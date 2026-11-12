"""Service layer for persistent session state and ownership isolation."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Optional

from backend.agent.guardrails import sanitize_message_content
from backend.inventory.search import InventorySearchService
from backend.memory.repository import MemoryRepository
from backend.memory.service import MemoryService
from backend.models.entities import Session, SessionMessage
from backend.models.schemas import SearchFilters, SearchResult
from backend.services.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)


class SessionService:
    """Business logic for session creation, ownership checks, and short-term state."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.repo = MemoryRepository(db_path)
        self.memory = MemoryService(db_path)
        self.inventory = InventorySearchService(db_path)

    def create_session(
        self, user_id: str, session_id: Optional[str] = None
    ) -> tuple[Session, bool]:
        """Create a fresh session for an existing user, or return matching owned session.

        A new session starts with clean short-term conversational context while leaving
        the user's long-term profile, preferences, and favourites untouched.
        """
        clean_uid = user_id.strip()
        if self.repo.get_user(clean_uid) is None:
            raise NotFoundError(f"User '{clean_uid}' not found.", error_code="user_not_found")

        sid = session_id.strip() if session_id and session_id.strip() else str(uuid.uuid4())
        existing = self.repo.get_session(sid)
        if existing is not None:
            if existing.user_id != clean_uid:
                raise ConflictError(
                    f"Session '{sid}' belongs to another user.",
                    error_code="session_user_mismatch",
                )
            return existing, False

        return self.repo.create_session(sid, clean_uid), True

    def get_session(self, session_id: str, *, user_id: str) -> Session:
        """Retrieve persisted session state, strictly enforcing ownership."""
        clean_uid = user_id.strip()
        if not clean_uid:
            raise BadRequestError("user_id is required.", error_code="missing_user_id")
        session = self.repo.get_session(session_id)
        if session is None:
            raise NotFoundError(
                f"Session '{session_id}' not found.", error_code="session_not_found"
            )
        if session.user_id != clean_uid:
            raise ForbiddenError(
                f"Session '{session_id}' does not belong to user '{clean_uid}'.",
                error_code="session_forbidden",
            )
        return session

    def require_user_session(self, session_id: str, user_id: str) -> Session:
        """Strictly require both an existing session and matching owner user_id."""
        return self.get_session(session_id, user_id=user_id)

    def focus_vehicle(
        self, session_id: str, listing_id: int, *, user_id: str
    ) -> Session:
        """Set session's current focused vehicle after validating ownership and listing."""
        self.get_session(session_id, user_id=user_id)
        if self.inventory.get_vehicle_details(listing_id) is None:
            raise NotFoundError(
                f"Listing {listing_id} not found.", error_code="listing_not_found"
            )
        self.repo.set_session_focus(session_id, listing_id)
        return self.get_session(session_id, user_id=user_id)

    def record_search_results(
        self,
        session_id: str,
        user_id: str,
        result_ids: list[int],
        *,
        active_filters: Optional[SearchFilters] = None,
    ) -> Session:
        """Persist ordered search result IDs and filters, replacing old reference context."""
        self.require_user_session(session_id, user_id)

        # Validate every ID against authoritative inventory, preserving exact order
        summaries = self.inventory.get_vehicle_summaries(result_ids)
        valid_ids = [s.listing_id for s in summaries]
        filters_dict = (
            active_filters.model_dump(exclude_none=True) if active_filters is not None else None
        )
        # New search replaces prior focused vehicle context
        self.repo.update_session_search(
            session_id,
            result_ids=valid_ids,
            active_filters=filters_dict,
            clear_focus=True,
        )
        return self.get_session(session_id, user_id=user_id)

    def search_in_session(
        self,
        session_id: str,
        user_id: str,
        filters: SearchFilters,
        *,
        limit: Optional[int] = None,
        query_text: Optional[str] = None,
    ) -> SearchResult:
        """Execute InventorySearchService, persist ordered session results, and log search history."""
        self.require_user_session(session_id, user_id)
        result = self.inventory.search_inventory(filters, limit=limit)
        ordered_ids = [item.listing_id for item in result.results]
        self.record_search_results(
            session_id,
            user_id,
            ordered_ids,
            active_filters=result.filters_applied,
        )
        self.memory.record_search(
            user_id=user_id,
            query_text=query_text,
            filters=result.filters_applied.model_dump(exclude_none=True),
            result_count=result.total_matches,
            listing_ids=ordered_ids,
        )
        return result

    def update_booking_draft(
        self, session_id: str, user_id: str, draft: Optional[dict[str, Any]]
    ) -> Session:
        """Persist booking draft state in session without confirming or reserving any slot."""
        self.require_user_session(session_id, user_id)
        self.repo.update_session_booking_draft(session_id, draft)
        return self.get_session(session_id, user_id=user_id)

    def add_message(
        self,
        session_id: str,
        user_id: str,
        role: str,
        content: str,
        vehicle_cards: Optional[list[int]] = None,
    ) -> SessionMessage:
        """Append a turn to persisted conversation history after verifying ownership."""
        self.require_user_session(session_id, user_id)
        if role not in {"user", "assistant", "system"}:
            raise BadRequestError(f"Invalid message role '{role}'.", error_code="invalid_role")
        safe_content = sanitize_message_content(content)
        return self.repo.append_message(
            session_id, role, safe_content, vehicle_cards=vehicle_cards
        )

    def get_recent_messages(
        self, session_id: str, user_id: str, *, limit: int = 20
    ) -> list[SessionMessage]:
        """Retrieve recent session conversation turns in chronological order."""
        self.require_user_session(session_id, user_id)
        return self.repo.get_recent_messages(session_id, limit=limit)

    def update_context_summary(
        self, session_id: str, user_id: str, context_summary: Optional[str]
    ) -> Session:
        """Persist a compact structured conversation summary on the session."""
        self.require_user_session(session_id, user_id)
        self.repo.update_session_context_summary(session_id, context_summary)
        return self.get_session(session_id, user_id=user_id)
