"""Offline API and lifecycle tests using FastAPI TestClient."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app, ensure_database_ready


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """Provide a TestClient backed by an isolated temporary SQLite database."""
    db_path = tmp_path / "api_test.db"
    app = create_app(db_path=db_path)
    with TestClient(app) as tc:
        yield tc


def test_lifespan_auto_ingests_once_and_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "lifecycle.db"

    # First startup: empty DB -> automatically initializes schema and ingests 100 listings
    with TestClient(create_app(db_path=db_path)) as c1:
        r1 = c1.get("/health")
        assert r1.status_code == 200
        assert r1.json()["inventory_count"] == 100

    # Second startup (restart): must NOT re-ingest or duplicate rows
    count_after = ensure_database_ready(db_path=db_path)
    assert count_after == 100

    with TestClient(create_app(db_path=db_path)) as c2:
        r2 = c2.get("/health")
        assert r2.status_code == 200
        assert r2.json()["inventory_count"] == 100


def test_inventory_search_aliases_and_budget_exclusion(client: TestClient) -> None:
    res = client.get(
        "/inventory",
        params={"make": "merc", "spec_region": "gcc", "max_price": 120000},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total_matches"] >= 2
    assert data["excluded_unknown_price"] > 0
    for item in data["results"]:
        assert item["make"] == "mercedes-benz"
        assert item["cash_price"] <= 120000
        assert item["cash_price_status"] == "confirmed"


def test_inventory_honda_and_porsche_truthfulness(client: TestClient) -> None:
    honda = client.get("/inventory", params={"make": "honda"})
    assert honda.status_code == 200
    h_data = honda.json()
    assert h_data["total_matches"] == 1
    assert h_data["results"][0]["listing_id"] == 87
    assert h_data["results"][0]["model"] == "cr-v"

    porsche = client.get("/inventory", params={"make": "porsche"})
    assert porsche.status_code == 200
    p_data = porsche.json()
    assert p_data["total_matches"] == 0
    assert p_data["results"] == []


def test_inventory_audi_q8_duplicate_semantics(client: TestClient) -> None:
    res = client.get("/inventory", params={"make": "audi", "model": "q8"})
    assert res.status_code == 200
    data = res.json()
    assert data["total_matches"] == 5
    assert data["distinct_listing_groups"] == 1
    assert data["suspected_duplicate_groups"] == 1
    assert data["related_duplicate_listings"] == 4
    assert all(item["is_suspected_duplicate"] is True for item in data["results"])


def test_inventory_makes_endpoint(client: TestClient) -> None:
    res = client.get("/inventory/makes")
    assert res.status_code == 200
    makes = res.json()
    assert len(makes) == 29
    assert makes[0] == {"make": "mercedes-benz", "count": 17}
    assert any(m == {"make": "honda", "count": 1} for m in makes)


def test_inventory_detail_and_not_found(client: TestClient) -> None:
    ok = client.get("/inventory/87")
    assert ok.status_code == 200
    listing = ok.json()["listing"]
    assert listing["make"] == "honda"
    assert "052 996 5849" in listing["description_raw"]
    assert "052 996 5849" not in listing["description_display"]

    missing = client.get("/inventory/9999")
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "listing_not_found"

    invalid = client.get("/inventory/0")
    assert invalid.status_code == 422
    assert invalid.json()["error_code"] == "validation_error"


def test_inventory_invalid_filter_ranges_return_422(client: TestClient) -> None:
    bad_years = client.get("/inventory", params={"min_year": 2025, "max_year": 2020})
    assert bad_years.status_code == 422
    assert bad_years.json()["error_code"] == "validation_error"

    bad_price = client.get("/inventory", params={"max_price": -100})
    assert bad_price.status_code == 422
    assert bad_price.json()["error_code"] == "validation_error"


def test_users_crud_and_forget_me_cascade(client: TestClient) -> None:
    created = client.post("/users", json={"user_id": "u_alice", "name": "Alice"})
    assert created.status_code == 201
    assert created.json()["user_id"] == "u_alice"
    assert created.json()["name"] == "Alice"

    # Idempotent re-entry returns 200
    again = client.post("/users", json={"user_id": "u_alice"})
    assert again.status_code == 200
    assert again.json()["name"] == "Alice"

    # Blank user_id rejected
    bad = client.post("/users", json={"user_id": "   "})
    assert bad.status_code == 422

    # Create a session & favourite so we can verify cascade delete
    sess = client.post("/sessions", json={"user_id": "u_alice", "session_id": "s_alice_1"})
    assert sess.status_code == 201
    fav = client.post("/users/u_alice/favourites", json={"listing_id": 87})
    assert fav.status_code == 201

    fetched = client.get("/users/u_alice")
    assert fetched.status_code == 200
    assert fetched.json()["favourite_count"] == 1

    # Forget Me requires explicit confirmation
    unconfirmed = client.delete("/users/u_alice")
    assert unconfirmed.status_code == 400
    assert unconfirmed.json()["error_code"] == "confirmation_required"

    # Forget Me with confirmation deletes user + cascades session and favourites
    deleted = client.delete("/users/u_alice", params={"confirm": "true"})
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    assert client.get("/users/u_alice").status_code == 404
    assert client.get("/sessions/s_alice_1", params={"user_id": "u_alice"}).status_code == 404


def test_forget_me_api_purges_bookings_leads_csv_and_preserves_other_users(tmp_path: Path) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    db_path = tmp_path / "forget_api.db"
    exports_dir = tmp_path / "exports"
    admin_secret = "forget-test-admin-key"

    def fixed_now() -> datetime:
        return datetime(2026, 11, 12, 9, 0, tzinfo=ZoneInfo("Asia/Dubai"))

    app = create_app(
        db_path=db_path,
        now_fn=fixed_now,
        exports_dir=exports_dir,
        admin_api_key=admin_secret,
    )
    with TestClient(app) as tc:
        # Create u_alice and u_keep with full state (preferences, session, booking, lead)
        assert tc.post("/users", json={"user_id": "u_alice", "name": "Alice"}).status_code == 201
        assert tc.post("/users", json={"user_id": "u_keep", "name": "Keep"}).status_code == 201
        assert tc.post("/sessions", json={"user_id": "u_alice", "session_id": "s_alice"}).status_code == 201
        assert tc.put(
            "/users/u_alice/preferences",
            json={"preference_key": "budget_max", "preference_value": "150000"},
        ).status_code == 200
        assert tc.post("/users/u_alice/favourites", json={"listing_id": 87}).status_code == 201

        prop = tc.post(
            "/bookings/propose",
            json={
                "user_id": "u_alice",
                "listing_id": 87,
                "requested_date": "2026-11-14",
                "requested_time": "14:00",
                "session_id": "s_alice",
                "customer_name": "Alice",
                "customer_phone": "+971501112233",
            },
        )
        assert prop.status_code == 200
        bid = prop.json()["booking"]["booking_id"]
        conf = tc.post(
            f"/bookings/{bid}/confirm",
            json={"user_id": "u_alice", "confirm": True, "confirmation_text": "Yes, confirm"},
        )
        assert conf.status_code == 200

        assert tc.put(
            "/leads/u_keep",
            json={"name": "Keep", "phone": "+971504445566", "budget": 200000},
        ).status_code == 200

        csv_path = exports_dir / "leads.csv"
        assert csv_path.exists()
        assert "u_alice" in csv_path.read_text(encoding="utf-8")
        assert "+971501112233" in csv_path.read_text(encoding="utf-8")

        # Delete u_alice
        del_resp = tc.delete("/users/u_alice", params={"confirm": "true"})
        assert del_resp.status_code == 200
        assert del_resp.json()["deleted"] is True

        # Deleted user resources are inaccessible
        assert tc.get("/users/u_alice").status_code == 404
        assert tc.get("/users/u_alice/preferences").status_code == 404
        assert tc.get("/users/u_alice/searches").status_code == 404
        assert tc.get("/users/u_alice/favourites").status_code == 404
        assert tc.get("/users/u_alice/bookings").status_code == 404
        assert tc.get("/leads/u_alice").status_code == 404
        assert tc.get(f"/bookings/{bid}", params={"user_id": "u_alice"}).status_code == 404
        assert tc.get("/sessions/s_alice", params={"user_id": "u_alice"}).status_code == 404
        # Second delete returns 404
        assert tc.delete("/users/u_alice", params={"confirm": "true"}).status_code == 404

        # CSV export on disk and admin endpoints no longer contain u_alice
        csv_after = csv_path.read_text(encoding="utf-8")
        assert "u_alice" not in csv_after
        assert "+971501112233" not in csv_after
        assert "u_keep" in csv_after
        assert "+971504445566" in csv_after

        admin_list = tc.get("/leads", headers={"X-Admin-Key": admin_secret})
        assert admin_list.status_code == 200
        assert [row["user_id"] for row in admin_list.json()["leads"]] == ["u_keep"]

        admin_export = tc.get("/leads/export", headers={"X-Admin-Key": admin_secret})
        assert admin_export.status_code == 200
        assert "u_alice" not in admin_export.text
        assert "u_keep" in admin_export.text

        # Slot freed for another buyer after u_alice's confirmed booking was deleted
        keep_prop = tc.post(
            "/bookings/propose",
            json={
                "user_id": "u_keep",
                "listing_id": 87,
                "requested_date": "2026-11-14",
                "requested_time": "14:00",
                "customer_name": "Keep",
                "customer_phone": "+971504445566",
            },
        )
        assert keep_prop.status_code == 200
        assert keep_prop.json()["success"] is True


def test_favourites_endpoints_and_error_handling(client: TestClient) -> None:
    client.post("/users", json={"user_id": "u_bob"})

    add_ok = client.post("/users/u_bob/favourites", json={"listing_id": 87})
    assert add_ok.status_code == 201
    assert add_ok.json()["saved"] is True

    listed = client.get("/users/u_bob/favourites")
    assert listed.status_code == 200
    assert [item["listing_id"] for item in listed.json()] == [87]

    rm_ok = client.delete("/users/u_bob/favourites/87")
    assert rm_ok.status_code == 200
    assert client.get("/users/u_bob/favourites").json() == []

    # Missing listing or user errors
    assert client.post("/users/u_bob/favourites", json={"listing_id": 9999}).status_code == 404
    assert client.post("/users/no_such_user/favourites", json={"listing_id": 87}).status_code == 404
    assert client.delete("/users/u_bob/favourites/87").status_code == 404


def test_sessions_endpoints_and_focus_persistence(client: TestClient) -> None:
    client.post("/users", json={"user_id": "u_1"})
    client.post("/users", json={"user_id": "u_2"})

    s_res = client.post("/sessions", json={"user_id": "u_1", "session_id": "sess_1"})
    assert s_res.status_code == 201
    assert s_res.json()["session"]["current_vehicle"] is None

    # Unauthenticated GET /sessions/{id} without user_id must be rejected
    assert client.get("/sessions/sess_1").status_code == 422

    # Focus on listing 87 with mandatory owner user_id
    focus = client.post("/sessions/sess_1/focus", json={"user_id": "u_1", "listing_id": 87})
    assert focus.status_code == 200
    assert focus.json()["session"]["current_vehicle"] == 87

    # Retrieve persisted session state with mandatory owner user_id
    fetched = client.get("/sessions/sess_1", params={"user_id": "u_1"})
    assert fetched.status_code == 200
    assert fetched.json()["session"]["current_vehicle"] == 87

    # Cross-user GET and focus must be rejected with 403
    assert client.get("/sessions/sess_1", params={"user_id": "u_2"}).status_code == 403
    assert (
        client.post(
            "/sessions/sess_1/focus", json={"user_id": "u_2", "listing_id": 87}
        ).status_code
        == 403
    )

    # Different user cannot hijack existing session_id
    conflict = client.post("/sessions", json={"user_id": "u_2", "session_id": "sess_1"})
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "session_user_mismatch"

    # Unknown session / unknown listing
    assert client.get("/sessions/missing", params={"user_id": "u_1"}).status_code == 404
    assert (
        client.post(
            "/sessions/sess_1/focus", json={"user_id": "u_1", "listing_id": 9999}
        ).status_code
        == 404
    )


def test_colour_filter_suggested_prompts_and_user_snapshot_endpoint(client: TestClient) -> None:
    # 1. GET /inventory?colour=white strictly filters confirmed white cars
    w_res = client.get("/inventory", params={"colour": "white"})
    assert w_res.status_code == 200
    w_body = w_res.json()
    assert w_body["total_matches"] > 0
    for item in w_body["results"]:
        assert item["colour"] and "white" in item["colour"].lower()
    assert w_body["excluded_unknown_attributes"].get("colour", 0) > 0

    # 2. GET /inventory/suggested-prompts returns up to 4 grounded prompts
    p_res = client.get("/inventory/suggested-prompts")
    assert p_res.status_code == 200
    prompts = p_res.json()
    assert isinstance(prompts, list) and 1 <= len(prompts) <= 4

    # 3. GET /users/{user_id}/snapshot consolidates bootstrap state and enforces session ownership
    client.post("/users", json={"user_id": "u_snap_a", "name": "Amina"})
    client.post("/users", json={"user_id": "u_snap_b", "name": "Bilal"})
    s_b = client.post("/sessions", json={"user_id": "u_snap_b", "session_id": "s_b_priv"}).json()[
        "session"
    ]["session_id"]

    client.post("/users/u_snap_a/favourites", json={"listing_id": 87})
    client.put(
        "/users/u_snap_a/preferences",
        json={
            "preference_key": "preferred_colour",
            "preference_value": "White",
            "source_type": "ui",
        },
    )

    # Trying to pass Bilal's session_id must NOT expose Bilal's session — creates a clean session for Amina
    snap_res = client.get(
        "/users/u_snap_a/snapshot",
        params={"session_id": s_b, "search_limit": 10},
    )
    assert snap_res.status_code == 200
    snap = snap_res.json()
    assert snap["user"]["user_id"] == "u_snap_a"
    assert snap["session"]["session_id"] != s_b
    assert snap["session"]["user_id"] == "u_snap_a"
    assert len(snap["favourites"]) == 1 and snap["favourites"][0]["listing_id"] == 87
    assert any(p["preference_key"] == "preferred_colour" for p in snap["preferences"])
    assert snap["returning_summary"]["is_returning"] is True
    assert any(h["listing_id"] == 87 for h in snap["hydrated_listings"])

    # 4. Snapshot ?name=... must NOT overwrite an existing user's stored name
    snap_impostor = client.get(
        "/users/u_snap_a/snapshot",
        params={"name": "ImpostorName"},
    )
    assert snap_impostor.status_code == 200
    assert snap_impostor.json()["user"]["name"] == "Amina"

    # 5. Direct cross-user session/returning-summary access returns 403
    rs_forbidden = client.get(
        "/users/u_snap_a/returning-summary",
        params={"session_id": s_b},
    )
    assert rs_forbidden.status_code == 403
    assert rs_forbidden.json()["error_code"] == "session_forbidden"

    sess_forbidden = client.get(f"/sessions/{s_b}", params={"user_id": "u_snap_a"})
    assert sess_forbidden.status_code == 403
    assert sess_forbidden.json()["error_code"] == "session_forbidden"


def test_buyer_api_masks_phone_numbers_while_preserving_db_and_admin_access(
    tmp_path: Path,
) -> None:
    """Buyer APIs must consistently mask phones while keeping protected DB/admin & Forget Me working."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from backend.db import get_db

    db_path = tmp_path / "buyer_mask.db"
    exports_dir = tmp_path / "exports"
    admin_secret = "admin-mask-secret-123"
    dubai_tz = ZoneInfo("Asia/Dubai")
    app = create_app(
        db_path=db_path,
        now_fn=lambda: datetime(2026, 11, 12, 9, 0, tzinfo=dubai_tz),
        exports_dir=exports_dir,
        admin_api_key=admin_secret,
    )
    raw_phone = "+971501234567"
    local_phone = "0501234567"
    expected_masked = "+971******567"

    with TestClient(app) as tc:
        assert tc.post("/users", json={"user_id": "u_mask", "name": "Omar"}).status_code == 201
        assert tc.post("/sessions", json={"user_id": "u_mask", "session_id": "s_mask"}).status_code == 201

        # 1. Propose booking returns masked phone
        prop = tc.post(
            "/bookings/propose",
            json={
                "user_id": "u_mask",
                "listing_id": 87,
                "requested_date": "2026-11-14",
                "requested_time": "10:00",
                "session_id": "s_mask",
                "customer_name": "Omar",
                "customer_phone": local_phone,
            },
        )
        assert prop.status_code == 200
        assert prop.json()["booking"]["customer_phone"] == expected_masked
        assert "501234567" not in prop.text
        bid = prop.json()["booking"]["booking_id"]

        # 2. Failed/vague confirm AND successful confirm both return masked phone
        vague_conf = tc.post(
            f"/bookings/{bid}/confirm",
            json={"user_id": "u_mask", "confirm": True, "confirmation_text": "okay"},
        )
        assert vague_conf.status_code == 200
        assert vague_conf.json()["success"] is False
        assert vague_conf.json()["booking"]["customer_phone"] == expected_masked
        assert "501234567" not in vague_conf.text

        ok_conf = tc.post(
            f"/bookings/{bid}/confirm",
            json={"user_id": "u_mask", "confirm": True, "confirmation_text": "Yes, confirm"},
        )
        assert ok_conf.status_code == 200
        assert ok_conf.json()["success"] is True
        assert ok_conf.json()["booking"]["customer_phone"] == expected_masked
        assert "501234567" not in ok_conf.text

        # 3. GET booking, list user bookings, and snapshot all mask phone numbers consistently
        bk_get = tc.get(f"/bookings/{bid}", params={"user_id": "u_mask"})
        assert bk_get.status_code == 200
        assert bk_get.json()["customer_phone"] == expected_masked
        assert "501234567" not in bk_get.text

        bk_list = tc.get("/users/u_mask/bookings")
        assert bk_list.status_code == 200
        assert bk_list.json()[0]["customer_phone"] == expected_masked
        assert "501234567" not in bk_list.text

        snap = tc.get("/users/u_mask/snapshot", params={"session_id": "s_mask"})
        assert snap.status_code == 200
        assert snap.json()["bookings"][0]["customer_phone"] == expected_masked
        assert "501234567" not in snap.text

        # 4. Buyer PUT & GET /leads/{user_id} both mask phone numbers
        lead_put = tc.put(
            "/leads/u_mask",
            json={"name": "Omar", "phone": local_phone, "budget": 175000},
        )
        assert lead_put.status_code == 200
        assert lead_put.json()["lead"]["phone"] == expected_masked
        assert "501234567" not in lead_put.text

        lead_get = tc.get("/leads/u_mask")
        assert lead_get.status_code == 200
        assert lead_get.json()["lead"]["phone"] == expected_masked
        assert "501234567" not in lead_get.text

        # 5. Protected SQLite database retains actual normalized phone
        with get_db(db_path) as conn:
            bk_row = conn.execute(
                "SELECT customer_phone FROM bookings WHERE booking_id = ?", (bid,)
            ).fetchone()
            ld_row = conn.execute(
                "SELECT phone FROM leads WHERE user_id = 'u_mask'"
            ).fetchone()
        assert bk_row["customer_phone"] == raw_phone
        assert ld_row["phone"] == raw_phone

        # 6. Authorized admin routes retain unmasked lead phone and CSV export; unauthorized still blocked
        assert tc.get("/leads").status_code == 401
        assert tc.get("/leads/export").status_code == 401

        admin_list = tc.get("/leads", headers={"X-Admin-Key": admin_secret})
        assert admin_list.status_code == 200
        assert admin_list.json()["leads"][0]["phone"] == raw_phone

        admin_csv = tc.get("/leads/export", headers={"X-Admin-Key": admin_secret})
        assert admin_csv.status_code == 200
        assert raw_phone in admin_csv.text

        # 7. Forget Me still deletes DB rows and purges raw phone from leads.csv
        del_res = tc.delete("/users/u_mask", params={"confirm": "true"})
        assert del_res.status_code == 200
        assert raw_phone not in (exports_dir / "leads.csv").read_text(encoding="utf-8")
        assert tc.get("/leads/u_mask").status_code == 404
        assert tc.get(f"/bookings/{bid}", params={"user_id": "u_mask"}).status_code == 404

