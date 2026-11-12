"""Two-step vehicle viewing booking dialog (proposal -> explicit confirmation)."""

from __future__ import annotations

import html
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import streamlit as st

from frontend.api_client import DubizzleAPIClient, DubizzleAPIError
from frontend.components.primitives import (
    format_vehicle_label,
    is_masked_phone,
    mask_phone_display,
)
from frontend.state import close_dialog, mark_dirty, seed_summary_cache

_DUBAI_TZ = ZoneInfo("Asia/Dubai")

_TIME_OPTIONS = [
    "07:00",
    "08:00",
    "09:00",
    "10:00",
    "11:00",
    "12:00",
    "13:00",
    "14:00",
    "15:00",
    "16:00",
    "17:00",
    "18:00",
    "19:00",
    "20:00",
    "Custom time...",
]


def _default_dubai_tomorrow() -> date:
    dubai_today = datetime.now(_DUBAI_TZ).date()
    cand = dubai_today + timedelta(days=1)
    if cand.weekday() == 6:
        cand = cand + timedelta(days=1)
    return cand


def _vehicle_options_from_api(
    client: DubizzleAPIClient,
    snapshot: dict[str, Any],
    preferred_listing_id: Optional[int],
) -> list[dict[str, Any]]:
    cache: dict[int, dict[str, Any]] = st.session_state.get("listing_summary_cache", {})
    if not st.session_state.get("_booking_inventory_loaded"):
        try:
            res = client.search_inventory(limit=100)
            seed_summary_cache(res.get("results", []))
            st.session_state["_booking_inventory_loaded"] = True
            cache = st.session_state.get("listing_summary_cache", {})
        except DubizzleAPIError:
            pass

    if preferred_listing_id and preferred_listing_id not in cache:
        try:
            fetched = client.get_listing_summaries([preferred_listing_id])
            seed_summary_cache(fetched)
            cache = st.session_state.get("listing_summary_cache", {})
        except DubizzleAPIError:
            pass

    ordered: list[dict[str, Any]] = []
    seen: set[int] = set()
    priority_ids: list[int] = []
    if preferred_listing_id:
        priority_ids.append(int(preferred_listing_id))
    session_obj = snapshot.get("session") or {}
    if session_obj.get("current_vehicle"):
        priority_ids.append(int(session_obj["current_vehicle"]))
    for fav in snapshot.get("favourites") or []:
        priority_ids.append(int(fav["listing_id"]))
    for lid in session_obj.get("last_result_ids") or []:
        priority_ids.append(int(lid))
    for lid in cache:
        priority_ids.append(int(lid))

    for lid in priority_ids:
        if lid in cache and lid not in seen:
            seen.add(lid)
            ordered.append(cache[lid])
    return ordered


def _render_stepper(step: int) -> None:
    """Render 3-step visual progression: 1 Select Slot -> 2 Review Proposal -> 3 Confirmed."""
    steps = [
        ("1. Select Slot & Contact", 1),
        ("2. Review Proposal", 2),
        ("3. Confirmed", 3),
    ]
    cells: list[str] = []
    for label, idx in steps:
        if idx < step or (step == 3 and idx == 3):
            cls = "dbz-step done"
        elif idx == step:
            cls = "dbz-step active"
        else:
            cls = "dbz-step"
        cells.append(f'<div class="{cls}">{html.escape(label)}</div>')
    st.markdown(f'<div class="dbz-stepper">{"".join(cells)}</div>', unsafe_allow_html=True)


def _validate_booking_contact(name_val: Any, phone_val: Any) -> Optional[str]:
    """Require both buyer name and phone number for booking proposals and confirmations."""
    clean_name = str(name_val or "").strip()
    clean_phone = str(phone_val or "").strip()
    if not clean_name and not clean_phone:
        return "Your name and phone number (e.g. +971501234567) are both required."
    if not clean_name:
        return "Your name is required before scheduling a viewing."
    if not clean_phone:
        return "Phone number (e.g. +971501234567) is required before scheduling a viewing."
    return None


def _sync_slot_widgets(target_date_str: str, target_time_str: str) -> None:
    """Queue date/time sync so form controls match selected alternative slots on rerun."""
    parsed_date: Optional[date] = None
    try:
        parsed_date = date.fromisoformat(target_date_str)
        st.session_state["booking_date_override"] = parsed_date
    except ValueError:
        pass
    st.session_state["booking_time_override"] = target_time_str
    st.session_state["_booking_pending_sync"] = (parsed_date, target_time_str)


def _render_booking_status_card(
    *,
    booking: Optional[dict[str, Any]],
    message: str,
    success: Optional[bool],
    alternatives: list[dict[str, Any]],
    summary_cache: dict[int, dict[str, Any]],
    client: DubizzleAPIClient,
    user_id: str,
) -> None:
    if booking and booking.get("status") == "confirmed":
        lid = int(booking["listing_id"])
        veh_title = format_vehicle_label(summary_cache.get(lid, {"listing_id": lid}))
        st.markdown(
            '<div class="dbz-booking-box">'
            '<span class="dbz-pill confirmed">Confirmed Booking</span>'
            f'<div class="dbz-booking-title">Booking #{int(booking["booking_id"])} Confirmed</div>'
            f'<p style="margin:0 0 0.3rem 0;"><strong>Vehicle:</strong> {html.escape(veh_title)}</p>'
            f'<p style="margin:0 0 0.3rem 0;"><strong>Viewing Slot:</strong> {html.escape(str(booking["booking_date"]))} '
            f'{html.escape(str(booking["start_time"]))}–{html.escape(str(booking["end_time"]))} '
            f'({html.escape(str(booking.get("timezone", "Asia/Dubai")))})</p>'
            f'<p style="margin:0 0 0.35rem 0;color:var(--dbz-text-sec);font-size:0.86rem;">'
            f'Contact: {html.escape(str(booking.get("customer_name") or "Not provided"))} · '
            f'{html.escape(mask_phone_display(booking.get("customer_phone"), empty_label="Not provided"))}'
            "</p>"
            '<p style="margin:0;color:var(--dbz-text-sec);font-size:0.84rem;">'
            "What happens next: your appointment is locked in the viewing schedule and synced to your Buyer Hub sidebar."
            "</p></div>",
            unsafe_allow_html=True,
        )
        return

    if success is False:
        st.markdown(
            '<div class="dbz-booking-box">'
            '<span class="dbz-pill rejected">Slot Rejected / Unavailable</span>'
            f'<div class="dbz-booking-title">Requested slot could not be reserved</div>'
            f'<p style="margin:0;">{html.escape(message)}</p>'
            "</div>",
            unsafe_allow_html=True,
        )
        if alternatives:
            st.markdown("**Available nearby slots (Asia/Dubai):**")
            alt_cols = st.columns(len(alternatives), gap="small")
            for idx, alt in enumerate(alternatives):
                if alt_cols[idx].button(
                    alt["display"],
                    key=f"booking_alt_{alt['booking_date']}_{alt['start_time']}_{idx}",
                    use_container_width=True,
                ):
                    target_lid = st.session_state.get("booking_listing_id") or (
                        booking.get("listing_id") if booking else None
                    )
                    if target_lid:
                        eff_name = str(
                            st.session_state.get("_booking_name_draft")
                            or (booking.get("customer_name") if booking else "")
                            or ""
                        ).strip()
                        raw_draft_phone = str(
                            st.session_state.get("_booking_phone_draft") or ""
                        ).strip()
                        stored_alt_phone = str(
                            (booking.get("customer_phone") if booking else "") or ""
                        ).strip()
                        eff_phone = (
                            raw_draft_phone
                            if raw_draft_phone and not is_masked_phone(raw_draft_phone)
                            else ("" if is_masked_phone(stored_alt_phone) else stored_alt_phone)
                        )
                        contact_err = _validate_booking_contact(eff_name, eff_phone)
                        if contact_err:
                            st.session_state["booking_inline_error"] = contact_err
                            st.rerun()
                        try:
                            resp = client.propose_booking(
                                {
                                    "user_id": user_id,
                                    "listing_id": int(target_lid),
                                    "requested_date": alt["booking_date"],
                                    "requested_time": alt["start_time"],
                                    "session_id": st.session_state.get("session_id") or None,
                                    "customer_name": eff_name,
                                    "customer_phone": eff_phone,
                                }
                            )
                            _sync_slot_widgets(alt["booking_date"], alt["start_time"])
                            st.session_state["booking_result"] = resp
                            st.session_state["booking_inline_error"] = None
                            mark_dirty()
                            st.rerun()
                        except DubizzleAPIError as exc:
                            st.session_state["booking_inline_error"] = exc.detail
                            st.rerun()
        return

    if booking and booking.get("status") == "proposed":
        bid = int(booking["booking_id"])
        lid = int(booking["listing_id"])
        veh_title = format_vehicle_label(summary_cache.get(lid, {"listing_id": lid}))
        st.markdown(
            '<div class="dbz-booking-box">'
            '<span class="dbz-pill proposed">Proposed — Not Reserved Yet</span>'
            f'<div class="dbz-booking-title">Review Proposed Viewing (#{bid})</div>'
            f'<p style="margin:0 0 0.3rem 0;"><strong>Vehicle:</strong> {html.escape(veh_title)}</p>'
            f'<p style="margin:0 0 0.3rem 0;"><strong>Proposed Slot:</strong> {html.escape(str(booking["booking_date"]))} '
            f'{html.escape(str(booking["start_time"]))}–{html.escape(str(booking["end_time"]))} '
            f'({html.escape(str(booking.get("timezone", "Asia/Dubai")))})</p>'
            '<p style="margin:0;color:var(--dbz-text-sec);font-size:0.85rem;">'
            "This draft slot is NOT reserved or held until you check the confirmation box and click Confirm Viewing below."
            "</p></div>",
            unsafe_allow_html=True,
        )

        agreed = st.checkbox(
            "I confirm this vehicle, date, and viewing time (Asia/Dubai).",
            key=f"confirm_chk_{bid}",
        )
        c1, c2 = st.columns([1.4, 1], gap="small")
        if c1.button(
            "Confirm Viewing",
            key=f"confirm_btn_{bid}",
            type="primary",
            use_container_width=True,
            disabled=not agreed,
        ):
            eff_name = str(
                st.session_state.get("_booking_name_draft")
                or booking.get("customer_name")
                or ""
            ).strip()
            raw_draft_phone = str(st.session_state.get("_booking_phone_draft") or "").strip()
            stored_bk_phone = str(booking.get("customer_phone") or "").strip()
            eff_phone = (
                raw_draft_phone
                if raw_draft_phone and not is_masked_phone(raw_draft_phone)
                else ""
            )
            contact_err = _validate_booking_contact(
                eff_name,
                eff_phone or stored_bk_phone,
            )
            if contact_err:
                st.session_state["booking_inline_error"] = contact_err
                st.rerun()
            try:
                confirm_payload: dict[str, Any] = {
                    "user_id": user_id,
                    "confirm": True,
                    "confirmation_text": "Yes, confirm my viewing",
                    "customer_name": eff_name,
                }
                if eff_phone:
                    confirm_payload["customer_phone"] = eff_phone
                conf = client.confirm_booking(bid, confirm_payload)
                st.session_state["booking_result"] = conf
                st.session_state["booking_inline_error"] = None
                mark_dirty()
                st.rerun()
            except DubizzleAPIError as exc:
                st.session_state["booking_inline_error"] = exc.detail
                st.rerun()

        if c2.button(
            "Dismiss Draft Review",
            key=f"dismiss_draft_{bid}",
            use_container_width=True,
        ):
            dismissed: set[int] = set(st.session_state.get("dismissed_booking_ids") or set())
            dismissed.add(bid)
            st.session_state["dismissed_booking_ids"] = dismissed
            st.session_state["booking_result"] = None
            st.session_state["booking_inline_error"] = None
            st.rerun()


def _resolve_active_booking_display(
    snapshot: dict[str, Any],
) -> tuple[Optional[dict[str, Any]], str, Optional[bool], list[dict[str, Any]]]:
    result = st.session_state.get("booking_result")
    if result is not None:
        return (
            result.get("booking"),
            str(result.get("message") or ""),
            result.get("success"),
            result.get("alternatives") or [],
        )
    dismissed: set[int] = set(st.session_state.get("dismissed_booking_ids") or set())
    pending = [
        b
        for b in (snapshot.get("bookings") or [])
        if b.get("status") == "proposed" and int(b["booking_id"]) not in dismissed
    ]
    if pending:
        return pending[0], "", True, []
    return None, "", None, []


@st.dialog("Schedule a Vehicle Viewing", width="large", on_dismiss=close_dialog)
def render_booking_dialog(
    *,
    client: DubizzleAPIClient,
    snapshot: dict[str, Any],
) -> None:
    """Render two-step Dubai viewing booking modal (proposal -> explicit confirmation)."""
    user_obj = snapshot.get("user") or {}
    user_id = str(user_obj.get("user_id") or st.session_state["user_id"])
    session_id = str(
        (snapshot.get("session") or {}).get("session_id")
        or st.session_state.get("session_id")
        or ""
    )
    summary_cache: dict[int, dict[str, Any]] = st.session_state.get("listing_summary_cache", {})

    preferred_lid: Optional[int] = st.session_state.get("booking_listing_id")
    options = _vehicle_options_from_api(client, snapshot, preferred_lid)

    active_bk, active_msg, active_ok, active_alts = _resolve_active_booking_display(snapshot)
    if active_bk and active_bk.get("status") == "confirmed":
        step = 3
    elif active_bk and active_bk.get("status") == "proposed":
        step = 2
    else:
        step = 1

    _render_stepper(step)

    st.caption(
        "Operating hours: Monday–Saturday, 08:00–20:00 Asia/Dubai (60-minute slots; latest start 19:00; minimum 2-hour notice)."
    )

    if st.session_state.get("booking_inline_error"):
        st.error(str(st.session_state["booking_inline_error"]))

    if not options:
        st.info("No vehicles loaded yet. Run a search first or check backend inventory.")
        return

    option_ids = [int(item["listing_id"]) for item in options]
    labels_by_id = {int(item["listing_id"]): format_vehicle_label(item) for item in options}
    default_idx = option_ids.index(preferred_lid) if preferred_lid in option_ids else 0

    selected_lid = st.selectbox(
        "Vehicle",
        options=option_ids,
        index=default_idx,
        format_func=lambda lid: labels_by_id.get(lid, f"Listing #{lid}"),
        key="booking_vehicle_select",
    )
    st.session_state["booking_listing_id"] = int(selected_lid)

    pending_sync = st.session_state.pop("_booking_pending_sync", None)
    if pending_sync is not None:
        sync_date, sync_time = pending_sync
        if sync_date is not None:
            st.session_state["booking_date_picker"] = sync_date
        if sync_time in _TIME_OPTIONS:
            st.session_state["booking_time_select"] = sync_time
            st.session_state.pop("booking_custom_time_input", None)
        else:
            st.session_state["booking_time_select"] = "Custom time..."
            st.session_state["booking_custom_time_input"] = sync_time

    col_d, col_t = st.columns(2, gap="medium")
    default_date = st.session_state.get("booking_date_override") or _default_dubai_tomorrow()
    if "booking_date_picker" not in st.session_state:
        st.session_state["booking_date_picker"] = default_date
    picked_date = col_d.date_input(
        "Preferred date (Asia/Dubai)",
        key="booking_date_picker",
    )

    default_time = st.session_state.get("booking_time_override") or "10:00"
    if "booking_time_select" not in st.session_state:
        st.session_state["booking_time_select"] = (
            default_time if default_time in _TIME_OPTIONS else "Custom time..."
        )

    picked_time_opt = col_t.selectbox(
        "Preferred start time",
        options=_TIME_OPTIONS,
        key="booking_time_select",
    )
    if picked_time_opt == "Custom time...":
        custom_default = (
            default_time if default_time not in _TIME_OPTIONS and default_time != "Custom time..." else "10:00"
        )
        if "booking_custom_time_input" not in st.session_state:
            st.session_state["booking_custom_time_input"] = custom_default
        custom_time = st.text_input(
            "Enter exact time (e.g. 14:30 or 2:00 PM)",
            key="booking_custom_time_input",
        )
        requested_time = custom_time.strip()
    else:
        requested_time = picked_time_opt

    if "_booking_name_draft" not in st.session_state:
        st.session_state["_booking_name_draft"] = str(
            user_obj.get("name") or (active_bk or {}).get("customer_name") or ""
        )
    existing_bk_phone = str((active_bk or {}).get("customer_phone") or "").strip()
    if "_booking_phone_draft" not in st.session_state:
        st.session_state["_booking_phone_draft"] = (
            "" if is_masked_phone(existing_bk_phone) else existing_bk_phone
        )

    col_n, col_p = st.columns(2, gap="medium")
    cust_name = col_n.text_input(
        "Your name *",
        placeholder="e.g. Ali Al Mansoori",
        key="_booking_name_draft",
    )
    cust_phone = col_p.text_input(
        "Phone number * (e.g. +971501234567)",
        placeholder=(
            f"Saved: {mask_phone_display(existing_bk_phone)} (leave blank to keep on confirm)"
            if existing_bk_phone
            else "e.g. +971501234567"
        ),
        key="_booking_phone_draft",
    )

    b_prop, b_cls = st.columns([2, 1], gap="small")
    if b_prop.button(
        "Propose Viewing Slot",
        key="propose_booking_btn",
        type="primary",
        use_container_width=True,
    ):
        contact_err = _validate_booking_contact(cust_name, cust_phone)
        if not requested_time:
            st.session_state["booking_inline_error"] = "Please enter an exact viewing time."
            st.rerun()
        elif contact_err:
            st.session_state["booking_inline_error"] = contact_err
            st.rerun()
        else:
            try:
                resp = client.propose_booking(
                    {
                        "user_id": user_id,
                        "listing_id": int(selected_lid),
                        "requested_date": picked_date.isoformat(),
                        "requested_time": requested_time,
                        "session_id": session_id or None,
                        "customer_name": cust_name.strip(),
                        "customer_phone": cust_phone.strip(),
                    }
                )
                st.session_state["booking_result"] = resp
                st.session_state["booking_date_override"] = None
                st.session_state["booking_time_override"] = None
                st.session_state["booking_inline_error"] = None
                if resp.get("booking") and resp["booking"].get("booking_id"):
                    dismissed = set(st.session_state.get("dismissed_booking_ids") or set())
                    dismissed.discard(int(resp["booking"]["booking_id"]))
                    st.session_state["dismissed_booking_ids"] = dismissed
                mark_dirty()
                st.rerun()
            except DubizzleAPIError as exc:
                st.session_state["booking_inline_error"] = exc.detail
                st.rerun()

    if b_cls.button("Close", key="close_booking_panel", use_container_width=True):
        st.session_state["booking_inline_error"] = None
        close_dialog()
        st.rerun()

    if active_bk is not None or active_ok is False:
        _render_booking_status_card(
            booking=active_bk,
            message=active_msg,
            success=active_ok,
            alternatives=active_alts,
            summary_cache=summary_cache,
            client=client,
            user_id=user_id,
        )


render_booking_panel = render_booking_dialog
