"""Conversational discovery interface, top workspace bar, structured filters, and focus strip."""

from __future__ import annotations

import html
import re
import uuid
from typing import Any

import streamlit as st

from frontend.api_client import DubizzleAPIClient, DubizzleAPIError
from frontend.components.cards import format_vehicle_label, render_vehicle_cards_grid
from frontend.components.primitives import format_make_name, render_empty_state
from frontend.state import (
    build_grounded_suggested_prompts,
    get_cached_inventory_makes,
    hydrate_listing_summaries,
    mark_dirty,
    open_dialog,
    seed_summary_cache,
    set_compare_listings,
)

_BODY_TYPES = [
    "Any",
    "SUV",
    "Sedan",
    "Coupe",
    "Convertible",
    "Hatchback",
    "Pickup",
    "Van",
    "MPV",
    "Wagon",
    "Commercial",
]

_SPEC_REGIONS = [
    "Any",
    "GCC",
    "US / American",
    "Japanese",
    "Korean",
    "European",
    "Canadian",
    "Chinese",
]

_TRANSMISSIONS = ["Any", "Automatic", "Manual"]

_COLOURS = [
    "Any",
    "White",
    "Black",
    "Silver",
    "Grey",
    "Blue",
    "Red",
    "Green",
    "Gold",
    "Beige",
    "Brown",
    "Orange",
    "Yellow",
    "Bronze",
    "Burgundy",
    "Maroon",
    "Champagne",
]


def _format_returning_banner_text(raw_text: str, summary_cache: dict[int, dict[str, Any]]) -> str:
    """Enhance backend returning-user prose with formatted make/model labels where cached."""
    text = raw_text.strip()
    if not text:
        return ""

    def _replace_saved_id(match: re.Match[str]) -> str:
        lid = int(match.group(1))
        cached = summary_cache.get(lid)
        if cached:
            return format_vehicle_label(cached)
        return match.group(0)

    text = re.sub(r"\d{4}\s+[a-z0-9\- ]+\(#(\d+)\)", _replace_saved_id, text)
    return text


def _render_top_utility_bar(
    *,
    client: DubizzleAPIClient,
    session_obj: dict[str, Any],
    summary_cache: dict[int, dict[str, Any]],
) -> None:
    makes = get_cached_inventory_makes(client)
    total_listings = sum(int(m.get("count") or 0) for m in makes) if makes else 100
    total_makes = len([m for m in makes if int(m.get("count") or 0) > 0]) if makes else 29
    degraded = bool(st.session_state.get("degraded_mode"))
    dot_cls = "dbz-topbar-dot degraded" if degraded else "dbz-topbar-dot"
    mode_lbl = "Structured Search Mode (AI offline)" if degraded else "AI Discovery Online"

    focus_id = session_obj.get("current_vehicle")
    focus_badge = ""
    if focus_id:
        lid = int(focus_id)
        lbl = format_vehicle_label(summary_cache.get(lid, {"listing_id": lid}))
        focus_badge = f' · Focused: <strong>{html.escape(lbl)}</strong>'

    st.markdown(
        '<div class="dbz-topbar">'
        '<div class="dbz-topbar-left">'
        f'<span class="{dot_cls}"></span>'
        f'<span class="dbz-topbar-title">{mode_lbl}</span>'
        f'<span class="dbz-topbar-meta">· {total_listings} verified listings · {total_makes} makes{focus_badge}</span>'
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    compare_count = len(st.session_state.get("compare_listing_ids") or [])
    filters_open = bool(st.session_state.get("show_filter_search", False)) or degraded

    st.markdown('<span class="dbz-toolbar-marker"></span>', unsafe_allow_html=True)
    t1, t2, t3, t4 = st.columns(4, gap="small")
    if t1.button(
        "Hide Filters" if filters_open and not degraded else "Filter Inventory",
        key="tb_toggle_filters",
        use_container_width=True,
    ):
        st.session_state["show_filter_search"] = not bool(
            st.session_state.get("show_filter_search", False)
        )
        st.rerun()

    if t2.button(
        f"Compare ({compare_count})",
        key="tb_open_compare",
        use_container_width=True,
    ):
        open_dialog("compare")
        st.rerun()

    if t3.button(
        "Book Viewing",
        key="tb_open_booking",
        use_container_width=True,
    ):
        open_dialog("booking", listing_id=int(focus_id) if focus_id else None)
        st.rerun()

    if t4.button(
        "My Enquiry",
        key="tb_open_leads",
        use_container_width=True,
    ):
        open_dialog("leads")
        st.rerun()


def _render_search_transparency_bar(res: dict[str, Any]) -> None:
    total = int(res.get("total_matches") or 0)
    distinct = int(res.get("distinct_listing_groups") or total)
    dup_groups = int(res.get("suspected_duplicate_groups") or 0)
    excl_price = int(res.get("excluded_unknown_price") or 0)
    excl_attrs = res.get("excluded_unknown_attributes") or {}

    parts: list[str] = [f"<strong>{total}</strong> matching listing(s)"]
    if dup_groups > 0:
        parts.append(f"<strong>{distinct}</strong> distinct ad group(s) ({dup_groups} near-duplicate group)")
    if excl_price > 0:
        parts.append(f"<strong>{excl_price}</strong> excluded (cash price unlisted)")
    for attr_name, cnt in excl_attrs.items():
        if int(cnt or 0) > 0:
            clean_attr = attr_name.replace("_", " ")
            parts.append(f"<strong>{int(cnt)}</strong> excluded ({html.escape(clean_attr)} unlisted)")

    st.markdown(
        f'<div class="dbz-transparency-bar"><span>{" · ".join(parts)}</span></div>',
        unsafe_allow_html=True,
    )


def _render_filter_drawer(
    *,
    client: DubizzleAPIClient,
    user_id: str,
    session_id: str,
    favourite_ids: set[int],
) -> None:
    degraded = bool(st.session_state.get("degraded_mode"))
    is_open = degraded or bool(st.session_state.get("show_filter_search", False))
    if not is_open:
        return

    makes_data = get_cached_inventory_makes(client)
    make_options = ["Any"] + [m["make"] for m in makes_data if m.get("count", 0) > 0]

    with st.container(border=True):
        st.markdown("**Structured Inventory Filter & Catalog Search**")
        st.caption(
            "Filter directly against verified SQLite attributes. Unknown prices or specs never match strict numeric filters."
        )

        with st.form("direct_inventory_filter_form", clear_on_submit=False):
            c1, c2, c3, c4 = st.columns(4, gap="small")
            sel_make = c1.selectbox(
                "Make",
                options=make_options,
                format_func=lambda m: "Any" if m == "Any" else format_make_name(m),
            )
            model_text = c2.text_input("Model (optional)", placeholder="e.g. GLC, CR-V, Q8")
            sel_body = c3.selectbox("Body type", options=_BODY_TYPES)
            sel_spec = c4.selectbox("Spec region", options=_SPEC_REGIONS)

            c5, c6, c7, c8 = st.columns(4, gap="small")
            sel_trans = c5.selectbox("Transmission", options=_TRANSMISSIONS)
            sel_colour = c6.selectbox("Exterior colour", options=_COLOURS)
            min_year = c7.number_input("Min year (0 = any)", min_value=0, max_value=2026, value=0, step=1)
            max_year = c8.number_input("Max year (0 = any)", min_value=0, max_value=2026, value=0, step=1)

            c9, c10, c11 = st.columns([1, 1, 1.4], gap="small")
            max_price = c9.number_input("Max cash price (AED, 0 = any)", min_value=0, value=0, step=10000)
            max_km = c10.number_input("Max mileage (km, 0 = any)", min_value=0, value=0, step=10000)
            keywords = c11.text_input("Keywords (optional)", placeholder="e.g. sunroof, warranty, 7 seater")

            warranty_only = st.checkbox("Confirmed active warranty only", value=False)
            b_sub, b_clr = st.columns([2, 1], gap="small")
            submitted = b_sub.form_submit_button("Search Inventory", type="primary", use_container_width=True)
            cleared = b_clr.form_submit_button("Clear Results", use_container_width=True)

        if cleared:
            st.session_state["manual_search_result"] = None
            st.rerun()

        if submitted:
            if min_year > 0 and max_year > 0 and min_year > max_year:
                st.warning("Minimum year cannot exceed maximum year.")
            else:
                filters: dict[str, Any] = {}
                if sel_make != "Any":
                    filters["make"] = sel_make
                if model_text.strip():
                    filters["model"] = model_text.strip()
                if sel_spec != "Any":
                    filters["spec_region"] = sel_spec
                if sel_body != "Any":
                    filters["body_type"] = sel_body
                if sel_trans != "Any":
                    filters["transmission"] = sel_trans
                if sel_colour != "Any":
                    filters["colour"] = sel_colour
                if min_year > 0:
                    filters["min_year"] = int(min_year)
                if max_year > 0:
                    filters["max_year"] = int(max_year)
                if max_price > 0:
                    filters["max_price"] = float(max_price)
                if max_km > 0:
                    filters["max_mileage"] = float(max_km)
                if keywords.strip():
                    filters["free_text"] = keywords.strip()
                if warranty_only:
                    filters["warranty"] = True

                try:
                    res = client.search_in_session(session_id, user_id, filters, limit=12)
                    seed_summary_cache(res.get("results") or [])
                    st.session_state["manual_search_result"] = res
                    st.session_state["show_filter_search"] = True
                    mark_dirty()
                    st.rerun()
                except DubizzleAPIError as exc:
                    st.session_state["api_error"] = exc.detail
                    st.rerun()

        manual_res = st.session_state.get("manual_search_result")
        if manual_res:
            _render_search_transparency_bar(manual_res)
            results = manual_res.get("results") or []
            if not results:
                render_empty_state(
                    "No matching listings found",
                    str(manual_res.get("message") or "Try broadening your budget, year, or mileage filters."),
                )
            else:
                if len(results) >= 2:
                    if st.button(
                        f"Compare top {min(len(results), 4)} filter matches",
                        key="cmp_filter_results_btn",
                    ):
                        set_compare_listings([int(r["listing_id"]) for r in results[:4]])
                        open_dialog("compare")
                        st.rerun()
                render_vehicle_cards_grid(
                    results,
                    client=client,
                    user_id=user_id,
                    session_id=session_id,
                    favourite_ids=favourite_ids,
                    key_prefix="filter_res",
                )


def _render_focus_strip(
    *,
    session_obj: dict[str, Any],
    summary_cache: dict[int, dict[str, Any]],
) -> None:
    focus_id = session_obj.get("current_vehicle")
    if not focus_id:
        return
    lid = int(focus_id)
    item = summary_cache.get(lid, {"listing_id": lid})
    label = format_vehicle_label(item)

    st.markdown(
        '<div class="dbz-focus-strip">'
        "<div>"
        '<div class="dbz-focus-title">Active vehicle in context</div>'
        f'<div class="dbz-focus-meta">{html.escape(label)}</div>'
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown('<span class="dbz-keep-2col"></span>', unsafe_allow_html=True)
    q1, q2 = st.columns(2, gap="small")
    if q1.button("What's the mileage?", key=f"focus_km_{lid}", use_container_width=True):
        st.session_state["pending_prompt"] = "What's the mileage on this car?"
        st.rerun()
    if q2.button("Does it have a warranty?", key=f"focus_war_{lid}", use_container_width=True):
        st.session_state["pending_prompt"] = "Does it have a warranty?"
        st.rerun()

    st.markdown('<span class="dbz-keep-2col"></span>', unsafe_allow_html=True)
    q3, q4 = st.columns(2, gap="small")
    if q3.button("Inspect full specs", key=f"focus_det_{lid}", use_container_width=True):
        open_dialog("details", listing_id=lid)
        st.rerun()
    if q4.button("Book viewing for this car", key=f"focus_book_{lid}", use_container_width=True):
        open_dialog("booking", listing_id=lid)
        st.rerun()


def _render_first_visit_welcome(
    *,
    client: DubizzleAPIClient,
    snapshot: dict[str, Any],
    summary_cache: dict[int, dict[str, Any]],
    user_id: str,
    session_id: str,
) -> None:
    returning_summary = snapshot.get("returning_summary")
    searches = snapshot.get("searches") or []
    favs = snapshot.get("favourites") or []
    bookings = snapshot.get("bookings") or []
    pending_bookings = [b for b in bookings if b.get("status") == "proposed"]

    if returning_summary and returning_summary.get("is_returning") and returning_summary.get("welcome_message"):
        formatted_msg = _format_returning_banner_text(
            str(returning_summary["welcome_message"]),
            summary_cache,
        )
        st.markdown(
            '<div class="dbz-returning-banner">'
            '<div class="dbz-returning-eyebrow">Welcome back</div>'
            f'<div class="dbz-returning-text">{html.escape(formatted_msg)}</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        # Actionable 1-click continuity triggers
        actions: list[tuple[str, str]] = []
        if searches:
            actions.append(("resume_search", "Resume last search"))
        if len(favs) >= 2:
            actions.append(("compare_saved", f"Compare saved ({min(len(favs), 4)})"))
        if pending_bookings:
            actions.append(("confirm_pending", f"Review pending booking #{pending_bookings[0]['booking_id']}"))
        if actions:
            acols = st.columns(len(actions), gap="small")
            for idx, (act_id, act_label) in enumerate(actions):
                if acols[idx].button(act_label, key=f"ret_act_{act_id}", use_container_width=True):
                    if act_id == "resume_search":
                        last_s = searches[0]
                        flt = last_s.get("filters") or {}
                        qtxt = str(last_s.get("query_text") or "").strip()
                        if flt:
                            try:
                                res = client.search_in_session(session_id, user_id, flt, limit=12)
                                seed_summary_cache(res.get("results") or [])
                                st.session_state["manual_search_result"] = res
                                st.session_state["show_filter_search"] = True
                                mark_dirty()
                                st.rerun()
                            except DubizzleAPIError as exc:
                                st.session_state["api_error"] = exc.detail
                                st.rerun()
                        elif qtxt:
                            st.session_state["pending_prompt"] = qtxt
                            st.rerun()
                    elif act_id == "compare_saved":
                        set_compare_listings([int(f["listing_id"]) for f in favs[:4]])
                        open_dialog("compare")
                        st.rerun()
                    elif act_id == "confirm_pending":
                        bk = pending_bookings[0]
                        open_dialog(
                            "booking",
                            listing_id=int(bk["listing_id"]),
                            booking_result={"success": True, "booking": bk, "message": "", "alternatives": []},
                        )
                        st.rerun()

    makes = get_cached_inventory_makes(client)
    total_listings = sum(int(m.get("count") or 0) for m in makes) if makes else 100
    total_makes = len([m for m in makes if int(m.get("count") or 0) > 0]) if makes else 29

    st.markdown(
        '<div class="dbz-hero">'
        '<div class="dbz-hero-kicker">UAE Used-Car Discovery Assistant</div>'
        '<h1 class="dbz-hero-title">Find verified used cars through natural conversation.</h1>'
        '<p class="dbz-hero-desc">'
        "Search by budget, regional spec, mileage, body type, or warranty—or browse structured filters directly. "
        "Every specification is grounded in listing evidence, cash prices are separated from financing estimates, "
        "and viewings schedule cleanly in Asia/Dubai time."
        "</p>"
        '<div class="dbz-hero-stats">'
        f"<span><strong>{total_listings}</strong> verified listings</span>"
        f"<span><strong>{total_makes}</strong> automotive makes</span>"
        "<span><strong>Evidence-backed</strong> specs &amp; cash prices</span>"
        "<span><strong>Mon–Sat 08:00–20:00</strong> Dubai viewings</span>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    prompts = build_grounded_suggested_prompts(client)
    if prompts:
        st.markdown(
            '<div class="dbz-section-label">Suggested searches from live inventory</div>',
            unsafe_allow_html=True,
        )
        cols = st.columns(2, gap="small")
        for idx, p_text in enumerate(prompts):
            if cols[idx % 2].button(
                p_text,
                key=f"suggested_prompt_{idx}",
                use_container_width=True,
            ):
                st.session_state["pending_prompt"] = p_text
                st.rerun()


def _submit_chat_turn(
    *,
    client: DubizzleAPIClient,
    user_id: str,
    session_id: str,
    prompt_text: str,
    pending_slot: Any,
) -> None:
    clean = prompt_text.strip()
    if not clean or st.session_state.get("chat_in_flight"):
        return

    existing_turn = st.session_state.get("pending_chat_turn")
    if (
        isinstance(existing_turn, dict)
        and existing_turn.get("session_id") == session_id
        and existing_turn.get("user_id") == user_id
        and existing_turn.get("message") == clean
        and existing_turn.get("request_id")
    ):
        req_id = str(existing_turn["request_id"])
    else:
        req_id = uuid.uuid4().hex
        st.session_state["pending_chat_turn"] = {
            "request_id": req_id,
            "session_id": session_id,
            "user_id": user_id,
            "message": clean,
        }

    st.session_state["chat_in_flight"] = True
    st.session_state["chat_error"] = None
    with pending_slot.container():
        st.markdown(
            '<div class="dbz-msg-user-card">'
            '<div class="dbz-msg-header">You</div>'
            f'<div class="dbz-msg-user-body">{html.escape(clean)}</div>'
            "</div>",
            unsafe_allow_html=True,
        )
        with st.spinner("Searching verified inventory and validating specifications..."):
            try:
                resp = client.chat(session_id, user_id, clean, request_id=req_id)
                st.session_state["degraded_mode"] = bool(resp.get("degraded_mode", False))
                cards = resp.get("vehicle_cards") or []
                if cards:
                    seed_summary_cache(cards)
                st.session_state["manual_search_result"] = None
                st.session_state["pending_chat_turn"] = None
                st.session_state["chat_error"] = None
                st.session_state["api_error"] = None
            except DubizzleAPIError as exc:
                st.session_state["chat_error"] = exc.detail
            finally:
                st.session_state["chat_in_flight"] = False
            mark_dirty()
            st.rerun()


def render_chat_workspace(
    *,
    client: DubizzleAPIClient,
    snapshot: dict[str, Any],
) -> None:
    """Render top workspace controls, filters, welcome/chat timeline, cards, focus strip, and composer."""
    user_obj = snapshot.get("user") or {}
    user_id = str(user_obj.get("user_id") or st.session_state["user_id"])
    session_obj = snapshot.get("session") or {}
    session_id = str(session_obj.get("session_id") or st.session_state.get("session_id") or "")
    messages = session_obj.get("recent_messages") or []
    favourite_ids = {int(f["listing_id"]) for f in (snapshot.get("favourites") or [])}
    summary_cache: dict[int, dict[str, Any]] = st.session_state.get("listing_summary_cache", {})

    _render_top_utility_bar(
        client=client,
        session_obj=session_obj,
        summary_cache=summary_cache,
    )

    if st.session_state.get("degraded_mode"):
        st.info(
            "AI conversational generation is currently in degraded mode (Gemini key unavailable or unreachable). "
            "Structured inventory search, vehicle cards, comparison, favourites, leads, and viewing bookings remain active."
        )

    _render_filter_drawer(
        client=client,
        user_id=user_id,
        session_id=session_id,
        favourite_ids=favourite_ids,
    )

    if not messages:
        _render_first_visit_welcome(
            client=client,
            snapshot=snapshot,
            summary_cache=summary_cache,
            user_id=user_id,
            session_id=session_id,
        )
    else:
        for idx, msg in enumerate(messages):
            role = str(msg.get("role") or "assistant")
            content = str(msg.get("content") or "")
            if role == "user":
                st.markdown(
                    '<div class="dbz-msg-user-card">'
                    '<div class="dbz-msg-header">You</div>'
                    f'<div class="dbz-msg-user-body">{html.escape(content)}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )
            else:
                with st.container():
                    st.markdown(
                        '<div class="dbz-msg-assistant-marker"></div>'
                        '<div class="dbz-msg-header assistant">Car Assistant</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(content)
                card_ids = [int(cid) for cid in (msg.get("vehicle_cards") or [])]
                if card_ids:
                    cache = hydrate_listing_summaries(client, card_ids)
                    turn_cards = [cache[cid] for cid in card_ids if cid in cache]
                    if len(turn_cards) >= 2:
                        h_l, h_r = st.columns([2.1, 1.2], gap="small")
                        h_l.caption(f"Showing {len(turn_cards)} verified listing(s) referenced in this reply")
                        if h_r.button(
                            f"Compare ({min(len(turn_cards), 4)})",
                            key=f"turn_cmp_{idx}",
                            use_container_width=True,
                        ):
                            set_compare_listings([int(c["listing_id"]) for c in turn_cards[:4]])
                            open_dialog("compare")
                            st.rerun()
                    render_vehicle_cards_grid(
                        turn_cards,
                        client=client,
                        user_id=user_id,
                        session_id=session_id,
                        favourite_ids=favourite_ids,
                        key_prefix=f"turn_{idx}",
                    )

    pending_slot = st.empty()

    pending_turn = st.session_state.get("pending_chat_turn")
    chat_err = st.session_state.get("chat_error")
    if (
        chat_err
        and isinstance(pending_turn, dict)
        and pending_turn.get("session_id") == session_id
        and pending_turn.get("user_id") == user_id
    ):
        failed_msg = str(pending_turn.get("message") or "")
        if failed_msg and (not messages or messages[-1].get("content") != failed_msg):
            st.markdown(
                '<div class="dbz-msg-user-card">'
                '<div class="dbz-msg-header">You (not sent)</div>'
                f'<div class="dbz-msg-user-body">{html.escape(failed_msg)}</div>'
                "</div>",
                unsafe_allow_html=True,
            )
        st.error(str(chat_err))
        r_col, d_col = st.columns(2, gap="small")
        if r_col.button("Retry message", key="retry_failed_chat_btn", use_container_width=True):
            st.session_state["chat_error"] = None
            st.session_state["pending_prompt"] = failed_msg
            st.rerun()
        if d_col.button("Dismiss", key="dismiss_failed_chat_btn", use_container_width=True):
            st.session_state["pending_chat_turn"] = None
            st.session_state["chat_error"] = None
            st.rerun()

    _render_focus_strip(
        session_obj=session_obj,
        summary_cache=summary_cache,
    )

    queued = st.session_state.pop("pending_prompt", None)
    user_typed = st.chat_input("Ask about cars, prices, specs, or viewings...")

    active_prompt = queued or user_typed
    if active_prompt:
        _submit_chat_turn(
            client=client,
            user_id=user_id,
            session_id=session_id,
            prompt_text=str(active_prompt),
            pending_slot=pending_slot,
        )
