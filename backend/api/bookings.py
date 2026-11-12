"""FastAPI routes for vehicle viewing bookings (proposal -> confirmation workflow)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from backend.api.deps import get_booking_service
from backend.models.entities import Booking
from backend.models.schemas import (
    ConfirmBookingRequest,
    ConfirmBookingResponse,
    ProposeBookingRequest,
    ProposeBookingResponse,
)
from backend.services.booking import BookingService
from backend.services.leads import mask_booking_for_buyer

router = APIRouter(tags=["bookings"])


@router.post("/bookings/propose", response_model=ProposeBookingResponse)
def propose_booking(
    payload: ProposeBookingRequest,
    service: BookingService = Depends(get_booking_service),
) -> ProposeBookingResponse:
    """Validate Dubai viewing rules and create a non-binding 'proposed' booking."""
    res = service.propose_booking(
        user_id=payload.user_id,
        listing_id=payload.listing_id,
        requested_date=payload.requested_date,
        requested_time=payload.requested_time,
        session_id=payload.session_id,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
    )
    return res.model_copy(update={"booking": mask_booking_for_buyer(res.booking)})


@router.post("/bookings/{booking_id}/confirm", response_model=ConfirmBookingResponse)
def confirm_booking(
    payload: ConfirmBookingRequest,
    booking_id: int = Path(gt=0),
    service: BookingService = Depends(get_booking_service),
) -> ConfirmBookingResponse:
    """Confirm a proposed viewing booking after explicit user confirmation."""
    res = service.confirm_booking(
        booking_id=booking_id,
        user_id=payload.user_id,
        confirm=payload.confirm,
        confirmation_text=payload.confirmation_text,
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
    )
    return res.model_copy(update={"booking": mask_booking_for_buyer(res.booking)})


@router.get("/bookings/{booking_id}", response_model=Booking)
def get_booking(
    booking_id: int = Path(gt=0),
    user_id: str = Query(..., min_length=1),
    service: BookingService = Depends(get_booking_service),
) -> Booking:
    """Retrieve a booking after enforcing user ownership."""
    masked = mask_booking_for_buyer(service.get_booking(booking_id, user_id=user_id))
    assert masked is not None
    return masked


@router.get("/users/{user_id}/bookings", response_model=list[Booking])
def list_user_bookings(
    user_id: str = Path(min_length=1),
    service: BookingService = Depends(get_booking_service),
) -> list[Booking]:
    """List all proposed and confirmed bookings belonging to a user."""
    return [
        masked
        for b in service.get_user_bookings(user_id)
        if (masked := mask_booking_for_buyer(b)) is not None
    ]
