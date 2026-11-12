"""Offline tests for deterministic Python vehicle reference resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.db import get_db
from backend.inventory.ingestion import ingest_inventory
from backend.main import create_app
from backend.models.schemas import SearchFilters
from backend.services.references import ReferenceResolutionService, ResolutionStatus
from backend.services.sessions import SessionService
from backend.services.users import UserService


@pytest.fixture()
def ctx(temp_db: Path) -> tuple[Path, str, str]:
    """Seed inventory, create user 'u_ref' and session 's_ref'."""
    ingest_inventory(db_path=temp_db)
    UserService(temp_db).create_or_get_user("u_ref", "Ref User")
    SessionService(temp_db).create_session("u_ref", "s_ref")
    return temp_db, "u_ref", "s_ref"


def test_resolve_first_second_and_last_in_exact_display_order(
    ctx: tuple[Path, str, str],
) -> None:
    db_path, uid, sid = ctx
    sessions = SessionService(db_path)
    resolver = ReferenceResolutionService(db_path)

    # Deliberately non-ID-sorted order: [87, 1, 51]
    sessions.record_search_results(sid, uid, [87, 1, 51])

    r1 = resolver.resolve(session_id=sid, user_id=uid, reference_text="What's the mileage on the first one?")
    assert r1.status == ResolutionStatus.RESOLVED
    assert r1.listing_id == 87
    assert r1.resolution_method == "ordinal"

    r2 = resolver.resolve(session_id=sid, user_id=uid, reference_text="tell me about the second car")
    assert r2.status == ResolutionStatus.RESOLVED
    assert r2.listing_id == 1

    r_last = resolver.resolve(session_id=sid, user_id=uid, reference_text="the last one")
    assert r_last.status == ResolutionStatus.RESOLVED
    assert r_last.listing_id == 51

    r_param = resolver.resolve(session_id=sid, user_id=uid, ordinal=2)
    assert r_param.status == ResolutionStatus.RESOLVED
    assert r_param.listing_id == 1

    r_oob = resolver.resolve(session_id=sid, user_id=uid, reference_text="the 5th car")
    assert r_oob.status == ResolutionStatus.NOT_FOUND
    assert "3" in (r_oob.clarification_prompt or "")


def test_resolve_focused_vehicle_deictic_references(ctx: tuple[Path, str, str]) -> None:
    db_path, uid, sid = ctx
    sessions = SessionService(db_path)
    resolver = ReferenceResolutionService(db_path)

    sessions.record_search_results(sid, uid, [87, 1])

    # Before focusing, "that car" is ambiguous when 2 results exist
    ambig = resolver.resolve(session_id=sid, user_id=uid, reference_text="that car")
    assert ambig.status == ResolutionStatus.AMBIGUOUS

    # Resolving ordinal 1 updates focus -> follow-up "this one" / "it" resolves to 87
    resolver.resolve(session_id=sid, user_id=uid, ordinal=1, update_focus=True)
    for phrase in ("that car", "this one", "it", "its", "its details", "the car", "current car", ""):
        res = resolver.resolve(session_id=sid, user_id=uid, reference_text=phrase)
        assert res.status == ResolutionStatus.RESOLVED
        assert res.listing_id == 87
        assert res.resolution_method == "focused_vehicle"


def test_resolve_explicit_listing_id_valid_and_missing(ctx: tuple[Path, str, str]) -> None:
    db_path, uid, sid = ctx
    resolver = ReferenceResolutionService(db_path)

    ok = resolver.resolve(session_id=sid, user_id=uid, reference_text="Tell me about listing #87")
    assert ok.status == ResolutionStatus.RESOLVED
    assert ok.listing_id == 87
    assert ok.resolution_method == "explicit_id"

    missing = resolver.resolve(session_id=sid, user_id=uid, explicit_listing_id=9999)
    assert missing.status == ResolutionStatus.NOT_FOUND


def test_resolve_named_make_model_unambiguous_vs_ambiguous(
    ctx: tuple[Path, str, str],
) -> None:
    db_path, uid, sid = ctx
    sessions = SessionService(db_path)
    resolver = ReferenceResolutionService(db_path)

    # Mix: ID 87 (Honda CR-V), ID 7 (Audi Q8), ID 6 (Mercedes C200)
    sessions.record_search_results(sid, uid, [87, 7, 6])

    r_audi = resolver.resolve(session_id=sid, user_id=uid, reference_text="the Audi")
    assert r_audi.status == ResolutionStatus.RESOLVED
    assert r_audi.listing_id == 7

    r_merc = resolver.resolve(session_id=sid, user_id=uid, reference_text="the Merc")
    assert r_merc.status == ResolutionStatus.RESOLVED
    assert r_merc.listing_id == 6

    r_crv = resolver.resolve(session_id=sid, user_id=uid, reference_text="the CRV")
    assert r_crv.status == ResolutionStatus.RESOLVED
    assert r_crv.listing_id == 87

    # Now put two Audis in results (ID 7 Q8 + ID 24 RS7) -> "the Audi" MUST be ambiguous!
    sessions.record_search_results(sid, uid, [7, 24, 87])
    r_ambig = resolver.resolve(session_id=sid, user_id=uid, reference_text="the Audi")
    assert r_ambig.status == ResolutionStatus.AMBIGUOUS
    assert set(r_ambig.candidate_ids) == {7, 24}
    assert r_ambig.clarification_prompt is not None


def test_resolve_named_reference_across_suspected_duplicates_stays_ambiguous(
    ctx: tuple[Path, str, str],
) -> None:
    db_path, uid, sid = ctx
    sessions = SessionService(db_path)
    resolver = ReferenceResolutionService(db_path)

    # Multiple Audi Q8 listings from suspected duplicate group 1 -> must clarify, never collapse
    sessions.search_in_session(sid, uid, SearchFilters(make="audi", model="q8"))
    res = resolver.resolve(session_id=sid, user_id=uid, reference_text="the Audi")
    assert res.status == ResolutionStatus.AMBIGUOUS
    assert len(res.candidate_ids) == 5


def test_resolve_saved_vehicle_reference(ctx: tuple[Path, str, str]) -> None:
    db_path, uid, sid = ctx
    users = UserService(db_path)
    resolver = ReferenceResolutionService(db_path)

    # 0 saved -> NO_CONTEXT
    none_res = resolver.resolve(session_id=sid, user_id=uid, reference_text="the one I saved")
    assert none_res.status == ResolutionStatus.NO_CONTEXT

    # 1 saved -> RESOLVED
    users.add_favourite(uid, 87)
    one_res = resolver.resolve(session_id=sid, user_id=uid, reference_text="the one I saved")
    assert one_res.status == ResolutionStatus.RESOLVED
    assert one_res.listing_id == 87

    # 2 saved -> AMBIGUOUS, unless narrowed by make
    users.add_favourite(uid, 6)
    two_res = resolver.resolve(session_id=sid, user_id=uid, reference_text="the one I saved")
    assert two_res.status == ResolutionStatus.AMBIGUOUS
    assert set(two_res.candidate_ids) == {87, 6}

    narrowed = resolver.resolve(session_id=sid, user_id=uid, reference_text="the Honda I saved")
    assert narrowed.status == ResolutionStatus.RESOLVED
    assert narrowed.listing_id == 87


def test_resolve_empty_result_set_never_invents(ctx: tuple[Path, str, str]) -> None:
    db_path, uid, sid = ctx
    resolver = ReferenceResolutionService(db_path)

    for text in ("the first one", "that car", "the Honda"):
        res = resolver.resolve(session_id=sid, user_id=uid, reference_text=text)
        assert res.status == ResolutionStatus.NO_CONTEXT
        assert res.listing_id is None


def test_resolve_after_new_search_uses_new_context(ctx: tuple[Path, str, str]) -> None:
    db_path, uid, sid = ctx
    sessions = SessionService(db_path)
    resolver = ReferenceResolutionService(db_path)

    # Search 1: Honda -> focus 87
    sessions.search_in_session(sid, uid, SearchFilters(make="honda"))
    first = resolver.resolve(session_id=sid, user_id=uid, reference_text="first one")
    assert first.listing_id == 87

    # Search 2: Ferrari -> replaces context and clears stale Honda focus
    sessions.search_in_session(sid, uid, SearchFilters(make="ferrari"))
    new_first = resolver.resolve(session_id=sid, user_id=uid, reference_text="first one")
    assert new_first.status == ResolutionStatus.RESOLVED
    assert new_first.listing_id in {1, 82}

    # Named reference to "the Honda" must NOT resolve from old context
    old_name = resolver.resolve(session_id=sid, user_id=uid, reference_text="the Honda")
    assert old_name.status == ResolutionStatus.NOT_FOUND


def test_resolve_handles_stale_ids_in_session_gracefully(ctx: tuple[Path, str, str]) -> None:
    db_path, uid, sid = ctx
    resolver = ReferenceResolutionService(db_path)

    # Simulate stale listing IDs stored directly in session row
    with get_db(db_path) as conn:
        conn.execute(
            "UPDATE sessions SET last_result_ids = '[99999]' WHERE session_id = ?",
            (sid,),
        )

    res = resolver.resolve(session_id=sid, user_id=uid, reference_text="first one")
    assert res.status == ResolutionStatus.NO_CONTEXT
    assert res.listing_id is None


def test_resolve_endpoint_via_api_and_ownership(ctx: tuple[Path, str, str]) -> None:
    db_path, uid, sid = ctx
    UserService(db_path).create_or_get_user("u_other", "Other")
    SessionService(db_path).record_search_results(sid, uid, [87, 1])

    with TestClient(create_app(db_path=db_path)) as client:
        ok = client.post(
            f"/sessions/{sid}/resolve",
            json={"user_id": uid, "reference_text": "the second one"},
        )
        assert ok.status_code == 200
        assert ok.json()["status"] == "resolved"
        assert ok.json()["listing_id"] == 1

        # Cross-user resolution attempt rejected
        forbidden = client.post(
            f"/sessions/{sid}/resolve",
            json={"user_id": "u_other", "reference_text": "the first one"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["error_code"] == "session_forbidden"


def test_resolve_multi_ordinal_previous_vehicle_and_full_sentence_deictic(
    ctx: tuple[Path, str, str],
) -> None:
    db_path, uid, sid = ctx
    sessions = SessionService(db_path)
    resolver = ReferenceResolutionService(db_path)

    # Seed ordered search results [87, 1, 16]
    sessions.record_search_results(sid, uid, [87, 1, 16])

    # Multi-ordinal comparison resolves both #1 (pos 2) and #16 (pos 3) deterministically
    multi = resolver.resolve(session_id=sid, user_id=uid, reference_text="Compare the second and third")
    assert multi.status == ResolutionStatus.RESOLVED
    assert multi.resolution_method == "multi_ordinal"
    assert multi.candidate_ids == [1, 16]
    assert [item.listing_id for item in multi.resolved_listings] == [1, 16]

    # Simulate focusing #87 then #1 cleanly via assistant messages with cards
    sessions.add_message(sid, uid, "assistant", "Checked listing #87.", vehicle_cards=[87])
    sessions.focus_vehicle(sid, 87, user_id=uid)
    sessions.add_message(sid, uid, "assistant", "Checked listing #1.", vehicle_cards=[1])
    sessions.focus_vehicle(sid, 1, user_id=uid)

    # Full-sentence deictic resolves focused vehicle (#1)
    deictic = resolver.resolve(
        session_id=sid, user_id=uid, reference_text="What's the mileage on this car?"
    )
    assert deictic.status == ResolutionStatus.RESOLVED
    assert deictic.listing_id == 1

    # "Compare that with the previous vehicle" returns BOTH focused (#1) and earlier (#87)
    cmp_prev = resolver.resolve(
        session_id=sid,
        user_id=uid,
        reference_text="Compare that with the previous vehicle",
    )
    assert cmp_prev.status == ResolutionStatus.RESOLVED
    assert cmp_prev.resolution_method == "previous_vehicle"
    assert set(cmp_prev.candidate_ids) == {1, 87}
    assert {item.listing_id for item in cmp_prev.resolved_listings} == {1, 87}
