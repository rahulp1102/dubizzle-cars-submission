"""FastAPI routes for vehicle inventory search and listing details."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Path, Query

from backend.api.deps import get_inventory_service
from backend.inventory.search import InventorySearchService
from backend.models.entities import ListingSummary
from backend.models.schemas import (
    InventoryDetailResponse,
    MakeCountItem,
    SearchFilters,
    SearchResult,
)
from backend.services.exceptions import BadRequestError, NotFoundError

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("", response_model=SearchResult)
def search_inventory(
    make: Optional[str] = Query(default=None),
    model: Optional[str] = Query(default=None),
    min_year: Optional[int] = Query(default=None),
    max_year: Optional[int] = Query(default=None),
    min_price: Optional[float] = Query(default=None),
    max_price: Optional[float] = Query(default=None),
    min_mileage: Optional[float] = Query(default=None),
    max_mileage: Optional[float] = Query(default=None),
    spec_region: Optional[str] = Query(default=None),
    body_type: Optional[str] = Query(default=None),
    transmission: Optional[str] = Query(default=None),
    colour: Optional[str] = Query(default=None),
    warranty: Optional[bool] = Query(default=None),
    free_text: Optional[str] = Query(default=None),
    limit: Optional[int] = Query(default=None, ge=1, le=100),
    service: InventorySearchService = Depends(get_inventory_service),
) -> SearchResult:
    """Search inventory with structured filters and FTS5 relevance ranking."""
    filters = SearchFilters(
        make=make,
        model=model,
        min_year=min_year,
        max_year=max_year,
        min_price=min_price,
        max_price=max_price,
        min_mileage=min_mileage,
        max_mileage=max_mileage,
        spec_region=spec_region,
        body_type=body_type,
        transmission=transmission,
        colour=colour,
        warranty=warranty,
        free_text=free_text,
    )
    return service.search_inventory(filters, limit=limit)


@router.get("/makes", response_model=list[MakeCountItem])
def list_makes(
    service: InventorySearchService = Depends(get_inventory_service),
) -> list[MakeCountItem]:
    """Return all available makes and listing counts."""
    return [MakeCountItem(**row) for row in service.get_inventory_makes()]


@router.get("/suggested-prompts", response_model=list[str])
def get_suggested_prompts(
    service: InventorySearchService = Depends(get_inventory_service),
) -> list[str]:
    """Return grounded suggested search prompts from live inventory in a single request."""
    return service.get_suggested_prompts()


@router.get("/summaries", response_model=list[ListingSummary])
def get_listing_summaries(
    ids: Optional[list[int]] = Query(default=None),
    service: InventorySearchService = Depends(get_inventory_service),
) -> list[ListingSummary]:
    """Retrieve DB-backed ListingSummary cards in order for explicit listing IDs."""
    if not ids:
        return []
    if any(item <= 0 for item in ids):
        raise BadRequestError(
            "Listing IDs must be positive integers.", error_code="invalid_listing_id"
        )
    return service.get_vehicle_summaries(ids)


@router.get("/{listing_id}", response_model=InventoryDetailResponse)
def get_listing_detail(
    listing_id: int = Path(ge=1),
    service: InventorySearchService = Depends(get_inventory_service),
) -> InventoryDetailResponse:
    """Retrieve full details and evidence-backed attributes for one listing."""
    listing = service.get_vehicle_details(listing_id)
    if listing is None:
        raise NotFoundError(f"Listing {listing_id} not found.", error_code="listing_not_found")
    return InventoryDetailResponse(listing=listing)
