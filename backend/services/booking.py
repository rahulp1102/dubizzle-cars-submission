"""Deterministic vehicle viewing booking service (Asia/Dubai, proposal -> confirmation)."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from backend.config import settings
from backend.inventory.search import InventorySearchService
from backend.memory.repository import MemoryRepository
from backend.models.entities import Booking
from backend.models.schemas import (
    BookingAlternative,
    ConfirmBookingResponse,
    ProposeBookingResponse,
)
from backend.services.booking_repository import BookingLeadRepository
from backend.services.exceptions import ForbiddenError, NotFoundError
from backend.services.leads import LeadService, mask_phone, validate_and_normalize_phone
from backend.services.sessions import SessionService

logger = logging.getLogger(__name__)

DUBAI_TZ = ZoneInfo(settings.BOOKING_TIMEZONE)

_WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_MONTHS: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_VAGUE_TIME_WORDS = {
    "morning",
    "afternoon",
    "evening",
    "night",
    "tonight",
    "anytime",
    "whenever",
    "later",
    "soon",
    "asap",
    "early",
    "late",
    "midday",
    "sometime",
}

_EXPLICIT_CLOCK_TIME_RE = re.compile(
    r"(?:"
    r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*(?:am|pm))?\b|"
    r"\b(?:1[0-2]|0?[1-9])\s*(?:am|pm)\b|"
    r"\b(?:1[0-2]|0?[1-9])\s*o'?clock\b|"
    r"\b(?:12\s+)?(?:noon|midnight)\b"
    r")",
    re.IGNORECASE,
)


def has_explicit_clock_time(text: str) -> bool:
    """Return True when text contains an explicit clock time (e.g. '14:00', '2 PM', '2:30 PM')."""
    cleaned = text.strip().replace(".", "")
    return bool(_EXPLICIT_CLOCK_TIME_RE.search(cleaned))


def contains_vague_time_phrase(text: str) -> bool:
    """Return True when text contains a vague time-of-day expression."""
    tokens = set(re.findall(r"[a-z]+", text.lower()))
    return bool(tokens & _VAGUE_TIME_WORDS)

_VAGUE_CONFIRM_RE = re.compile(
    r"^(?:"
    r"ok(?:ay)?|sure|fine|alright|cool|great|nice|sounds\s+good|looks\s+good|maybe|perhaps|later|"
    r"(?:this\s+|next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"(?:\s+(?:morning|afternoon|evening))?|"
    r"morning|afternoon|evening"
    r")[.!?\s]*$",
    re.IGNORECASE,
)

_EXPLICIT_CONFIRM_RE = re.compile(
    r"\b(?:confirm|book\s+it|schedule\s+it|lock\s+it\s+in|yes,?\s+(?:please\s+)?(?:confirm|book|proceed|go\s+ahead))\b",
    re.IGNORECASE,
)


class _SlotValidationError(Exception):
    """Internal deterministic slot validation error carrying alternative anchor hints."""

    def __init__(
        self,
        message: str,
        *,
        anchor_date: Optional[date] = None,
        anchor_time: Optional[time] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.anchor_date = anchor_date
        self.anchor_time = anchor_time


def _to_dubai_now(now_fn: Optional[Callable[[], datetime]]) -> datetime:
    if now_fn is None:
        return datetime.now(DUBAI_TZ)
    dt = now_fn()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=DUBAI_TZ)
    return dt.astimezone(DUBAI_TZ)


def _parse_calendar_date_core(text: str, raw_date: str, today: date) -> Optional[date]:
    """Parse explicit non-weekday calendar date expressions (ISO, DMY, Month-Day)."""
    low = text.lower()

    # ISO YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
        parts = text.split("-")
        try:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError as exc:
            raise _SlotValidationError(
                f"'{raw_date}' is not a valid calendar date.",
                anchor_date=today,
            ) from exc

    # DD/MM/YYYY or DD-MM-YYYY
    m_dmy = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", text)
    if m_dmy:
        day_i, mon_i, year_i = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
        try:
            return date(year_i, mon_i, day_i)
        except ValueError as exc:
            raise _SlotValidationError(
                f"'{raw_date}' is not a valid calendar date (expected DD/MM/YYYY).",
                anchor_date=today,
            ) from exc

    # Month day forms: "14 Nov", "14 Nov 2026", "Nov 14", "November 14, 2026"
    cleaned_md = re.sub(r",", " ", low)
    cleaned_md = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", cleaned_md)
    cleaned_md = " ".join(cleaned_md.split())

    m_day_mon = re.fullmatch(r"(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?", cleaned_md)
    if m_day_mon and m_day_mon.group(2) in _MONTHS:
        day_i = int(m_day_mon.group(1))
        mon_i = _MONTHS[m_day_mon.group(2)]
        year_i = int(m_day_mon.group(3)) if m_day_mon.group(3) else today.year
        try:
            parsed = date(year_i, mon_i, day_i)
        except ValueError as exc:
            raise _SlotValidationError(
                f"'{raw_date}' is not a valid calendar date.",
                anchor_date=today,
            ) from exc
        if not m_day_mon.group(3) and parsed < today - timedelta(days=180):
            try:
                parsed = date(year_i + 1, mon_i, day_i)
            except ValueError as exc:
                raise _SlotValidationError(
                    f"'{raw_date}' is not a valid calendar date.",
                    anchor_date=today,
                ) from exc
        return parsed

    m_mon_day = re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:\s+(\d{4}))?", cleaned_md)
    if m_mon_day and m_mon_day.group(1) in _MONTHS:
        mon_i = _MONTHS[m_mon_day.group(1)]
        day_i = int(m_mon_day.group(2))
        year_i = int(m_mon_day.group(3)) if m_mon_day.group(3) else today.year
        try:
            parsed = date(year_i, mon_i, day_i)
        except ValueError as exc:
            raise _SlotValidationError(
                f"'{raw_date}' is not a valid calendar date.",
                anchor_date=today,
            ) from exc
        if not m_mon_day.group(3) and parsed < today - timedelta(days=180):
            try:
                parsed = date(year_i + 1, mon_i, day_i)
            except ValueError as exc:
                raise _SlotValidationError(
                    f"'{raw_date}' is not a valid calendar date.",
                    anchor_date=today,
                ) from exc
        return parsed

    return None


def parse_booking_date(raw_date: str, now_dubai: datetime) -> date:
    """Deterministically parse ISO, DD/MM/YYYY, weekday, or weekday+calendar date."""
    text = " ".join(raw_date.strip().split())
    low = text.lower()
    today = now_dubai.date()

    if not text:
        raise _SlotValidationError(
            "Please provide a specific date (e.g. 'tomorrow' or 'YYYY-MM-DD').",
            anchor_date=today,
        )

    if low == "today":
        return today
    if low == "tomorrow":
        return today + timedelta(days=1)
    if low in {"day after tomorrow", "the day after tomorrow"}:
        return today + timedelta(days=2)

    # Combined weekday + date (e.g. "Saturday, November 14, 2026" or "2026-11-14 (Saturday)")
    expected_wd: Optional[int] = None
    expected_wd_name: Optional[str] = None
    core_text = text

    m_lead_wd = re.match(r"^(?:(?:this|next)\s+)?([a-z]+)\s*,?\s+(.+)$", low)
    if m_lead_wd and m_lead_wd.group(1) in _WEEKDAYS:
        expected_wd_name = m_lead_wd.group(1)
        expected_wd = _WEEKDAYS[expected_wd_name]
        # Preserve clean slice from text after the leading weekday/prefix
        prefix_len = m_lead_wd.start(2)
        core_text = text[prefix_len:].strip(" ,-")
    else:
        m_trail_wd = re.match(r"^(.+?)\s*\(\s*([a-z]+)\s*\)$", low)
        if m_trail_wd and m_trail_wd.group(2) in _WEEKDAYS:
            expected_wd_name = m_trail_wd.group(2)
            expected_wd = _WEEKDAYS[expected_wd_name]
            core_text = text[: m_trail_wd.end(1)].strip(" ,-")

    if expected_wd is not None:
        core_low = core_text.lower()
        parsed_combo: Optional[date] = None
        if core_low == "today":
            parsed_combo = today
        elif core_low == "tomorrow":
            parsed_combo = today + timedelta(days=1)
        elif core_low in {"day after tomorrow", "the day after tomorrow"}:
            parsed_combo = today + timedelta(days=2)
        else:
            parsed_combo = _parse_calendar_date_core(core_text, raw_date, today)

        if parsed_combo is not None:
            if parsed_combo.weekday() != expected_wd:
                actual_wd = parsed_combo.strftime("%A")
                raise _SlotValidationError(
                    f"Date mismatch: {parsed_combo.isoformat()} is a {actual_wd}, "
                    f"not {str(expected_wd_name).title()}. Please provide a consistent date.",
                    anchor_date=parsed_combo,
                )
            return parsed_combo

    # Pure explicit calendar date
    parsed_direct = _parse_calendar_date_core(text, raw_date, today)
    if parsed_direct is not None:
        return parsed_direct

    # Pure weekday: "Saturday", "this Monday", "next Tuesday"
    m_wd = re.fullmatch(r"(?:(this|next)\s+)?([a-z]+)", low)
    if m_wd and m_wd.group(2) in _WEEKDAYS:
        prefix = m_wd.group(1)
        target_wd = _WEEKDAYS[m_wd.group(2)]
        delta = (target_wd - today.weekday()) % 7
        if prefix == "next" and delta == 0:
            delta = 7
        return today + timedelta(days=delta)

    raise _SlotValidationError(
        f"Could not recognise viewing date '{raw_date}'. Please provide a specific date "
        "(e.g. 'YYYY-MM-DD', 'tomorrow', or 'Saturday').",
        anchor_date=today,
    )


def parse_booking_time(raw_time: str) -> time:
    """Deterministically parse exact 24h/12h viewing start time and reject vague phrases."""
    text = " ".join(raw_time.strip().split())
    low = text.lower().replace(".", "")

    if not text:
        raise _SlotValidationError(
            "Please provide a specific viewing time between 08:00 and 19:00 Asia/Dubai."
        )

    tokens = set(re.findall(r"[a-z]+", low))
    if tokens & _VAGUE_TIME_WORDS:
        raise _SlotValidationError(
            f"'{raw_time}' is too vague for booking. Please choose an exact time between "
            "08:00 and 19:00 Asia/Dubai (for example '14:00' or '2:00 PM')."
        )

    if low in {"noon", "12 noon", "12pm noon"}:
        return time(12, 0)
    if low == "midnight":
        return time(0, 0)

    # 12-hour clock: "2pm", "2:30 pm", "08:00 am"
    m_12 = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", low)
    if m_12:
        hour = int(m_12.group(1))
        minute = int(m_12.group(2) or 0)
        ampm = m_12.group(3)
        if not (1 <= hour <= 12) or not (0 <= minute <= 59):
            raise _SlotValidationError(
                f"'{raw_time}' is not a valid 12-hour clock time."
            )
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
        return time(hour, minute)

    # 24-hour clock: "08:00", "14:30", "8:00"
    m_24 = re.fullmatch(r"(\d{1,2}):(\d{2})", low)
    if m_24:
        hour = int(m_24.group(1))
        minute = int(m_24.group(2))
        if not (0 <= hour <= 23) or not (0 <= minute <= 59):
            raise _SlotValidationError(
                f"'{raw_time}' is not a valid 24-hour clock time (expected HH:MM)."
            )
        return time(hour, minute)

    raise _SlotValidationError(
        f"Could not recognise viewing time '{raw_time}'. Please specify an exact time "
        "between 08:00 and 19:00 Asia/Dubai (e.g. '10:00' or '2:00 PM')."
    )


def validate_slot_rules(
    booking_date: date,
    start_time: time,
    now_dubai: datetime,
) -> tuple[datetime, datetime]:
    """Validate Dubai past timestamps, operating day/hours, and minimum lead time."""
    start_dt = datetime.combine(booking_date, start_time, tzinfo=DUBAI_TZ)
    end_dt = start_dt + timedelta(minutes=settings.BOOKING_VIEWING_DURATION_MINUTES)

    if start_dt <= now_dubai:
        raise _SlotValidationError(
            "Requested viewing date/time is in the past (Asia/Dubai).",
            anchor_date=now_dubai.date(),
            anchor_time=now_dubai.time().replace(second=0, microsecond=0),
        )

    if booking_date.weekday() not in settings.BOOKING_OPEN_DAYS:
        raise _SlotValidationError(
            "Viewings are available Monday through Saturday only (closed on Sundays).",
            anchor_date=booking_date + timedelta(days=1),
            anchor_time=time(settings.BOOKING_OPEN_HOUR + 1, 0),
        )

    open_dt = datetime.combine(
        booking_date, time(settings.BOOKING_OPEN_HOUR, 0), tzinfo=DUBAI_TZ
    )
    close_dt = datetime.combine(
        booking_date, time(settings.BOOKING_CLOSE_HOUR, 0), tzinfo=DUBAI_TZ
    )

    if start_dt < open_dt or end_dt > close_dt:
        raise _SlotValidationError(
            f"Viewing appointments must start between {settings.BOOKING_OPEN_HOUR:02d}:00 and "
            f"{settings.BOOKING_LAST_START_HOUR:02d}:00 Asia/Dubai "
            f"({settings.BOOKING_VIEWING_DURATION_MINUTES}-minute duration, closing at "
            f"{settings.BOOKING_CLOSE_HOUR:02d}:00).",
            anchor_date=booking_date,
            anchor_time=time(
                min(max(start_time.hour, settings.BOOKING_OPEN_HOUR), settings.BOOKING_LAST_START_HOUR),
                0,
            ),
        )

    min_allowed = now_dubai + timedelta(hours=settings.BOOKING_MIN_LEAD_TIME_HOURS)
    if start_dt < min_allowed:
        raise _SlotValidationError(
            f"Viewing bookings require at least {settings.BOOKING_MIN_LEAD_TIME_HOURS} hours "
            "advance notice (Asia/Dubai time).",
            anchor_date=min_allowed.date(),
            anchor_time=min_allowed.time().replace(second=0, microsecond=0),
        )

    return start_dt, end_dt


def is_explicit_confirmation(confirm: bool, confirmation_text: Optional[str]) -> bool:
    """Enforce explicit user confirmation and reject vague phrases like 'okay' or 'Saturday afternoon'."""
    if confirmation_text is not None:
        cleaned = " ".join(confirmation_text.strip().split())
        if cleaned and _VAGUE_CONFIRM_RE.match(cleaned):
            return False
        if not confirm:
            return bool(cleaned and _EXPLICIT_CONFIRM_RE.search(cleaned))
        return True
    return confirm is True


class BookingService:
    """Service layer enforcing deterministic Dubai booking rules and proposal -> confirm workflow."""

    def __init__(
        self,
        db_path: Optional[Path | str] = None,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
        exports_dir: Optional[Path | str] = None,
    ) -> None:
        self.db_path = db_path
        self.now_fn = now_fn
        self.repo = BookingLeadRepository(db_path)
        self.memory_repo = MemoryRepository(db_path)
        self.inventory = InventorySearchService(db_path)
        self.sessions = SessionService(db_path)
        self.leads = LeadService(db_path, exports_dir=exports_dir)

    def _now(self) -> datetime:
        return _to_dubai_now(self.now_fn)

    def _suggest_alternatives(
        self,
        *,
        listing_id: int,
        user_id: str,
        now_dubai: datetime,
        anchor_date: Optional[date] = None,
        anchor_time: Optional[time] = None,
        limit: int = 3,
    ) -> list[BookingAlternative]:
        """Find up to `limit` valid, conflict-free hourly slots near anchor_date/time."""
        if self.repo.has_user_confirmed_for_listing(user_id, listing_id):
            return []

        duration = timedelta(minutes=settings.BOOKING_VIEWING_DURATION_MINUTES)
        min_allowed = now_dubai + timedelta(hours=settings.BOOKING_MIN_LEAD_TIME_HOURS)

        start_date = anchor_date or min_allowed.date()
        if start_date < min_allowed.date():
            start_date = min_allowed.date()

        preferred_hour = anchor_time.hour if anchor_time else settings.BOOKING_OPEN_HOUR + 2
        preferred_hour = min(
            max(preferred_hour, settings.BOOKING_OPEN_HOUR), settings.BOOKING_LAST_START_HOUR
        )

        # Order candidate hours on anchor date closest to preferred_hour, then next days ascending
        all_hours = list(range(settings.BOOKING_OPEN_HOUR, settings.BOOKING_LAST_START_HOUR + 1))
        alternatives: list[BookingAlternative] = []
        seen_slots: set[tuple[str, str]] = set()

        for day_offset in range(0, 10):
            if len(alternatives) >= limit:
                break
            cand_date = start_date + timedelta(days=day_offset)
            if cand_date.weekday() not in settings.BOOKING_OPEN_DAYS:
                continue

            intervals = self.repo.list_confirmed_intervals_for_date(
                listing_id, user_id, cand_date.isoformat()
            )
            if day_offset == 0:
                hours_order = sorted(
                    all_hours,
                    key=lambda h: (abs(h - preferred_hour), 0 if h >= preferred_hour else 1, h),
                )
            else:
                hours_order = sorted(
                    all_hours,
                    key=lambda h: (0 if h == preferred_hour else 1, h),
                )

            for h in hours_order:
                if len(alternatives) >= limit:
                    break
                cand_start_dt = datetime.combine(cand_date, time(h, 0), tzinfo=DUBAI_TZ)
                cand_end_dt = cand_start_dt + duration
                if cand_start_dt < min_allowed:
                    continue
                if cand_end_dt.hour > settings.BOOKING_CLOSE_HOUR or (
                    cand_end_dt.hour == settings.BOOKING_CLOSE_HOUR and cand_end_dt.minute > 0
                ):
                    continue

                st_str = cand_start_dt.strftime("%H:%M")
                et_str = cand_end_dt.strftime("%H:%M")
                if any(iv_s < et_str and iv_e > st_str for iv_s, iv_e in intervals):
                    continue

                slot_key = (cand_date.isoformat(), st_str)
                if slot_key in seen_slots:
                    continue
                seen_slots.add(slot_key)
                alternatives.append(
                    BookingAlternative(
                        booking_date=cand_date.isoformat(),
                        start_time=st_str,
                        end_time=et_str,
                        display=(
                            f"{cand_date.strftime('%a %d %b %Y')} {st_str}-{et_str} "
                            f"({settings.BOOKING_TIMEZONE})"
                        ),
                    )
                )

        return alternatives

    def propose_booking(
        self,
        *,
        user_id: str,
        listing_id: int,
        requested_date: str,
        requested_time: str,
        session_id: Optional[str] = None,
        customer_name: Optional[str] = None,
        customer_phone: Optional[str] = None,
    ) -> ProposeBookingResponse:
        """Validate slot rules deterministically and create a non-binding 'proposed' booking."""
        if self.memory_repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")

        vehicle = self.inventory.get_vehicle_details(listing_id)
        if vehicle is None:
            raise NotFoundError(
                f"Listing {listing_id} not found.", error_code="listing_not_found"
            )

        if session_id is not None:
            self.sessions.require_user_session(session_id, user_id)

        norm_phone: Optional[str] = None
        if customer_phone is not None and customer_phone.strip():
            norm_phone = validate_and_normalize_phone(customer_phone)
        clean_name = customer_name.strip() if customer_name and customer_name.strip() else None

        now_dubai = self._now()

        # Check duplicate confirmed booking by same user on same vehicle upfront
        if self.repo.has_user_confirmed_for_listing(user_id, listing_id):
            return ProposeBookingResponse(
                success=False,
                booking=None,
                message=(
                    f"You already have a confirmed viewing booking for {vehicle.title} "
                    f"(listing #{listing_id}). Duplicate bookings for the same vehicle are not allowed."
                ),
                alternatives=[],
            )

        parsed_date: Optional[date] = None
        parsed_time: Optional[time] = None
        try:
            parsed_date = parse_booking_date(requested_date, now_dubai)
            parsed_time = parse_booking_time(requested_time)
            start_dt, end_dt = validate_slot_rules(parsed_date, parsed_time, now_dubai)
        except _SlotValidationError as exc:
            alts = self._suggest_alternatives(
                listing_id=listing_id,
                user_id=user_id,
                now_dubai=now_dubai,
                anchor_date=parsed_date or exc.anchor_date,
                anchor_time=parsed_time or exc.anchor_time,
            )
            return ProposeBookingResponse(
                success=False,
                booking=None,
                message=exc.message,
                alternatives=alts,
            )

        date_str = start_dt.date().isoformat()
        start_str = start_dt.strftime("%H:%M")
        end_str = end_dt.strftime("%H:%M")

        if self.repo.has_vehicle_confirmed_overlap(listing_id, date_str, start_str, end_str):
            alts = self._suggest_alternatives(
                listing_id=listing_id,
                user_id=user_id,
                now_dubai=now_dubai,
                anchor_date=start_dt.date(),
                anchor_time=start_dt.time(),
            )
            return ProposeBookingResponse(
                success=False,
                booking=None,
                message=(
                    f"Listing #{listing_id} already has a confirmed viewing on {date_str} "
                    f"around {start_str} ({settings.BOOKING_TIMEZONE})."
                ),
                alternatives=alts,
            )

        if self.repo.has_user_confirmed_overlap(user_id, date_str, start_str, end_str):
            alts = self._suggest_alternatives(
                listing_id=listing_id,
                user_id=user_id,
                now_dubai=now_dubai,
                anchor_date=start_dt.date(),
                anchor_time=start_dt.time(),
            )
            return ProposeBookingResponse(
                success=False,
                booking=None,
                message=(
                    f"You already have another confirmed viewing on {date_str} around "
                    f"{start_str} ({settings.BOOKING_TIMEZONE})."
                ),
                alternatives=alts,
            )

        booking = self.repo.create_proposed_booking(
            user_id=user_id,
            listing_id=listing_id,
            booking_date=date_str,
            start_time=start_str,
            end_time=end_str,
            duration_minutes=settings.BOOKING_VIEWING_DURATION_MINUTES,
            timezone=settings.BOOKING_TIMEZONE,
            session_id=session_id,
            customer_name=clean_name,
            customer_phone=norm_phone,
        )

        if session_id is not None:
            self.sessions.update_booking_draft(
                session_id,
                user_id,
                {
                    "booking_id": booking.booking_id,
                    "listing_id": booking.listing_id,
                    "booking_date": booking.booking_date,
                    "start_time": booking.start_time,
                    "end_time": booking.end_time,
                    "status": booking.status.value,
                },
            )

        logger.info(
            "Proposed booking booking_id=%s user_id=%s listing_id=%s slot=%s %s-%s phone=%s",
            booking.booking_id,
            user_id,
            listing_id,
            date_str,
            start_str,
            end_str,
            mask_phone(norm_phone),
        )

        self.leads.sync_user_lead(
            user_id,
            name=clean_name,
            phone=norm_phone,
            listing_id=listing_id,
            booking_id=booking.booking_id,
        )

        return ProposeBookingResponse(
            success=True,
            booking=booking,
            message=(
                f"Viewing slot proposed for {vehicle.title} (#{listing_id}) on "
                f"{date_str} from {start_str} to {end_str} ({settings.BOOKING_TIMEZONE}). "
                "Your slot is NOT confirmed yet—please confirm explicitly to lock it in."
            ),
            alternatives=[],
        )

    def confirm_booking(
        self,
        *,
        booking_id: int,
        user_id: str,
        confirm: bool = False,
        confirmation_text: Optional[str] = None,
        customer_name: Optional[str] = None,
        customer_phone: Optional[str] = None,
    ) -> ConfirmBookingResponse:
        """Confirm a previously proposed booking inside a serialised SQLite transaction."""
        if self.memory_repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")

        existing = self.repo.get_booking_by_id(booking_id)
        if existing is None:
            raise NotFoundError(
                f"Booking {booking_id} not found.", error_code="booking_not_found"
            )
        if existing.user_id != user_id:
            raise ForbiddenError(
                "You do not have permission to confirm this booking.",
                error_code="booking_forbidden",
            )

        if not is_explicit_confirmation(confirm, confirmation_text):
            return ConfirmBookingResponse(
                success=False,
                booking=existing,
                message=(
                    "Booking confirmation was not completed because an explicit confirmation "
                    "is required (vague replies like 'okay' or 'Saturday afternoon' are not treated "
                    "as final confirmation). Please reply clearly to confirm."
                ),
                alternatives=[],
            )

        norm_phone: Optional[str] = None
        if customer_phone is not None and customer_phone.strip():
            norm_phone = validate_and_normalize_phone(customer_phone)
        clean_name = customer_name.strip() if customer_name and customer_name.strip() else None

        # Re-validate slot rules against current Dubai clock before committing
        now_dubai = self._now()
        try:
            b_date = date.fromisoformat(existing.booking_date)
            hh, mm = (int(p) for p in existing.start_time.split(":"))
            b_time = time(hh, mm)
            validate_slot_rules(b_date, b_time, now_dubai)
        except _SlotValidationError as exc:
            alts = self._suggest_alternatives(
                listing_id=existing.listing_id,
                user_id=user_id,
                now_dubai=now_dubai,
                anchor_date=exc.anchor_date,
                anchor_time=exc.anchor_time,
            )
            return ConfirmBookingResponse(
                success=False,
                booking=existing,
                message=f"Proposed slot is no longer valid: {exc.message}",
                alternatives=alts,
            )

        outcome, result_booking = self.repo.confirm_booking_transaction(
            booking_id=booking_id,
            user_id=user_id,
            customer_name=clean_name,
            customer_phone=norm_phone,
        )

        if outcome == "not_found":
            raise NotFoundError(
                f"Booking {booking_id} not found.", error_code="booking_not_found"
            )
        if outcome == "forbidden":
            raise ForbiddenError(
                "You do not have permission to confirm this booking.",
                error_code="booking_forbidden",
            )

        if outcome == "already_confirmed":
            return ConfirmBookingResponse(
                success=False,
                booking=result_booking,
                message=(
                    f"Booking #{booking_id} is already confirmed for "
                    f"{result_booking.booking_date if result_booking else existing.booking_date} "
                    f"{result_booking.start_time if result_booking else existing.start_time}."
                ),
                alternatives=[],
            )

        if outcome in {"vehicle_conflict", "user_slot_conflict"}:
            b_ref = result_booking or existing
            alts = self._suggest_alternatives(
                listing_id=b_ref.listing_id,
                user_id=user_id,
                now_dubai=now_dubai,
                anchor_date=date.fromisoformat(b_ref.booking_date),
                anchor_time=time(*(int(x) for x in b_ref.start_time.split(":"))),
            )
            conflict_msg = (
                f"Listing #{b_ref.listing_id} was just booked by another user on "
                f"{b_ref.booking_date} at {b_ref.start_time} ({settings.BOOKING_TIMEZONE})."
                if outcome == "vehicle_conflict"
                else (
                    f"You already have a confirmed booking on {b_ref.booking_date} "
                    f"at {b_ref.start_time} ({settings.BOOKING_TIMEZONE})."
                )
            )
            return ConfirmBookingResponse(
                success=False,
                booking=b_ref,
                message=conflict_msg,
                alternatives=alts,
            )

        if outcome == "user_vehicle_duplicate":
            return ConfirmBookingResponse(
                success=False,
                booking=result_booking or existing,
                message=(
                    f"You already have an active confirmed booking for listing "
                    f"#{existing.listing_id}."
                ),
                alternatives=[],
            )

        if outcome != "confirmed" or result_booking is None:
            return ConfirmBookingResponse(
                success=False,
                booking=result_booking or existing,
                message=f"Booking #{booking_id} cannot be confirmed in its current state.",
                alternatives=[],
            )

        if result_booking.session_id:
            session = self.memory_repo.get_session(
                result_booking.session_id, include_messages=False
            )
            if session is not None and session.user_id == user_id:
                self.sessions.update_booking_draft(result_booking.session_id, user_id, None)

        logger.info(
            "Confirmed booking booking_id=%s user_id=%s listing_id=%s slot=%s %s-%s phone=%s",
            result_booking.booking_id,
            user_id,
            result_booking.listing_id,
            result_booking.booking_date,
            result_booking.start_time,
            result_booking.end_time,
            mask_phone(result_booking.customer_phone),
        )

        self.leads.sync_user_lead(
            user_id,
            name=result_booking.customer_name,
            phone=result_booking.customer_phone,
            listing_id=result_booking.listing_id,
            booking_id=result_booking.booking_id,
        )

        return ConfirmBookingResponse(
            success=True,
            booking=result_booking,
            message=(
                f"Confirmed viewing booking #{result_booking.booking_id} for listing "
                f"#{result_booking.listing_id} on {result_booking.booking_date} from "
                f"{result_booking.start_time} to {result_booking.end_time} "
                f"({settings.BOOKING_TIMEZONE})."
            ),
            alternatives=[],
        )

    def get_booking(self, booking_id: int, *, user_id: str) -> Booking:
        """Retrieve a booking after enforcing user ownership."""
        booking = self.repo.get_booking_by_id(booking_id)
        if booking is None:
            raise NotFoundError(
                f"Booking {booking_id} not found.", error_code="booking_not_found"
            )
        if booking.user_id != user_id:
            raise ForbiddenError(
                "You do not have permission to view this booking.",
                error_code="booking_forbidden",
            )
        return booking

    def get_user_bookings(self, user_id: str) -> list[Booking]:
        """List all bookings belonging to user_id."""
        if self.memory_repo.get_user(user_id) is None:
            raise NotFoundError(f"User '{user_id}' not found.", error_code="user_not_found")
        return self.repo.list_user_bookings(user_id)
