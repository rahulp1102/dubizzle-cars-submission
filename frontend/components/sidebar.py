"""Compact, cohesive Buyer Workspace sidebar: profile, memory, shortlist, bookings, searches, leads, privacy."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from frontend.api_client import DubizzleAPIClient, DubizzleAPIError
from frontend.components.primitives import format_make_name, format_vehicle_label
from frontend.state import (
    mark_dirty,
    open_dialog,
    reset_after_forget_me,
    seed_summary_cache,
    set_compare_listings,
    start_new_session,
    switch_profile,
)

_PREF_LABELS: dict[str, str] = {
    "budget_max": "Max budget",
    "preferred_makes": "Preferred makes",
    "preferred_models": "Preferred models",
    "preferred_body_types": "Body type",
    "preferred_colour": "Preferred colour",
    "spec_region": "Spec region",
    "max_mileage_km": "Max mileage",
    "min_year": "Minimum year",
    "transmission": "Transmission",
    "warranty_required": "Warranty",
    "must_have_features": "Must-have features",
    "excluded_features": "Excluded features",
    "user_name": "Name",
}

_EDITABLE_PREF_KEYS: list[str] = [
    "budget_max",
    "preferred_makes",
    "preferred_models",
    "preferred_body_types",
    "spec_region",
    "max_mileage_km",
    "min_year",
    "transmission",
    "warranty_required",
    "preferred_colour",
    "must_have_features",
]

_PREF_HINTS: dict[str, str] = {
    "budget_max": "e.g. 150000",
    "preferred_makes": "e.g. Mercedes-Benz, Toyota",
    "preferred_models": "e.g. GLC-Class, CR-V",
    "preferred_body_types": "e.g. SUV, Sedan, Coupe",
    "spec_region": "e.g. GCC, Japanese, European",
    "max_mileage_km": "e.g. 60000",
    "min_year": "e.g. 2022",
    "transmission": "Automatic or Manual",
    "warranty_required": "Yes",
    "preferred_colour": "e.g. White, Black, Red",
    "must_have_features": "e.g. sunroof, full service history",
}


def _format_pref_value(pref: dict[str, Any]) -> str:
    key = str(pref.get("preference_key") or "")
    val = str(pref.get("preference_value") or "")
    meta = pref.get("metadata") or {}
    if key == "budget_max" and meta:
        orig_amt = meta.get("original_amount")
        orig_curr = meta.get("original_currency", "AED")
        norm_aed = meta.get("normalized_amount_aed")
        if orig_curr == "USD" and orig_amt is not None and norm_aed is not None:
            return f"USD {float(orig_amt):,.0f} (~AED {float(norm_aed):,.0f})"
        if norm_aed is not None:
            return f"AED {float(norm_aed):,.0f}"
    if key == "max_mileage_km":
        try:
            return f"{int(float(val)):,} km"
        except ValueError:
            return val
    if key == "preferred_makes":
        parts = [format_make_name(p.strip()) for p in val.split(",") if p.strip()]
        return ", ".join(parts) if parts else val
    return val


def _format_search_summary(search_entry: dict[str, Any]) -> str:
    qtxt = str(search_entry.get("query_text") or "").strip()
    if qtxt:
        return qtxt
    flt = search_entry.get("filters") or {}
    bits: list[str] = []
    if flt.get("make"):
        bits.append(format_make_name(str(flt["make"])))
    if flt.get("model"):
        bits.append(str(flt["model"]).upper())
    if flt.get("spec_region"):
        bits.append(f"{flt['spec_region']} spec")
    if flt.get("body_type"):
        bits.append(str(flt["body_type"]))
    if flt.get("max_price"):
        bits.append(f"<= AED {int(round(float(flt['max_price']))):,}")
    if flt.get("max_mileage"):
        bits.append(f"<= {int(round(float(flt['max_mileage']))):,} km")
    if flt.get("colour"):
        bits.append(str(flt["colour"]))
    if flt.get("warranty"):
        bits.append("Warranty")
    return " · ".join(bits) if bits else "All inventory"


def render_sidebar(
    *,
    client: DubizzleAPIClient,
    snapshot: dict[str, Any],
) -> None:
    """Render Buyer Workspace sidebar with persisted profile, memory, shortlist, bookings, and privacy."""
    user_obj = snapshot.get("user") or {}
    uid = str(user_obj.get("user_id") or st.session_state["user_id"])
    uname = user_obj.get("name") or ""
    session_obj = snapshot.get("session") or {}
    sid = str(session_obj.get("session_id") or st.session_state.get("session_id") or "")
    summary_cache: dict[int, dict[str, Any]] = st.session_state.get("listing_summary_cache", {})

    with st.sidebar:
        # ── Branding ──
        st.markdown(
            '<div class="dbz-brand">'
            '<span class="dbz-wordmark">dubizzle</span>'
            '<span class="dbz-submark">UAE Car Assistant</span>'
            "</div>",
            unsafe_allow_html=True,
        )

        # ── Profile & Session Controls ──
        st.markdown('<div class="dbz-section-label">Profile & Session</div>', unsafe_allow_html=True)
        name_display = html.escape(uname) if uname else "Guest buyer"
        lead_detail = st.session_state.get("lead_status_cache") or {}
        lead_obj = lead_detail.get("lead") if isinstance(lead_detail, dict) else None
        lead_badge = ""
        if lead_obj and lead_obj.get("status"):
            l_stat = str(lead_obj["status"]).lower()
            lead_badge = f' <span class="dbz-pill {l_stat}">{html.escape(l_stat.upper())} LEAD</span>'

        st.markdown(
            f'<div class="dbz-sidebar-item">'
            f'<strong>{name_display}</strong>{lead_badge}<br/>'
            f'<span class="dbz-sidebar-sub">ID: <code>{html.escape(uid)}</code></span>'
            "</div>",
            unsafe_allow_html=True,
        )

        with st.expander("Switch or create profile", expanded=False):
            with st.form("profile_switch_form", clear_on_submit=False):
                target_uid = st.text_input("User ID", value=uid)
                target_name = st.text_input("Display name (optional)", value=uname)
                submitted = st.form_submit_button("Load / Create Profile", use_container_width=True)
            if submitted:
                switched = switch_profile(client, target_uid, target_name)
                if switched or not st.session_state.get("profile_form_error"):
                    st.rerun()
            if st.session_state.get("profile_form_error"):
                st.warning(str(st.session_state["profile_form_error"]))

        c_new, c_book = st.columns(2, gap="small")
        if c_new.button("New chat", key="sidebar_new_chat", use_container_width=True):
            if start_new_session(client):
                st.rerun()
        if c_book.button("Book viewing", key="sidebar_open_booking", use_container_width=True):
            open_dialog("booking")
            st.rerun()

        # ── Remembered Preferences ──
        prefs = snapshot.get("preferences") or []
        pref_history = snapshot.get("preference_history") or []
        st.markdown(
            f'<div class="dbz-section-label">Remembered Preferences ({len(prefs)})</div>',
            unsafe_allow_html=True,
        )
        if not prefs:
            st.caption("No saved preferences yet. Share your budget or preferred spec in chat.")
        else:
            for idx, pref in enumerate(prefs):
                pkey = str(pref.get("preference_key") or "")
                label = _PREF_LABELS.get(pkey, pkey.replace("_", " ").title())
                val_text = _format_pref_value(pref)
                src = str(pref.get("source_type") or "explicit")
                col_p, col_del = st.columns([4.2, 1], gap="small")
                col_p.markdown(
                    f'<div class="dbz-sidebar-item"><strong>{html.escape(label)}:</strong> '
                    f"{html.escape(val_text)} "
                    f'<span class="dbz-sidebar-sub">({html.escape(src)})</span>'
                    "</div>",
                    unsafe_allow_html=True,
                )
                if col_del.button(
                    "✕",
                    key=f"del_pref_{uid}_{pkey}_{idx}",
                    help=f"Remove {label} preference",
                    use_container_width=True,
                ):
                    try:
                        client.delete_preference(uid, pkey)
                        mark_dirty()
                        st.rerun()
                    except DubizzleAPIError as exc:
                        st.session_state["api_error"] = exc.detail
                        st.rerun()

        with st.expander("Add / update preference", expanded=False):
            pref_seq = int(st.session_state.get("_pref_form_seq", 0))
            pref_choice = st.selectbox(
                "Preference",
                options=_EDITABLE_PREF_KEYS,
                format_func=lambda k: _PREF_LABELS.get(k, k),
                key=f"pref_key_select_{uid}",
            )
            pref_val = st.text_input(
                "Value",
                placeholder=_PREF_HINTS.get(pref_choice, "Enter preference value"),
                key=f"pref_val_input_{uid}_{pref_seq}",
            )
            pref_curr = "AED"
            if pref_choice == "budget_max":
                pref_curr = st.selectbox(
                    "Currency",
                    options=["AED", "USD"],
                    key=f"pref_curr_select_{uid}",
                )
            if st.button("Save Preference", key=f"save_pref_btn_{uid}", use_container_width=True):
                cleaned_val = pref_val.strip()
                if not cleaned_val:
                    st.warning("Please enter a preference value before saving.")
                else:
                    try:
                        kwargs: dict[str, Any] = {
                            "preference_key": pref_choice,
                            "preference_value": cleaned_val,
                            "original_wording": f"UI preference: {pref_choice}={cleaned_val}",
                            "source_type": "ui",
                        }
                        if pref_choice == "budget_max":
                            num_str = "".join(ch for ch in cleaned_val if ch.isdigit() or ch == ".")
                            if not num_str:
                                raise ValueError("invalid budget")
                            kwargs["amount"] = float(num_str)
                            kwargs["currency"] = pref_curr
                        elif pref_choice == "max_mileage_km":
                            num_str = "".join(ch for ch in cleaned_val if ch.isdigit())
                            if not num_str:
                                raise ValueError("invalid mileage")
                            kwargs["preference_value"] = num_str
                        elif pref_choice == "min_year":
                            num_str = "".join(ch for ch in cleaned_val if ch.isdigit())
                            if len(num_str) != 4:
                                raise ValueError("invalid year")
                            kwargs["preference_value"] = num_str

                        client.upsert_preference(uid, **kwargs)
                        st.session_state["_pref_form_seq"] = pref_seq + 1
                        mark_dirty()
                        st.rerun()
                    except ValueError:
                        st.warning("Please enter a valid numeric value.")
                    except DubizzleAPIError as exc:
                        st.session_state["api_error"] = exc.detail
                        st.rerun()

        if pref_history:
            with st.expander(f"Preference change log ({len(pref_history)})", expanded=False):
                for hist in pref_history[:8]:
                    h_key = _PREF_LABELS.get(
                        str(hist.get("preference_key") or ""),
                        str(hist.get("preference_key") or ""),
                    )
                    old_v = hist.get("old_value") or "—"
                    new_v = hist.get("new_value") or "—"
                    src = hist.get("source_type") or "explicit"
                    st.caption(f"• **{h_key}**: {old_v} → {new_v} ({src})")

        # ── Saved Vehicles ──
        favs = snapshot.get("favourites") or []
        st.markdown(
            f'<div class="dbz-section-label">Saved Vehicles ({len(favs)})</div>',
            unsafe_allow_html=True,
        )
        if not favs:
            st.caption("No saved vehicles yet.")
        else:
            if len(favs) >= 2:
                if st.button(
                    f"Compare Saved ({min(len(favs), 4)})",
                    key=f"sb_compare_favs_{uid}",
                    use_container_width=True,
                ):
                    set_compare_listings([int(f["listing_id"]) for f in favs[:4]])
                    open_dialog("compare")
                    st.rerun()

            show_all_favs = bool(st.session_state.get("show_all_favs", False))
            visible_favs = favs if show_all_favs else favs[:4]
            for idx, item in enumerate(visible_favs):
                lid = int(item["listing_id"])
                lbl = format_vehicle_label(item)
                cash = item.get("cash_price")
                cash_status = item.get("cash_price_status")
                if cash is not None and cash_status == "confirmed":
                    price_txt = f"AED {int(round(float(cash))):,}"
                elif cash is not None and cash_status == "inferred":
                    price_txt = "Price conflict — check details"
                else:
                    price_txt = "Price not listed"

                st.markdown(
                    f'<div class="dbz-sidebar-item"><strong>{html.escape(lbl)}</strong><br/>'
                    f'<span class="dbz-sidebar-sub">{html.escape(price_txt)}</span></div>',
                    unsafe_allow_html=True,
                )
                fa, fb, fc = st.columns(3, gap="small")
                if fa.button("Ask", key=f"fav_ask_{uid}_{lid}_{idx}", use_container_width=True):
                    try:
                        client.focus_vehicle(sid, uid, lid)
                        mark_dirty()
                        st.rerun()
                    except DubizzleAPIError as exc:
                        st.session_state["api_error"] = exc.detail
                        st.rerun()
                if fb.button("Book", key=f"fav_book_{uid}_{lid}_{idx}", use_container_width=True):
                    open_dialog("booking", listing_id=lid)
                    st.rerun()
                if fc.button("Remove", key=f"fav_rm_{uid}_{lid}_{idx}", use_container_width=True):
                    try:
                        client.remove_favourite(uid, lid)
                        mark_dirty()
                        st.rerun()
                    except DubizzleAPIError as exc:
                        st.session_state["api_error"] = exc.detail
                        st.rerun()

            if len(favs) > 4:
                toggle_lbl = "Show fewer saved" if show_all_favs else f"Show all ({len(favs)})"
                if st.button(toggle_lbl, key=f"toggle_all_favs_{uid}", use_container_width=True):
                    st.session_state["show_all_favs"] = not show_all_favs
                    st.rerun()

        # ── Booking History ──
        bookings = snapshot.get("bookings") or []
        st.markdown(
            f'<div class="dbz-section-label">Booking History ({len(bookings)})</div>',
            unsafe_allow_html=True,
        )
        if not bookings:
            st.caption("No viewing bookings yet.")
        else:
            show_all_bk = bool(st.session_state.get("show_all_bookings", False))
            visible_bookings = bookings if show_all_bk else bookings[:4]
            for idx, bk in enumerate(visible_bookings):
                bid = int(bk["booking_id"])
                lid = int(bk["listing_id"])
                status = str(bk.get("status") or "proposed")
                pill_cls = (
                    "confirmed"
                    if status == "confirmed"
                    else ("proposed" if status == "proposed" else "rejected")
                )
                status_text = (
                    "CONFIRMED"
                    if status == "confirmed"
                    else ("PROPOSED · NOT HELD" if status == "proposed" else status.upper())
                )
                veh = format_vehicle_label(summary_cache.get(lid, {"listing_id": lid}))
                st.markdown(
                    f'<div class="dbz-sidebar-item">'
                    f'<span class="dbz-pill {pill_cls}">{html.escape(status_text)}</span> '
                    f'<strong>#{bid}</strong><br/>'
                    f'{html.escape(veh)}<br/>'
                    f'<span class="dbz-sidebar-sub">'
                    f'{html.escape(str(bk["booking_date"]))} {html.escape(str(bk["start_time"]))}–'
                    f'{html.escape(str(bk["end_time"]))} ({html.escape(str(bk.get("timezone", "Asia/Dubai")))})'
                    f"</span></div>",
                    unsafe_allow_html=True,
                )
                if status == "proposed":
                    if st.button(
                        f"Review & Confirm #{bid}",
                        key=f"sb_confirm_{uid}_{bid}_{idx}",
                        use_container_width=True,
                    ):
                        open_dialog(
                            "booking",
                            listing_id=lid,
                            booking_result={
                                "success": True,
                                "booking": bk,
                                "message": "",
                                "alternatives": [],
                            },
                        )
                        st.rerun()

            if len(bookings) > 4:
                toggle_bk_lbl = "Show fewer bookings" if show_all_bk else f"Show all ({len(bookings)})"
                if st.button(toggle_bk_lbl, key=f"toggle_all_bk_{uid}", use_container_width=True):
                    st.session_state["show_all_bookings"] = not show_all_bk
                    st.rerun()

        # ── Recent Searches (Click to Re-Run) ──
        searches = snapshot.get("searches") or []
        if searches:
            with st.expander(f"Recent searches ({len(searches)})", expanded=False):
                show_all_s = bool(st.session_state.get("show_all_searches", False))
                visible_s = searches if show_all_s else searches[:5]
                for idx, s in enumerate(visible_s):
                    summary_lbl = _format_search_summary(s)
                    cnt = int(s.get("result_count") or 0)
                    flt = s.get("filters") or {}
                    qtxt = str(s.get("query_text") or "").strip()
                    if st.button(
                        f"{summary_lbl} ({cnt})",
                        key=f"sb_rerun_search_{uid}_{idx}",
                        use_container_width=True,
                    ):
                        if flt:
                            try:
                                res = client.search_in_session(sid, uid, flt, limit=10)
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
                if len(searches) > 5:
                    t_s = "Show fewer" if show_all_s else f"Show all ({len(searches)})"
                    if st.button(t_s, key=f"toggle_all_s_{uid}", use_container_width=True):
                        st.session_state["show_all_searches"] = not show_all_s
                        st.rerun()

        # ── Buyer Enquiry ──
        st.markdown('<div class="dbz-section-label">Buyer Enquiry</div>', unsafe_allow_html=True)
        if st.button(
            "My Enquiry Profile",
            key="sidebar_open_leads",
            use_container_width=True,
        ):
            open_dialog("leads")
            st.rerun()

        # ── Privacy / Forget Me ──
        st.markdown('<div class="dbz-section-label">Privacy</div>', unsafe_allow_html=True)
        with st.expander("Forget Me (delete my data)", expanded=False):
            st.caption(
                "Permanently removes your profile, preferences, saved vehicles, conversation sessions, bookings, and lead data."
            )
            confirmed = st.checkbox(
                "Confirm permanent deletion of all my data",
                key=f"forget_me_confirm_chk_{uid}",
            )
            if st.button(
                "Delete All My Data",
                key=f"forget_me_btn_{uid}",
                type="primary",
                disabled=not confirmed,
                use_container_width=True,
            ):
                try:
                    client.delete_user(uid, confirm=True)
                    reset_after_forget_me()
                    st.rerun()
                except DubizzleAPIError as exc:
                    st.session_state["api_error"] = exc.detail
                    st.rerun()
