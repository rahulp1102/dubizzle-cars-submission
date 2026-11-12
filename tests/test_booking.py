"""Automated offline tests for Phase 7 deterministic viewing bookings."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from backend.agent.llm_client import LLMClient, LLMMessage, LLMResponse, LLMToolCall, ToolSchema
from backend.agent.orchestrator import AgentOrchestrator
from backend.db import get_connection, get_db, init_db
from backend.inventory.ingestion import ingest_inventory
from backend.inventory.search import InventorySearchService
from backend.main import create_app
from backend.memory.repository import MemoryRepository
from backend.models.entities import BookingStatus
from backend.models.schemas import SearchFilters
from backend.services.booking import (
    DUBAI_TZ,
    BookingService,
    _SlotValidationError,
    parse_booking_date,
)
from backend.services.exceptions import ForbiddenError, NotFoundError
from backend.services.sessions import SessionService
from backend.services.users import UserService


class _ScriptedLLMClient(LLMClient):
    """Offline mock LLM returning pre-scripted responses."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)

    def generate(
        self,
        *,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[ToolSchema],
    ) -> LLMResponse:
        if not self._responses:
            raise AssertionError("ScriptedLLMClient ran out of scripted responses.")
        return self._responses.pop(0)


@pytest.fixture()
def booking_env(temp_db: Path, tmp_path: Path) -> dict[str, Any]:
    ingest_inventory(db_path=temp_db)
    users = UserService(temp_db)
    users.create_or_get_user("u_ali", "Ali")
    users.create_or_get_user("u_sara", "Sara")
    sessions = SessionService(temp_db)
    s_ali, _ = sessions.create_session("u_ali", "s_ali")
    listings = InventorySearchService(temp_db).search_inventory(SearchFilters(), limit=5).results
    exports_dir = tmp_path / "exports"
    # Fixed clock: Thursday 2026-11-12 09:00 Asia/Dubai
    def fixed_now() -> datetime:
        return datetime(2026, 11, 12, 9, 0, tzinfo=DUBAI_TZ)
    return {
        "db": temp_db,
        "exports_dir": exports_dir,
        "now_fn": fixed_now,
        "listing_id": listings[0].listing_id,
        "listing_id_2": listings[1].listing_id,
        "session_id": s_ali.session_id,
    }


def test_valid_mon_sat_hours_and_boundaries(booking_env: dict[str, Any]) -> None:
    svc = BookingService(
        booking_env["db"],
        now_fn=booking_env["now_fn"],
        exports_dir=booking_env["exports_dir"],
    )
    lid = booking_env["listing_id"]
    sid = booking_env["session_id"]

    # Boundary 08:00 next open day (Friday 2026-11-13)
    res_early = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="2026-11-13",
        requested_time="08:00",
        session_id=sid,
    )
    assert res_early.success is True
    assert res_early.booking is not None
    assert res_early.booking.status == BookingStatus.PROPOSED
    assert res_early.booking.start_time == "08:00"
    assert res_early.booking.end_time == "09:00"
    assert res_early.booking.timezone == "Asia/Dubai"

    # Boundary 19:00 (latest valid start, ends 20:00) on Saturday 2026-11-14
    res_late = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="Saturday",
        requested_time="7:00 PM",
    )
    assert res_late.success is True
    assert res_late.booking is not None
    assert res_late.booking.booking_date == "2026-11-14"
    assert res_late.booking.start_time == "19:00"
    assert res_late.booking.end_time == "20:00"

    # Natural language "tomorrow" + DD/MM/YYYY + Month Day
    res_tmrw = svc.propose_booking(
        user_id="u_sara",
        listing_id=lid,
        requested_date="tomorrow",
        requested_time="2:00 pm",
    )
    assert res_tmrw.success is True
    assert res_tmrw.booking is not None
    assert res_tmrw.booking.booking_date == "2026-11-13"
    assert res_tmrw.booking.start_time == "14:00"


def test_sunday_hours_past_lead_time_and_invalid_nl_dates_rejected(
    booking_env: dict[str, Any],
) -> None:
    svc = BookingService(
        booking_env["db"],
        now_fn=booking_env["now_fn"],
        exports_dir=booking_env["exports_dir"],
    )
    lid = booking_env["listing_id"]

    # Sunday 2026-11-15 -> rejected + Monday alternatives
    sun = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="2026-11-15",
        requested_time="10:00",
    )
    assert sun.success is False
    assert "Sunday" in sun.message
    assert len(sun.alternatives) > 0
    assert all(
        datetime.fromisoformat(a.booking_date).weekday() != 6 for a in sun.alternatives
    )

    # Before 08:00
    too_early = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="2026-11-13",
        requested_time="07:30",
    )
    assert too_early.success is False
    assert "08:00" in too_early.message
    assert len(too_early.alternatives) > 0

    # At or after 19:01 (20:00 ends at 21:00)
    too_late = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="2026-11-13",
        requested_time="20:00",
    )
    assert too_late.success is False
    assert len(too_late.alternatives) > 0

    # Past date/time
    past = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="2026-11-11",
        requested_time="15:00",
    )
    assert past.success is False
    assert "past" in past.message.lower()
    assert len(past.alternatives) > 0

    # Minimum lead time (< 2 hours from 09:00 -> 10:30 rejected, 11:00 valid)
    short_lead = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="today",
        requested_time="10:30",
    )
    assert short_lead.success is False
    assert "2 hours" in short_lead.message
    assert len(short_lead.alternatives) > 0

    exact_lead = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="today",
        requested_time="11:00",
    )
    assert exact_lead.success is True

    # Invalid calendar date & vague time phrase
    bad_cal = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="2026-02-30",
        requested_time="10:00",
    )
    assert bad_cal.success is False
    assert len(bad_cal.alternatives) > 0

    vague_time = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="Saturday",
        requested_time="Saturday afternoon",
    )
    assert vague_time.success is False
    assert "vague" in vague_time.message.lower()
    assert len(vague_time.alternatives) > 0


def test_dubai_timezone_cross_midnight_and_dst_conversion(
    booking_env: dict[str, Any],
) -> None:
    # 21:00 UTC on 2026-11-10 == 01:00 Asia/Dubai on 2026-11-11 (Wednesday)
    def utc_late() -> datetime:
        return datetime(2026, 11, 10, 21, 0, tzinfo=timezone.utc)
    svc_utc = BookingService(
        booking_env["db"],
        now_fn=utc_late,
        exports_dir=booking_env["exports_dir"],
    )
    res_today = svc_utc.propose_booking(
        user_id="u_ali",
        listing_id=booking_env["listing_id"],
        requested_date="today",
        requested_time="10:00",
    )
    assert res_today.success is True
    assert res_today.booking is not None
    assert res_today.booking.booking_date == "2026-11-11"

    # America/New_York during US DST fall-back night (2026-11-01 00:30 EDT = 08:30 Dubai)
    def ny_clock() -> datetime:
        return datetime(2026, 11, 1, 0, 30, tzinfo=ZoneInfo("America/New_York"))
    svc_ny = BookingService(
        booking_env["db"],
        now_fn=ny_clock,
        exports_dir=booking_env["exports_dir"],
    )
    # 2026-11-01 is Sunday in Dubai -> tomorrow is Monday 2026-11-02
    res_mon = svc_ny.propose_booking(
        user_id="u_ali",
        listing_id=booking_env["listing_id"],
        requested_date="tomorrow",
        requested_time="10:00",
    )
    assert res_mon.success is True
    assert res_mon.booking is not None
    assert res_mon.booking.booking_date == "2026-11-02"


def test_proposal_does_not_reserve_and_vague_confirmations_rejected(
    booking_env: dict[str, Any],
) -> None:
    svc = BookingService(
        booking_env["db"],
        now_fn=booking_env["now_fn"],
        exports_dir=booking_env["exports_dir"],
    )
    lid = booking_env["listing_id"]
    sid = booking_env["session_id"]

    # Ali proposes Saturday 14:00 (does not reserve slot yet)
    prop_ali = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="2026-11-14",
        requested_time="14:00",
        session_id=sid,
    )
    assert prop_ali.success is True
    assert prop_ali.booking is not None
    bid_ali = prop_ali.booking.booking_id
    assert bid_ali is not None

    # Sara can still propose the exact same unconfirmed slot
    prop_sara = svc.propose_booking(
        user_id="u_sara",
        listing_id=lid,
        requested_date="2026-11-14",
        requested_time="14:00",
    )
    assert prop_sara.success is True

    # Vague confirmations must be rejected cleanly
    for kwargs in [
        {"confirm": False, "confirmation_text": None},
        {"confirm": False, "confirmation_text": "okay"},
        {"confirm": True, "confirmation_text": "okay"},
        {"confirm": True, "confirmation_text": "Saturday afternoon"},
    ]:
        bad_conf = svc.confirm_booking(booking_id=bid_ali, user_id="u_ali", **kwargs)
        assert bad_conf.success is False
        assert svc.get_booking(bid_ali, user_id="u_ali").status == BookingStatus.PROPOSED

    # Session booking draft still present while unconfirmed
    sess = SessionService(booking_env["db"]).get_session(sid, user_id="u_ali")
    assert sess.booking_draft is not None
    assert sess.booking_draft["booking_id"] == bid_ali

    # Explicit confirmation succeeds & clears draft
    ok_conf = svc.confirm_booking(
        booking_id=bid_ali,
        user_id="u_ali",
        confirm=True,
        confirmation_text="Yes, confirm my viewing",
        customer_name="Ali",
        customer_phone="0501234567",
    )
    assert ok_conf.success is True
    assert ok_conf.booking is not None
    assert ok_conf.booking.status == BookingStatus.CONFIRMED
    assert ok_conf.booking.customer_phone == "+971501234567"

    sess_after = SessionService(booking_env["db"]).get_session(sid, user_id="u_ali")
    assert sess_after.booking_draft is None


def test_confirm_revalidates_lead_time_and_prevents_duplicates(
    booking_env: dict[str, Any],
) -> None:
    clock = [datetime(2026, 11, 12, 9, 0, tzinfo=DUBAI_TZ)]
    svc = BookingService(
        booking_env["db"],
        now_fn=lambda: clock[0],
        exports_dir=booking_env["exports_dir"],
    )
    lid1 = booking_env["listing_id"]
    lid2 = booking_env["listing_id_2"]

    # Propose 12:00 today at 09:00 (3h lead -> valid)
    prop = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid1,
        requested_date="today",
        requested_time="12:00",
    )
    assert prop.success is True and prop.booking and prop.booking.booking_id

    # Advance clock to 10:30 (only 1.5h before 12:00) -> confirm must reject!
    clock[0] = datetime(2026, 11, 12, 10, 30, tzinfo=DUBAI_TZ)
    expired = svc.confirm_booking(
        booking_id=prop.booking.booking_id,
        user_id="u_ali",
        confirm=True,
    )
    assert expired.success is False
    assert "2 hours" in expired.message
    assert len(expired.alternatives) > 0

    # Confirm a valid Friday 10:00 booking on lid1
    prop_fri = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid1,
        requested_date="2026-11-13",
        requested_time="10:00",
    )
    assert prop_fri.success and prop_fri.booking and prop_fri.booking.booking_id
    assert (
        svc.confirm_booking(
            booking_id=prop_fri.booking.booking_id,
            user_id="u_ali",
            confirm=True,
        ).success
        is True
    )

    # Duplicate booking by same user for same vehicle blocked
    dup_same_car = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid1,
        requested_date="2026-11-14",
        requested_time="11:00",
    )
    assert dup_same_car.success is False
    assert "already have a confirmed" in dup_same_car.message

    # Same user overlapping slot on DIFFERENT vehicle blocked
    overlap_diff_car = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid2,
        requested_date="2026-11-13",
        requested_time="10:30",
    )
    assert overlap_diff_car.success is False
    assert len(overlap_diff_car.alternatives) > 0

    # Different user overlapping slot on SAME vehicle blocked
    other_user_overlap = svc.propose_booking(
        user_id="u_sara",
        listing_id=lid1,
        requested_date="2026-11-13",
        requested_time="10:00",
    )
    assert other_user_overlap.success is False
    assert len(other_user_overlap.alternatives) > 0
    assert all(
        not (a.booking_date == "2026-11-13" and a.start_time == "10:00")
        for a in other_user_overlap.alternatives
    )


def test_concurrent_confirm_booking_allows_only_one_winner(
    booking_env: dict[str, Any],
) -> None:
    svc = BookingService(
        booking_env["db"],
        now_fn=booking_env["now_fn"],
        exports_dir=booking_env["exports_dir"],
    )
    lid = booking_env["listing_id"]

    b_ali = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="2026-11-14",
        requested_time="11:00",
    )
    b_sara = svc.propose_booking(
        user_id="u_sara",
        listing_id=lid,
        requested_date="2026-11-14",
        requested_time="11:00",
    )
    assert b_ali.booking and b_ali.booking.booking_id
    assert b_sara.booking and b_sara.booking.booking_id

    barrier = threading.Barrier(2, timeout=5.0)
    outcomes: list[tuple[str, bool]] = []
    lock = threading.Lock()

    def _worker(uid: str, bid: int) -> None:
        thread_svc = BookingService(
            booking_env["db"],
            now_fn=booking_env["now_fn"],
            exports_dir=booking_env["exports_dir"],
        )
        barrier.wait()
        res = thread_svc.confirm_booking(booking_id=bid, user_id=uid, confirm=True)
        with lock:
            outcomes.append((uid, res.success))

    t1 = threading.Thread(target=_worker, args=("u_ali", b_ali.booking.booking_id))
    t2 = threading.Thread(target=_worker, args=("u_sara", b_sara.booking.booking_id))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(outcomes) == 2
    assert sum(1 for _, ok in outcomes if ok) == 1
    assert sum(1 for _, ok in outcomes if not ok) == 1

    with get_db(booking_env["db"]) as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE listing_id = ? AND status = 'confirmed'",
            (lid,),
        ).fetchone()[0]
    assert cnt == 1


def test_booking_api_ownership_and_restart_persistence(booking_env: dict[str, Any]) -> None:
    app = create_app(
        db_path=booking_env["db"],
        auto_ingest=False,
        now_fn=booking_env["now_fn"],
        exports_dir=booking_env["exports_dir"],
    )
    lid = booking_env["listing_id"]
    with TestClient(app) as client:
        prop = client.post(
            "/bookings/propose",
            json={
                "user_id": "u_ali",
                "listing_id": lid,
                "requested_date": "2026-11-14",
                "requested_time": "15:00",
                "session_id": booking_env["session_id"],
            },
        )
        assert prop.status_code == 200
        bid = prop.json()["booking"]["booking_id"]

        # Ownership enforcement
        assert client.get(f"/bookings/{bid}").status_code == 422
        assert client.get(f"/bookings/{bid}", params={"user_id": "u_sara"}).status_code == 403
        assert client.get(f"/bookings/{bid}", params={"user_id": "u_ali"}).status_code == 200
        assert client.get("/bookings/999999", params={"user_id": "u_ali"}).status_code == 404

        # Another user cannot confirm Ali's booking
        forb = client.post(
            f"/bookings/{bid}/confirm",
            json={"user_id": "u_sara", "confirm": True},
        )
        assert forb.status_code == 403

        # Ali confirms
        conf = client.post(
            f"/bookings/{bid}/confirm",
            json={"user_id": "u_ali", "confirm": True},
        )
        assert conf.status_code == 200
        assert conf.json()["success"] is True
        assert conf.json()["booking"]["status"] == "confirmed"

    # Simulated app restart: fresh app instance reads persisted confirmed booking
    app_restarted = create_app(
        db_path=booking_env["db"],
        auto_ingest=False,
        now_fn=booking_env["now_fn"],
        exports_dir=booking_env["exports_dir"],
    )
    with TestClient(app_restarted) as client2:
        listed = client2.get("/users/u_ali/bookings")
        assert listed.status_code == 200
        data = listed.json()
        assert len(data) == 1
        assert data[0]["booking_id"] == bid
        assert data[0]["status"] == "confirmed"

    # Direct service ownership check
    with pytest.raises(ForbiddenError):
        BookingService(booking_env["db"]).get_booking(bid, user_id="u_sara")
    with pytest.raises(NotFoundError):
        BookingService(booking_env["db"]).get_booking(999999, user_id="u_ali")


def test_agent_booking_workflow_guardrails_and_redacted_traces(
    booking_env: dict[str, Any],
) -> None:
    db = booking_env["db"]
    lid = booking_env["listing_id"]
    sid = booking_env["session_id"]

    mock_llm = _ScriptedLLMClient(
        [
            # Turn 1: propose booking, model falsely claims it's already confirmed -> guardrail catches!
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="propose_booking",
                        arguments={
                            "listing_id": lid,
                            "requested_date": "Saturday",
                            "requested_time": "10:00",
                            "customer_name": "Ali",
                            "customer_phone": "+971501234567",
                        },
                    )
                ]
            ),
            LLMResponse(text="Your viewing appointment is confirmed for Saturday at 10:00!"),
            # Turn 2: vague reply 'okay' -> confirm_booking blocked by Python!
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="confirm_booking",
                        arguments={"confirm": True},
                    )
                ]
            ),
            LLMResponse(text="Your viewing appointment is confirmed!"),
            # Turn 3: explicit confirmation -> confirmed + allowed by output guardrail
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="confirm_booking",
                        arguments={
                            "confirm": True,
                            "confirmation_text": "Yes, please confirm my viewing",
                        },
                    )
                ]
            ),
            LLMResponse(
                text=(
                    f"Your viewing appointment has been confirmed for listing #{lid} on "
                    "2026-11-14 from 10:00 to 11:00 Asia/Dubai."
                )
            ),
        ]
    )

    orch = AgentOrchestrator(
        db_path=db,
        llm_client=mock_llm,
        now_fn=booking_env["now_fn"],
        exports_dir=booking_env["exports_dir"],
    )

    # Turn 1
    r1 = orch.handle_turn(
        session_id=sid,
        user_id="u_ali",
        message=f"Can I propose Saturday 10:00 for listing {lid}? Phone +971501234567",
    )
    assert "cannot be confirmed directly" in r1.reply
    assert "propose_booking" in r1.tools_called

    # Turn 2: vague 'okay'
    r2 = orch.handle_turn(session_id=sid, user_id="u_ali", message="okay")
    assert "cannot be confirmed directly" in r2.reply
    bookings_mid = BookingService(db).get_user_bookings("u_ali")
    assert len(bookings_mid) == 1
    assert bookings_mid[0].status == BookingStatus.PROPOSED

    # Turn 3: explicit confirmation
    r3 = orch.handle_turn(
        session_id=sid,
        user_id="u_ali",
        message="Yes, please confirm my viewing",
    )
    assert "confirmed" in r3.reply.lower()
    assert "cannot be confirmed directly" not in r3.reply
    bookings_end = BookingService(db).get_user_bookings("u_ali")
    assert bookings_end[0].status == BookingStatus.CONFIRMED

    # Phone number must be redacted in agent_traces
    traces = MemoryRepository(db).get_session_traces(sid, limit=10)
    joined_traces = str(traces)
    assert "+971501234567" not in joined_traces
    assert "0501234567" not in joined_traces
    assert "[REDACTED_PHONE]" in joined_traces


def test_agent_propose_with_reference_and_booking_lookup_tools(
    booking_env: dict[str, Any],
) -> None:
    db = booking_env["db"]
    sid = booking_env["session_id"]

    mock_llm = _ScriptedLLMClient(
        [
            # Turn 1: search inventory
            LLMResponse(
                tool_calls=[
                    LLMToolCall(name="search_inventory", arguments={"limit": 3}),
                ]
            ),
            LLMResponse(text="Here are 3 available vehicles."),
            # Turn 2: propose viewing referencing "the first one" without explicit listing_id
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="propose_booking",
                        arguments={
                            "reference_text": "the first one",
                            "requested_date": "tomorrow",
                            "requested_time": "11:00",
                        },
                    )
                ]
            ),
            LLMResponse(text="Proposed viewing for tomorrow at 11:00. Please confirm explicitly."),
            # Turn 3: confirm + inspect via get_user_bookings and get_booking_details
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="confirm_booking",
                        arguments={
                            "confirm": True,
                            "confirmation_text": "Yes, confirm booking",
                        },
                    ),
                    LLMToolCall(name="get_user_bookings", arguments={}),
                    LLMToolCall(name="get_booking_details", arguments={"booking_id": 1}),
                ]
            ),
            LLMResponse(text="Confirmed and retrieved booking #1."),
        ]
    )

    orch = AgentOrchestrator(
        db_path=db,
        llm_client=mock_llm,
        now_fn=booking_env["now_fn"],
        exports_dir=booking_env["exports_dir"],
    )
    orch.handle_turn(session_id=sid, user_id="u_ali", message="Show me available cars")
    r2 = orch.handle_turn(
        session_id=sid,
        user_id="u_ali",
        message="Propose a viewing for the first one tomorrow at 11:00",
    )
    assert "propose_booking" in r2.tools_called
    assert len(r2.vehicle_cards) == 1

    r3 = orch.handle_turn(
        session_id=sid,
        user_id="u_ali",
        message="Yes, confirm booking",
    )
    assert set(["confirm_booking", "get_user_bookings", "get_booking_details"]).issubset(
        r3.tools_called
    )


def test_legacy_bookings_and_leads_schema_migration(tmp_path: Path) -> None:
    """Upgrading pre-Phase 7 bookings/leads tables preserves rows and adds Phase 7 columns/indexes."""
    legacy_db = tmp_path / "legacy_booking.db"
    conn = sqlite3.connect(str(legacy_db))
    conn.executescript(
        """
        CREATE TABLE users (user_id TEXT PRIMARY KEY, name TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE listings (
            listing_id INTEGER PRIMARY KEY AUTOINCREMENT,
            make TEXT NOT NULL, model TEXT NOT NULL, trim TEXT NOT NULL, year INTEGER NOT NULL,
            title TEXT NOT NULL, description_raw TEXT NOT NULL, description_display TEXT NOT NULL,
            photo_url TEXT NOT NULL, language TEXT NOT NULL DEFAULT 'en',
            duplicate_group INTEGER, content_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE bookings (
            booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            listing_id INTEGER NOT NULL REFERENCES listings(listing_id) ON DELETE CASCADE,
            booking_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL DEFAULT 60,
            status TEXT NOT NULL DEFAULT 'proposed',
            timezone TEXT NOT NULL DEFAULT 'Asia/Dubai',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT
        );
        CREATE TABLE leads (
            lead_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            name TEXT, phone TEXT, budget REAL, currency TEXT DEFAULT 'AED',
            preferred_makes TEXT, preferred_body_type TEXT, must_have_features TEXT,
            purchase_timeline TEXT, financing_interest TEXT, listing_ids TEXT,
            booking_id INTEGER REFERENCES bookings(booking_id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'cold',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO users (user_id, name) VALUES ('u_old', 'Omar');
        INSERT INTO listings (make, model, trim, year, title, description_raw, description_display, photo_url, content_hash)
            VALUES ('toyota', 'camry', 'le', 2023, '2023 Toyota Camry', 'Clean', 'Clean', '', 'hash1');
        INSERT INTO bookings (user_id, listing_id, booking_date, start_time, end_time, status)
            VALUES ('u_old', 1, '2026-11-20', '10:00', '11:00', 'confirmed');
        INSERT INTO leads (user_id, name, budget, status) VALUES ('u_old', 'Omar', 90000, 'warm');
        """
    )
    conn.commit()
    conn.close()

    init_db(legacy_db)

    with get_connection(legacy_db) as upgraded:
        fk_enabled = upgraded.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk_enabled == 1
        b_cols = {r["name"] for r in upgraded.execute("PRAGMA table_info(bookings)").fetchall()}
        l_cols = {r["name"] for r in upgraded.execute("PRAGMA table_info(leads)").fetchall()}
        assert {"session_id", "customer_name", "customer_phone"}.issubset(b_cols)
        assert {"original_budget", "original_currency", "preferred_models"}.issubset(l_cols)
        idx_names = {
            r["name"]
            for r in upgraded.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert {"idx_bookings_confirmed_slot", "idx_bookings_user_confirmed_slot", "idx_leads_user_unique"}.issubset(
            idx_names
        )
        assert upgraded.execute("SELECT COUNT(*) FROM bookings WHERE user_id='u_old'").fetchone()[0] == 1
        assert upgraded.execute("SELECT COUNT(*) FROM leads WHERE user_id='u_old'").fetchone()[0] == 1


def test_unexpected_server_error_never_exposes_stack_trace(temp_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ingest_inventory(db_path=temp_db)
    app = create_app(db_path=temp_db, auto_ingest=False)

    def _boom(_self: Any) -> list[dict[str, Any]]:
        raise RuntimeError("sensitive internal trace details /secret/path")

    monkeypatch.setattr(InventorySearchService, "get_inventory_makes", _boom)
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/inventory/makes")
        assert resp.status_code == 500
        body = resp.json()
        assert body == {"detail": "Internal server error", "error_code": "internal_error"}
        assert "Traceback" not in resp.text
        assert "sensitive internal" not in resp.text


def test_booking_vague_time_bypass_blocked_and_past_sunday_error(
    booking_env: dict[str, Any],
) -> None:
    db = booking_env["db"]
    lid = booking_env["listing_id"]
    sid = booking_env["session_id"]

    # Past Sunday (2026-11-08 vs fixed_now=2026-11-12) must report past date, not Sunday
    svc = BookingService(db, now_fn=booking_env["now_fn"], exports_dir=booking_env["exports_dir"])
    past_sun = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="2026-11-08",
        requested_time="10:00",
    )
    assert past_sun.success is False
    assert "past" in past_sun.message.lower()

    # Even if LLM guesses "14:00" for vague user phrases, _tool_propose_booking rejects in Python
    for vague_msg in (
        f"Book a viewing for listing #{lid} tomorrow afternoon",
        f"Book listing #{lid} in the morning",
        f"Can I view listing #{lid} later?",
        f"Book listing #{lid} sometime in the evening",
    ):
        llm_guess = _ScriptedLLMClient(
            [
                LLMResponse(
                    tool_calls=[
                        LLMToolCall(
                            name="propose_booking",
                            arguments={
                                "listing_id": lid,
                                "requested_date": "tomorrow",
                                "requested_time": "14:00",
                            },
                        )
                    ]
                ),
                LLMResponse(text="Please choose an exact clock time like 2:00 PM."),
            ]
        )
        orch = AgentOrchestrator(
            db_path=db,
            llm_client=llm_guess,
            now_fn=booking_env["now_fn"],
            exports_dir=booking_env["exports_dir"],
        )
        res = orch.handle_turn(session_id=sid, user_id="u_ali", message=vague_msg)
        assert "propose_booking" in res.tools_called
        assert svc.get_user_bookings("u_ali") == []

    # Explicit time inside "tomorrow afternoon at 2 PM" proceeds cleanly to BookingService
    llm_explicit = _ScriptedLLMClient(
        [
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="propose_booking",
                        arguments={
                            "listing_id": lid,
                            "requested_date": "tomorrow",
                            "requested_time": "2 PM",
                        },
                    )
                ]
            ),
            LLMResponse(text="Proposed viewing tomorrow at 2:00 PM. Please confirm explicitly."),
        ]
    )
    orch_ok = AgentOrchestrator(
        db_path=db,
        llm_client=llm_explicit,
        now_fn=booking_env["now_fn"],
        exports_dir=booking_env["exports_dir"],
    )
    orch_ok.handle_turn(
        session_id=sid,
        user_id="u_ali",
        message=f"Book a viewing for listing #{lid} tomorrow afternoon at 2 PM",
    )
    bookings = svc.get_user_bookings("u_ali")
    assert len(bookings) == 1
    assert bookings[0].start_time == "14:00"

    # 9:00 PM reaches deterministic booking validation and fails with operating-hours alternatives
    nine_pm = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="tomorrow",
        requested_time="9:00 PM",
    )
    assert nine_pm.success is False
    assert "08:00" in nine_pm.message and "19:00" in nine_pm.message
    assert len(nine_pm.alternatives) > 0


def test_booking_weekday_calendar_date_agreement_and_lifecycle(
    booking_env: dict[str, Any],
) -> None:
    db = booking_env["db"]
    lid = booking_env["listing_id"]
    svc = BookingService(db, now_fn=booking_env["now_fn"], exports_dir=booking_env["exports_dir"])
    ref_now = booking_env["now_fn"]()  # Thursday 2026-11-12 09:00 Asia/Dubai
    expected_sat = ref_now.date().replace(day=14)

    # Valid future weekday + calendar date variations must parse cleanly
    for valid_expr in (
        "Saturday, November 14, 2026",
        "2026-11-14 (Saturday)",
        "Sat 14 Nov 2026",
        "14/11/2026",
    ):
        parsed = parse_booking_date(valid_expr, ref_now)
        assert parsed == expected_sat, valid_expr

    # Weekday/calendar date mismatch MUST be rejected deterministically
    with pytest.raises(_SlotValidationError) as exc_info:
        parse_booking_date("Friday, November 14, 2026", ref_now)
    assert "saturday" in str(exc_info.value).lower() or "friday" in str(exc_info.value).lower()

    rejected_mismatch = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="Friday, November 14, 2026",
        requested_time="14:00",
    )
    assert rejected_mismatch.success is False
    assert rejected_mismatch.booking is None

    # Past date rejected
    rejected_past = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="2026-11-11",
        requested_time="14:00",
    )
    assert rejected_past.success is False
    assert "past" in rejected_past.message.lower()

    # Valid weekday + explicit date proposes successfully, then explicit confirmation reserves
    proposed = svc.propose_booking(
        user_id="u_ali",
        listing_id=lid,
        requested_date="Saturday, November 14, 2026",
        requested_time="14:00",
    )
    assert proposed.success is True
    assert proposed.booking is not None
    assert proposed.booking.status == BookingStatus.PROPOSED
    assert proposed.booking.booking_date == "2026-11-14"

    confirmed = svc.confirm_booking(
        booking_id=proposed.booking.booking_id,
        user_id="u_ali",
        confirm=True,
        confirmation_text="Yes, confirm Saturday November 14 at 2:00 PM",
    )
    assert confirmed.success is True
    assert confirmed.booking is not None
    assert confirmed.booking.status == BookingStatus.CONFIRMED
