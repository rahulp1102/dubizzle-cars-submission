"""Streamlit presentation state coordination and FastAPI snapshot synchronization."""

from __future__ import annotations

import uuid
from typing import Any, Optional

import streamlit as st

from frontend.api_client import DubizzleAPIClient, DubizzleAPIError
from frontend.components.primitives import format_make_name

_display_make = format_make_name


def get_api_client() -> DubizzleAPIClient:
    """Return active API client (allows injection during automated AppTest runs)."""
    injected = st.session_state.get("_injected_api_client")
    if isinstance(injected, DubizzleAPIClient):
        return injected
    return DubizzleAPIClient()


def init_ui_state() -> None:
    """Initialise presentation-only Streamlit session keys from query params if present."""
    qp = st.query_params

    st.session_state["theme"] = "light"

    if "user_id" not in st.session_state:
        qp_uid = str(qp.get("user_id", "")).strip()
        if not qp_uid:
            qp_uid = f"buyer_{uuid.uuid4().hex[:16]}"
            st.query_params["user_id"] = qp_uid
        st.session_state["user_id"] = qp_uid

    if "session_id" not in st.session_state:
        qp_sid = str(qp.get("session_id", "")).strip()
        st.session_state["session_id"] = qp_sid

    defaults: dict[str, Any] = {
        "pending_prompt": None,
        "pending_chat_turn": None,
        "chat_error": None,
        "chat_in_flight": False,
        "active_dialog": None,
        "booking_panel_open": False,
        "booking_listing_id": None,
        "booking_result": None,
        "booking_date_override": None,
        "booking_time_override": None,
        "dismissed_booking_ids": set(),
        "inspect_listing_id": None,
        "compare_listing_ids": [],
        "show_filter_search": False,
        "manual_search_result": None,
        "api_error": None,
        "profile_form_error": None,
        "degraded_mode": False,
        "health_info": None,
        "profile_dirty": True,
        "backend_snapshot": None,
        "listing_summary_cache": {},
        "listing_detail_cache": {},
        "inventory_makes_cache": None,
        "grounded_prompts": None,
        "show_all_favs": False,
        "show_all_bookings": False,
        "show_all_searches": False,
        "lead_status_cache": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def mark_dirty() -> None:
    """Mark backend snapshot stale so next render pulls latest FastAPI state."""
    st.session_state["profile_dirty"] = True


def sync_query_params() -> None:
    """Keep URL query params aligned with active user and session for page reload recovery."""
    uid = st.session_state.get("user_id")
    sid = st.session_state.get("session_id")
    if uid:
        st.query_params["user_id"] = uid
    if sid:
        st.query_params["session_id"] = sid
    elif "session_id" in st.query_params:
        del st.query_params["session_id"]
    if "theme" in st.query_params:
        del st.query_params["theme"]


def open_dialog(
    kind: str,
    *,
    listing_id: Optional[int] = None,
    booking_result: Optional[dict[str, Any]] = None,
) -> None:
    """Open exactly one modal dialog cleanly while clearing competing dialog flags."""
    st.session_state["active_dialog"] = kind
    st.session_state["booking_panel_open"] = kind == "booking"
    if kind == "details":
        st.session_state["inspect_listing_id"] = listing_id
    else:
        st.session_state["inspect_listing_id"] = None

    if kind == "booking":
        if listing_id is not None:
            st.session_state["booking_listing_id"] = int(listing_id)
        st.session_state["booking_result"] = booking_result


def close_dialog() -> None:
    """Reset active dialog state (used both by Close buttons and dialog on_dismiss)."""
    st.session_state["active_dialog"] = None
    st.session_state["booking_panel_open"] = False
    st.session_state["inspect_listing_id"] = None


def toggle_compare_listing(listing_id: int, max_items: int = 4) -> None:
    """Add or remove a listing ID from the buyer's comparison shortlist (max 4)."""
    lid = int(listing_id)
    current = list(st.session_state.get("compare_listing_ids") or [])
    if lid in current:
        current.remove(lid)
    else:
        if len(current) >= max_items:
            current.pop(0)
        current.append(lid)
    st.session_state["compare_listing_ids"] = current


def set_compare_listings(listing_ids: list[int], max_items: int = 4) -> None:
    """Pre-populate the comparison tray with up to max_items unique listing IDs."""
    unique: list[int] = []
    for raw in listing_ids:
        lid = int(raw)
        if lid > 0 and lid not in unique:
            unique.append(lid)
        if len(unique) >= max_items:
            break
    st.session_state["compare_listing_ids"] = unique


def hydrate_listing_summaries(
    client: DubizzleAPIClient,
    listing_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Fetch any uncached ListingSummary objects from FastAPI in a single batch call."""
    cache: dict[int, dict[str, Any]] = st.session_state.setdefault("listing_summary_cache", {})
    missing = [int(lid) for lid in listing_ids if int(lid) > 0 and int(lid) not in cache]
    if missing:
        summaries = client.get_listing_summaries(missing)
        for item in summaries:
            cache[int(item["listing_id"])] = item
    return cache


def seed_summary_cache(items: list[dict[str, Any]]) -> None:
    cache: dict[int, dict[str, Any]] = st.session_state.setdefault("listing_summary_cache", {})
    for item in items:
        if "listing_id" in item:
            cache[int(item["listing_id"])] = item


def get_cached_listing_detail(client: DubizzleAPIClient, listing_id: int) -> dict[str, Any]:
    """Return full Listing detail from cache or FastAPI without re-fetching across unrelated reruns."""
    lid = int(listing_id)
    cache: dict[int, dict[str, Any]] = st.session_state.setdefault("listing_detail_cache", {})
    if lid not in cache:
        resp = client.get_listing_detail(lid)
        cache[lid] = resp["listing"]
    return cache[lid]


def get_cached_inventory_makes(client: DubizzleAPIClient) -> list[dict[str, Any]]:
    """Fetch and cache inventory make counts cleanly."""
    cached = st.session_state.get("inventory_makes_cache")
    if isinstance(cached, list) and cached:
        return cached
    try:
        makes = client.get_inventory_makes()
        st.session_state["inventory_makes_cache"] = makes
        return makes
    except DubizzleAPIError:
        return []


def build_grounded_suggested_prompts(client: DubizzleAPIClient) -> list[str]:
    """Generate suggested prompts exclusively from live FastAPI inventory availability."""
    existing = st.session_state.get("grounded_prompts")
    if isinstance(existing, list) and existing:
        return existing

    prompts: list[str] = []
    try:
        try:
            prompts = client.get_suggested_prompts()
        except DubizzleAPIError as exc:
            if exc.status_code != 404:
                raise
            makes = get_cached_inventory_makes(client)
            if not makes:
                return []

            gcc_suvs = client.search_inventory(spec_region="GCC", body_type="SUV", limit=1)
            if gcc_suvs.get("total_matches", 0) > 0:
                prompts.append("Find GCC-spec SUVs.")

            top_make_raw = makes[0]["make"]
            budget_check = client.search_inventory(make=top_make_raw, max_price=150000, limit=1)
            if budget_check.get("total_matches", 0) > 0:
                prompts.append(f"Show me {_display_make(top_make_raw)} under AED 150,000.")
            else:
                any_budget = client.search_inventory(max_price=150000, limit=1)
                if any_budget.get("total_matches", 0) > 0:
                    prompts.append("Show me cars under AED 150,000.")

            low_km = client.search_inventory(max_mileage=50000, limit=1)
            if low_km.get("total_matches", 0) > 0:
                prompts.append("Find cars with low mileage.")

            make_names = [m["make"] for m in makes if m.get("count", 0) > 0]
            if "toyota" in make_names:
                prompts.append("Show me available Toyota vehicles.")
            elif len(make_names) > 1:
                prompts.append(f"Show me available {_display_make(make_names[1])} vehicles.")
            elif make_names:
                prompts.append(f"Show me available {_display_make(make_names[0])} vehicles.")
    except DubizzleAPIError:
        return []

    st.session_state["grounded_prompts"] = prompts[:4]
    return st.session_state["grounded_prompts"]


def refresh_backend_snapshot(
    client: DubizzleAPIClient,
    *,
    force: bool = False,
) -> Optional[dict[str, Any]]:
    """Synchronize persisted user, session, preferences, favourites, searches, and bookings from FastAPI."""
    if (
        not force
        and not st.session_state.get("profile_dirty", True)
        and st.session_state.get("backend_snapshot") is not None
    ):
        return st.session_state["backend_snapshot"]

    uid: str = st.session_state["user_id"]
    sid: str = st.session_state.get("session_id") or ""
    requested_name: Optional[str] = st.session_state.pop("_pending_profile_name", None)

    try:
        try:
            snap = client.get_user_snapshot(
                uid,
                session_id=sid or None,
                name=requested_name,
                search_limit=15,
            )
            health = snap.get("health") or {"status": "ok", "gemini_configured": False}
            st.session_state["health_info"] = health
            st.session_state["degraded_mode"] = not bool(health.get("gemini_configured", False))

            session_obj = snap["session"]
            st.session_state["session_id"] = session_obj["session_id"]

            hydrated = snap.get("hydrated_listings") or []
            if hydrated:
                seed_summary_cache(hydrated)
            seed_summary_cache(snap.get("favourites") or [])

            snapshot = {
                "user": snap["user"],
                "session": session_obj,
                "preferences": snap.get("preferences", []),
                "preference_history": snap.get("preference_history", []),
                "favourites": snap.get("favourites", []),
                "searches": snap.get("searches", []),
                "bookings": snap.get("bookings", []),
                "returning_summary": snap.get("returning_summary"),
            }
        except DubizzleAPIError as exc:
            if exc.status_code != 404:
                raise
            health = client.health()
            st.session_state["health_info"] = health
            st.session_state["degraded_mode"] = not bool(health.get("gemini_configured", False))

            user_profile = client.create_or_get_user(uid, name=requested_name)

            session_obj = None
            if sid:
                try:
                    session_obj = client.get_session(sid, user_id=uid)["session"]
                except DubizzleAPIError as sess_exc:
                    if sess_exc.status_code not in {403, 404}:
                        raise
                    sid = ""

            if not sid or session_obj is None:
                session_obj = client.create_session(uid)["session"]
                sid = session_obj["session_id"]
                st.session_state["session_id"] = sid

            prefs_resp = client.get_preferences(uid)
            favourites = client.list_favourites(uid)
            searches_resp = client.get_search_history(uid, limit=15)
            bookings = client.list_user_bookings(uid)
            returning_resp = client.get_returning_summary(uid, session_id=sid)

            seed_summary_cache(favourites)

            ids_to_hydrate: list[int] = []
            for msg in session_obj.get("recent_messages", []):
                ids_to_hydrate.extend(msg.get("vehicle_cards") or [])
            if session_obj.get("current_vehicle"):
                ids_to_hydrate.append(int(session_obj["current_vehicle"]))
            for bk in bookings:
                if bk.get("listing_id"):
                    ids_to_hydrate.append(int(bk["listing_id"]))
            if ids_to_hydrate:
                hydrate_listing_summaries(client, ids_to_hydrate)

            snapshot = {
                "user": user_profile,
                "session": session_obj,
                "preferences": prefs_resp.get("preferences", []),
                "preference_history": prefs_resp.get("history", []),
                "favourites": favourites,
                "searches": searches_resp.get("searches", []),
                "bookings": bookings,
                "returning_summary": returning_resp.get("summary"),
            }

        # Refresh optional lead summary cleanly without failing snapshot if absent (404)
        try:
            lead_detail = client.get_lead(uid)
            st.session_state["lead_status_cache"] = lead_detail
        except DubizzleAPIError:
            st.session_state["lead_status_cache"] = None

        st.session_state["backend_snapshot"] = snapshot
        st.session_state["profile_dirty"] = False
        st.session_state["api_error"] = None
        sync_query_params()
        return snapshot
    except DubizzleAPIError as exc:
        st.session_state["api_error"] = exc.detail
        cached = st.session_state.get("backend_snapshot")
        if isinstance(cached, dict) and (cached.get("user") or {}).get("user_id") == uid:
            return cached
        return None


def switch_profile(client: DubizzleAPIClient, user_id: str, name: Optional[str] = None) -> bool:
    """Switch active user profile, start clean session for that user, and reload state."""
    clean_uid = user_id.strip()
    if not clean_uid:
        st.session_state["profile_form_error"] = "User ID cannot be empty."
        return False

    st.session_state["profile_form_error"] = None
    st.session_state["user_id"] = clean_uid
    st.session_state["session_id"] = ""
    st.session_state["_pending_profile_name"] = name.strip() if name and name.strip() else None
    st.session_state["backend_snapshot"] = None
    st.session_state["pending_prompt"] = None
    st.session_state["pending_chat_turn"] = None
    st.session_state["chat_error"] = None
    st.session_state["chat_in_flight"] = False
    st.session_state["active_dialog"] = None
    st.session_state["booking_panel_open"] = False
    st.session_state["booking_listing_id"] = None
    st.session_state["booking_result"] = None
    st.session_state["booking_date_override"] = None
    st.session_state["booking_time_override"] = None
    st.session_state["booking_inline_error"] = None
    st.session_state["lead_inline_error"] = None
    st.session_state["lead_save_success"] = None
    st.session_state["forget_me_success"] = None
    st.session_state["api_error"] = None
    st.session_state["show_all_favs"] = False
    st.session_state["show_all_bookings"] = False
    st.session_state["show_all_searches"] = False
    st.session_state["_pref_form_seq"] = 0
    for wkey in (
        "_booking_pending_sync",
        "_booking_inventory_loaded",
        "booking_vehicle_select",
        "booking_date_picker",
        "booking_time_select",
        "booking_custom_time_input",
        "_booking_name_draft",
        "_booking_phone_draft",
    ):
        st.session_state.pop(wkey, None)
    st.session_state["dismissed_booking_ids"] = set()
    st.session_state["inspect_listing_id"] = None
    st.session_state["compare_listing_ids"] = []
    st.session_state["show_filter_search"] = False
    st.session_state["manual_search_result"] = None
    st.session_state["lead_status_cache"] = None
    sync_query_params()
    mark_dirty()
    snap = refresh_backend_snapshot(client, force=True)
    return snap is not None


def start_new_session(client: DubizzleAPIClient) -> bool:
    """Create a fresh session while keeping user profile, memory, favourites, and bookings."""
    uid = st.session_state["user_id"]
    try:
        sess = client.create_session(uid)["session"]
        st.session_state["session_id"] = sess["session_id"]
        st.session_state["pending_prompt"] = None
        st.session_state["pending_chat_turn"] = None
        st.session_state["chat_error"] = None
        st.session_state["chat_in_flight"] = False
        st.session_state["active_dialog"] = None
        st.session_state["booking_panel_open"] = False
        st.session_state["booking_result"] = None
        st.session_state["inspect_listing_id"] = None
        st.session_state["show_filter_search"] = False
        st.session_state["manual_search_result"] = None
        mark_dirty()
        refresh_backend_snapshot(client, force=True)
        return True
    except DubizzleAPIError as exc:
        st.session_state["api_error"] = exc.detail
        return False


def reset_after_forget_me() -> None:
    """Clear local state completely and start fresh visitor session after backend deletion."""
    preserved_injected = st.session_state.get("_injected_api_client")
    st.session_state.clear()
    st.query_params.clear()
    st.session_state["theme"] = "light"
    if preserved_injected is not None:
        st.session_state["_injected_api_client"] = preserved_injected
    fresh_uid = f"buyer_{uuid.uuid4().hex[:16]}"
    st.session_state["user_id"] = fresh_uid
    st.session_state["session_id"] = ""
    st.query_params["user_id"] = fresh_uid
    init_ui_state()
    mark_dirty()
    st.session_state["forget_me_success"] = (
        "Your profile and all associated data have been permanently deleted. "
        "You are now browsing with a fresh guest session."
    )
