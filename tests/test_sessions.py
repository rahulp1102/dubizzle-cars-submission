"""Offline tests for persistent SQLite session state, restart survival, and ownership isolation."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.db import get_db
from backend.inventory.ingestion import ingest_inventory
from backend.main import create_app
from backend.models.schemas import SearchFilters
from backend.services.exceptions import ForbiddenError, NotFoundError
from backend.services.sessions import SessionService
from backend.services.users import UserService


@pytest.fixture()
def seeded_db(temp_db: Path) -> Path:
    ingest_inventory(db_path=temp_db)
    return temp_db


def test_session_persists_across_restarts(seeded_db: Path) -> None:
    # Phase A: Create user + session via first app instance
    with TestClient(create_app(db_path=seeded_db)) as c1:
        c1.post("/users", json={"user_id": "u_tariq", "name": "Tariq"})
        c1.post("/sessions", json={"user_id": "u_tariq", "session_id": "s_persist"})

        search_res = c1.post(
            "/sessions/s_persist/search",
            json={
                "user_id": "u_tariq",
                "filters": {"make": "merc", "spec_region": "gcc"},
                "limit": 5,
            },
        )
        assert search_res.status_code == 200
        expected_ids = [item["listing_id"] for item in search_res.json()["results"]]
        assert len(expected_ids) == 5

        focus_res = c1.post(
            "/sessions/s_persist/focus",
            json={"user_id": "u_tariq", "listing_id": expected_ids[1]},
        )
        assert focus_res.status_code == 200

    # Phase B: Simulate service/process restarts directly via fresh SessionService
    svc = SessionService(seeded_db)
    svc.update_booking_draft(
        "s_persist",
        "u_tariq",
        {"listing_id": expected_ids[1], "date": "2026-11-14", "time": "16:00"},
    )
    svc.add_message("s_persist", "u_tariq", "user", "Show me GCC Mercedes cars.")
    svc.add_message(
        "s_persist",
        "u_tariq",
        "assistant",
        "Here are 5 GCC Mercedes-Benz listings.",
        vehicle_cards=expected_ids,
    )
    svc.update_context_summary("s_persist", "u_tariq", "Looking at GCC Mercedes-Benz listings.")

    # Phase C: New TestClient instance (full API restart) reads exact persisted state
    with TestClient(create_app(db_path=seeded_db)) as c2:
        res = c2.get("/sessions/s_persist", params={"user_id": "u_tariq"})
        assert res.status_code == 200
        sess = res.json()["session"]
        assert sess["last_result_ids"] == expected_ids
        assert sess["current_vehicle"] == expected_ids[1]
        assert sess["active_filters"]["make"] == "mercedes-benz"
        assert sess["active_filters"]["spec_region"] == "GCC"
        assert sess["booking_draft"]["time"] == "16:00"
        assert sess["context_summary"] == "Looking at GCC Mercedes-Benz listings."
        assert len(sess["recent_messages"]) == 2
        assert sess["recent_messages"][0]["role"] == "user"
        assert sess["recent_messages"][1]["vehicle_cards"] == expected_ids


def test_new_session_starts_clean_without_erasing_long_term_data(seeded_db: Path) -> None:
    users = UserService(seeded_db)
    sessions = SessionService(seeded_db)

    users.create_or_get_user("u_nora", "Nora")
    users.add_favourite("u_nora", 87)

    s1, _ = sessions.create_session("u_nora", "s_old")
    sessions.search_in_session(s1.session_id, "u_nora", SearchFilters(make="honda"))
    sessions.focus_vehicle(s1.session_id, 87, user_id="u_nora")
    sessions.add_message(s1.session_id, "u_nora", "user", "Do you have Hondas?")

    # Open a completely new session for the same user
    s2, created = sessions.create_session("u_nora", "s_new")
    assert created is True
    assert s2.last_result_ids == []
    assert s2.current_vehicle is None
    assert s2.active_filters is None
    assert s2.booking_draft is None
    assert s2.recent_messages == []

    # Long-term favourites remain untouched
    favs = users.list_favourites("u_nora")
    assert [f.listing_id for f in favs] == [87]


def test_new_search_replaces_previous_reference_context(seeded_db: Path) -> None:
    sessions = SessionService(seeded_db)
    UserService(seeded_db).create_or_get_user("u_sara", "Sara")
    sessions.create_session("u_sara", "s_search")

    sessions.search_in_session("s_search", "u_sara", SearchFilters(make="honda"))
    sessions.focus_vehicle("s_search", 87, user_id="u_sara")
    s_mid = sessions.get_session("s_search", user_id="u_sara")
    assert s_mid.last_result_ids == [87]
    assert s_mid.current_vehicle == 87

    # Second search replaces result IDs, filters, and clears stale focus
    sessions.search_in_session("s_search", "u_sara", SearchFilters(make="ferrari"))
    s_after = sessions.get_session("s_search", user_id="u_sara")
    assert set(s_after.last_result_ids) == {1, 82}
    assert s_after.current_vehicle is None
    assert s_after.active_filters == {"make": "ferrari"}


def test_session_ownership_isolation_api_and_service(seeded_db: Path) -> None:
    with TestClient(create_app(db_path=seeded_db)) as client:
        client.post("/users", json={"user_id": "u_alice", "name": "Alice"})
        client.post("/users", json={"user_id": "u_bob", "name": "Bob"})
        client.post("/sessions", json={"user_id": "u_alice", "session_id": "s_alice"})

        # Unauthenticated GET without user_id must be rejected (422)
        r_unauth = client.get("/sessions/s_alice")
        assert r_unauth.status_code == 422

        # Bob cannot read, focus, search, or resolve inside Alice's session
        r_get = client.get("/sessions/s_alice", params={"user_id": "u_bob"})
        assert r_get.status_code == 403
        assert r_get.json()["error_code"] == "session_forbidden"

        r_focus = client.post(
            "/sessions/s_alice/focus", json={"user_id": "u_bob", "listing_id": 87}
        )
        assert r_focus.status_code == 403

        r_search = client.post(
            "/sessions/s_alice/search",
            json={"user_id": "u_bob", "filters": {"make": "honda"}},
        )
        assert r_search.status_code == 403

        r_resolve = client.post(
            "/sessions/s_alice/resolve",
            json={"user_id": "u_bob", "reference_text": "first one"},
        )
        assert r_resolve.status_code == 403

    # Service-level isolation check
    svc = SessionService(seeded_db)
    with pytest.raises(ForbiddenError):
        svc.require_user_session("s_alice", "u_bob")
    with pytest.raises(NotFoundError):
        svc.require_user_session("s_nonexistent", "u_alice")


def test_booking_draft_does_not_create_booking_row(seeded_db: Path) -> None:
    UserService(seeded_db).create_or_get_user("u_draft")
    sessions = SessionService(seeded_db)
    sessions.create_session("u_draft", "s_draft")
    sessions.update_booking_draft(
        "s_draft",
        "u_draft",
        {"listing_id": 87, "date": "2026-11-14", "time": "15:00", "status": "draft"},
    )

    with get_db(seeded_db) as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    assert cnt == 0
