"""Service layer for user profiles, preferences access, and favourites."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from backend.inventory.search import InventorySearchService
from backend.memory.repository import MemoryRepository
from backend.memory.service import MemoryService
from backend.models.entities import ListingSummary
from backend.models.schemas import FavouriteResponse, UserResponse
from backend.services.exceptions import NotFoundError


class UserService:
    """Business logic for user lifecycle and saved favourites."""

    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        *,
        exports_dir: Optional[Path | str] = None,
    ) -> None:
        self.repo = MemoryRepository(db_path)
        self.memory = MemoryService(db_path, exports_dir=exports_dir)
        self.inventory = InventorySearchService(db_path)

    def create_or_get_user(
        self, user_id: str, name: Optional[str] = None
    ) -> tuple[UserResponse, bool]:
        """Create a user if new, or return existing profile. Returns (profile, created)."""
        clean_id = user_id.strip()
        existing = self.repo.get_user(clean_id)
        if existing is not None:
            if name and name.strip() and not existing.name:
                self.repo.update_user_name(clean_id, name.strip())
            return self.get_user_profile(clean_id), False

        clean_name = name.strip() if name and name.strip() else None
        self.repo.create_user(clean_id, clean_name)
        return self.get_user_profile(clean_id), True

    def get_user_profile(self, user_id: str) -> UserResponse:
        """Fetch user profile, preferences, favourite count, and booking count."""
        user = self.repo.get_user(user_id)
        if user is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")
        prefs = self.repo.get_preferences(user_id)
        fav_count, booking_count = self.repo.get_user_counts(user_id)
        return UserResponse(
            user_id=user.user_id,
            name=user.name,
            preferences=prefs,
            favourite_count=fav_count,
            booking_count=booking_count,
        )

    def delete_user(self, user_id: str, *, confirm: bool = False) -> None:
        """Forget Me: transactionally delete user and all associated data upon explicit confirmation."""
        self.memory.forget_user(user_id, confirm=confirm)

    def get_bookings_readonly(self, user_id: str) -> list[dict[str, Any]]:
        """Return read-only booking history for user context."""
        if self.repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")
        return self.repo.get_user_bookings_readonly(user_id)

    def add_favourite(self, user_id: str, listing_id: int) -> FavouriteResponse:
        """Save a listing to user's favourites after validating user and listing exist."""
        if self.repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")
        if self.inventory.get_vehicle_details(listing_id) is None:
            raise NotFoundError(
                f"Listing {listing_id} not found.", error_code="listing_not_found"
            )
        self.repo.add_favourite(user_id, listing_id)
        return FavouriteResponse(user_id=user_id, listing_id=listing_id, saved=True)

    def remove_favourite(self, user_id: str, listing_id: int) -> None:
        """Remove a saved listing from user's favourites."""
        if self.repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")
        removed = self.repo.remove_favourite(user_id, listing_id)
        if not removed:
            raise NotFoundError(
                f"Listing {listing_id} is not in user '{user_id}' favourites.",
                error_code="favourite_not_found",
            )

    def list_favourites(self, user_id: str) -> list[ListingSummary]:
        """Return full ListingSummary objects for user's saved favourites."""
        if self.repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")
        fav_ids = self.repo.list_favourite_ids(user_id)
        return self.inventory.get_vehicle_summaries(fav_ids)
