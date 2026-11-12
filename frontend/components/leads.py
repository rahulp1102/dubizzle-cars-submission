"""Buyer lead capture and personal qualification status dialog."""

from __future__ import annotations

import html
from typing import Any, Optional

import streamlit as st

from frontend.api_client import DubizzleAPIClient, DubizzleAPIError
from frontend.components.primitives import (
    format_aed,
    format_make_name,
    is_masked_phone,
    mask_phone_display,
    render_empty_state,
)
from frontend.state import close_dialog, mark_dirty

_BODY_OPTIONS = [
    "",
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

_TIMELINE_OPTIONS = [
    "",
    "This week",
    "This month",
    "1-3 months",
    "Just exploring",
]

_FINANCING_OPTIONS = [
    "",
    "Cash",
    "Bank financing",
    "Open to cash or financing",
]


def _render_lead_summary_card(lead: dict[str, Any], next_question: Optional[str]) -> None:
    status = str(lead.get("status") or "cold").lower()
    budget = lead.get("budget")
    orig_b = lead.get("original_budget")
    orig_c = str(lead.get("original_currency") or "AED")
    if budget is not None:
        if orig_c == "USD" and orig_b is not None:
            budget_txt = f"USD {float(orig_b):,.0f} (~{format_aed(budget)})"
        else:
            budget_txt = format_aed(budget)
    else:
        budget_txt = "Not specified"

    makes = ", ".join(format_make_name(m) for m in (lead.get("preferred_makes") or [])) or "Any"
    lids = lead.get("listing_ids") or []
    veh_txt = ", ".join(f"#{int(x)}" for x in lids) if lids else "None selected"

    st.markdown(
        '<div class="dbz-booking-box" style="margin-top:0.25rem;">'
        f'<span class="dbz-pill {status}">{html.escape(status.upper())} LEAD</span>'
        '<div class="dbz-booking-title">Your Buyer Qualification Summary</div>'
        f'<p style="margin:0 0 0.25rem 0;"><strong>Contact:</strong> {html.escape(str(lead.get("name") or "Not provided"))} · '
        f'{html.escape(mask_phone_display(lead.get("phone")))}</p>'
        f'<p style="margin:0 0 0.25rem 0;"><strong>Target Budget:</strong> {html.escape(budget_txt)} · '
        f'<strong>Makes:</strong> {html.escape(makes)}</p>'
        f'<p style="margin:0;color:var(--dbz-text-sec);font-size:0.84rem;">'
        f'Timeline: {html.escape(str(lead.get("purchase_timeline") or "Not specified"))} · '
        f'Payment: {html.escape(str(lead.get("financing_interest") or "Not specified"))} · '
        f'Shortlisted IDs: {html.escape(veh_txt)}'
        "</p></div>",
        unsafe_allow_html=True,
    )
    if next_question:
        st.info(f"Suggested next detail to complete your profile: {next_question}")


@st.dialog("My Buyer Enquiry & Qualification Status", width="large", on_dismiss=close_dialog)
def render_leads_dialog(
    *,
    client: DubizzleAPIClient,
    snapshot: dict[str, Any],
) -> None:
    """Render the current buyer's enquiry form and qualification status."""
    user_obj = snapshot.get("user") or {}
    user_id = str(user_obj.get("user_id") or st.session_state["user_id"])
    fav_ids = [int(f["listing_id"]) for f in (snapshot.get("favourites") or [])]

    lead_detail = st.session_state.get("lead_status_cache")
    if lead_detail is None:
        try:
            lead_detail = client.get_lead(user_id)
            st.session_state["lead_status_cache"] = lead_detail
        except DubizzleAPIError:
            lead_detail = None

    existing_lead = lead_detail.get("lead") if isinstance(lead_detail, dict) else None
    next_q = lead_detail.get("next_question") if isinstance(lead_detail, dict) else None

    if st.session_state.get("lead_save_success"):
        st.success(str(st.session_state.pop("lead_save_success")))
    if st.session_state.get("lead_inline_error"):
        st.error(str(st.session_state["lead_inline_error"]))

    if existing_lead:
        _render_lead_summary_card(existing_lead, next_q)
    else:
        render_empty_state(
            "No buyer enquiry submitted yet",
            "Share your target budget, preferred car type, and contact details below so a specialist can prepare relevant options.",
        )

    with st.form(f"lead_capture_form_{user_id}", clear_on_submit=False):
        c1, c2 = st.columns(2, gap="medium")
        name_val = c1.text_input(
            "Full name",
            value=str((existing_lead or {}).get("name") or user_obj.get("name") or ""),
            placeholder="e.g. Sara Al Mansoori",
        )
        existing_lead_phone = str((existing_lead or {}).get("phone") or "").strip()
        phone_val = c2.text_input(
            "Phone number (UAE or E.164)",
            value="" if is_masked_phone(existing_lead_phone) else existing_lead_phone,
            placeholder=(
                f"Saved: {mask_phone_display(existing_lead_phone)} (leave blank to keep)"
                if existing_lead_phone
                else "e.g. +971501234567 or 0501234567"
            ),
        )

        c3, c4 = st.columns([2, 1], gap="medium")
        existing_orig_b = (existing_lead or {}).get("original_budget") or (
            existing_lead or {}
        ).get("budget")
        budget_default = int(round(float(existing_orig_b))) if existing_orig_b else 0
        budget_val = c3.number_input(
            "Target max budget (0 = not set)",
            min_value=0,
            value=budget_default,
            step=5000,
        )
        existing_curr = str((existing_lead or {}).get("original_currency") or "AED")
        curr_idx = 1 if existing_curr == "USD" else 0
        currency_val = c4.selectbox("Currency", options=["AED", "USD"], index=curr_idx)

        c5, c6 = st.columns(2, gap="medium")
        makes_default = ", ".join((existing_lead or {}).get("preferred_makes") or [])
        makes_val = c5.text_input(
            "Preferred makes (comma-separated)",
            value=makes_default,
            placeholder="e.g. Mercedes-Benz, Toyota",
        )
        existing_bt = str((existing_lead or {}).get("preferred_body_type") or "")
        bt_idx = _BODY_OPTIONS.index(existing_bt) if existing_bt in _BODY_OPTIONS else 0
        body_val = c6.selectbox(
            "Preferred body type",
            options=_BODY_OPTIONS,
            index=bt_idx,
            format_func=lambda x: "Not specified" if not x else x,
        )

        c7, c8 = st.columns(2, gap="medium")
        existing_tl = str((existing_lead or {}).get("purchase_timeline") or "")
        tl_idx = _TIMELINE_OPTIONS.index(existing_tl) if existing_tl in _TIMELINE_OPTIONS else 0
        timeline_val = c7.selectbox(
            "Purchase timeline",
            options=_TIMELINE_OPTIONS,
            index=tl_idx,
            format_func=lambda x: "Not specified" if not x else x,
        )
        existing_fin = str((existing_lead or {}).get("financing_interest") or "")
        fin_idx = _FINANCING_OPTIONS.index(existing_fin) if existing_fin in _FINANCING_OPTIONS else 0
        financing_val = c8.selectbox(
            "Payment / financing preference",
            options=_FINANCING_OPTIONS,
            index=fin_idx,
            format_func=lambda x: "Not specified" if not x else x,
        )

        submit_lead = st.form_submit_button(
            "Save Buyer Enquiry",
            type="primary",
            use_container_width=True,
        )

    if submit_lead:
        has_any_input = any(
            [
                name_val.strip(),
                phone_val.strip(),
                existing_lead_phone,
                budget_val > 0,
                makes_val.strip(),
                body_val,
                timeline_val,
                financing_val,
                bool(fav_ids),
            ]
        )
        if not has_any_input:
            st.session_state["lead_inline_error"] = (
                "Please provide at least one detail (such as budget, phone, or preferred make) before saving."
            )
            st.rerun()
        else:
            payload: dict[str, Any] = {
                "user_id": user_id,
                "currency": currency_val,
            }
            if name_val.strip():
                payload["name"] = name_val.strip()
            if phone_val.strip() and not is_masked_phone(phone_val):
                payload["phone"] = phone_val.strip()
            if budget_val > 0:
                payload["budget"] = float(budget_val)
            if makes_val.strip():
                payload["preferred_makes"] = [
                    m.strip() for m in makes_val.split(",") if m.strip()
                ]
            if body_val:
                payload["preferred_body_type"] = body_val
            if timeline_val:
                payload["purchase_timeline"] = timeline_val
            if financing_val:
                payload["financing_interest"] = financing_val
            if fav_ids:
                payload["listing_ids"] = fav_ids

            try:
                saved_detail = client.upsert_lead(user_id, payload)
                st.session_state["lead_status_cache"] = saved_detail
                st.session_state["lead_inline_error"] = None
                new_status = str(saved_detail.get("lead", {}).get("status", "cold")).upper()
                st.session_state["lead_save_success"] = (
                    f"Buyer enquiry saved (Qualification status: {new_status})."
                )
                mark_dirty()
                st.rerun()
            except DubizzleAPIError as exc:
                st.session_state["lead_inline_error"] = exc.detail
                st.rerun()

    if st.button("Close", key="close_leads_dialog_btn", use_container_width=True):
        st.session_state["lead_inline_error"] = None
        close_dialog()
        st.rerun()
