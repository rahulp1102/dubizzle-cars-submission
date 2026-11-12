"""FastAPI routes for lead management, scoring, and CSV export."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from fastapi.responses import FileResponse

from backend.api.deps import get_lead_service, require_admin_access
from backend.models.schemas import LeadDetailResponse, LeadResponse, UpsertLeadRequest
from backend.services.exceptions import BadRequestError
from backend.services.leads import LeadService, mask_lead_for_buyer

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("", response_model=LeadResponse, dependencies=[Depends(require_admin_access)])
def list_leads(service: LeadService = Depends(get_lead_service)) -> LeadResponse:
    """List all leads stored in SQLite (administrative access only)."""
    leads = service.list_leads()
    return LeadResponse(leads=leads, total=len(leads))


@router.get("/export", dependencies=[Depends(require_admin_access)])
def export_leads_csv(service: LeadService = Depends(get_lead_service)) -> FileResponse:
    """Export SQLite leads to CSV and return the generated leads.csv file (administrative access only)."""
    path = service.export_leads_csv()
    return FileResponse(
        path=str(path),
        media_type="text/csv",
        filename="leads.csv",
    )


@router.get("/{user_id}", response_model=LeadDetailResponse)
def get_lead(
    user_id: str = Path(min_length=1),
    service: LeadService = Depends(get_lead_service),
) -> LeadDetailResponse:
    """Retrieve a single user's lead record (phone masked) and next qualification question."""
    res = service.get_lead(user_id)
    return res.model_copy(update={"lead": mask_lead_for_buyer(res.lead)})


@router.put("/{user_id}", response_model=LeadDetailResponse)
def upsert_lead(
    payload: UpsertLeadRequest,
    user_id: str = Path(min_length=1),
    service: LeadService = Depends(get_lead_service),
) -> LeadDetailResponse:
    """Create or update a single lead record per user_id, score it, and refresh leads.csv."""
    if payload.user_id is not None and payload.user_id != user_id:
        raise BadRequestError(
            "Payload user_id does not match URL path user_id.",
            error_code="user_id_mismatch",
        )
    res = service.upsert_lead(
        user_id=user_id,
        name=payload.name,
        phone=payload.phone,
        budget=payload.budget,
        currency=payload.currency,
        preferred_makes=payload.preferred_makes,
        preferred_models=payload.preferred_models,
        preferred_body_type=payload.preferred_body_type,
        must_have_features=payload.must_have_features,
        purchase_timeline=payload.purchase_timeline,
        financing_interest=payload.financing_interest,
        listing_ids=payload.listing_ids,
        booking_id=payload.booking_id,
    )
    return res.model_copy(update={"lead": mask_lead_for_buyer(res.lead)})
