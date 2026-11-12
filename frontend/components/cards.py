"""Database-backed vehicle card grid, horizontal showcase, and Vehicle Details dialog."""

from __future__ import annotations

import html
import re
from typing import Any

import streamlit as st

from frontend.api_client import DubizzleAPIClient, DubizzleAPIError
from frontend.components.primitives import (
    PLACEHOLDER_IMAGE_URI,
    build_placeholder_image_uri,
    format_aed,
    format_attribute_label,
    format_make_name,
    format_rtl_safe_html,
    format_trim_name,
    format_vehicle_label,
    format_vehicle_title,
    safe_photo_src,
)
from frontend.state import (
    close_dialog,
    get_cached_listing_detail,
    hydrate_listing_summaries,
    mark_dirty,
    open_dialog,
    toggle_compare_listing,
)

__all__ = [
    "PLACEHOLDER_IMAGE_URI",
    "_render_card_html",
    "format_vehicle_label",
    "render_vehicle_cards_grid",
    "render_vehicle_details_dialog",
]

_DETAIL_ATTRIBUTE_ORDER: list[str] = [
    "cash_price",
    "monthly_instalment",
    "down_payment",
    "mileage_km",
    "spec_region",
    "body_type",
    "transmission",
    "colour",
    "warranty",
    "engine",
    "service_history",
    "accident_free",
]


def _format_match_reason(raw_reason: str) -> str:
    """Format backend match_reason segments cleanly without raw lowercase slugs."""
    text = raw_reason.strip()
    if not text:
        return ""

    def _replace_make(match: re.Match[str]) -> str:
        return f"Make: {format_make_name(match.group(1))}"

    return re.sub(r"Make:\s*([a-z0-9\- ]+?)(?=\s*\||$)", _replace_make, text)


def _render_card_html(
    item: dict[str, Any],
    *,
    theme: str = "light",
    horizontal: bool = False,
) -> str:
    """Render strictly DB-backed HTML card with epistemic status indicators."""
    lid = int(item["listing_id"])
    title = format_vehicle_title(item)
    trim_fmt = format_trim_name(item.get("trim"))
    trim_display = html.escape(trim_fmt) if trim_fmt else "&nbsp;"

    cash_price = item.get("cash_price")
    cash_status = item.get("cash_price_status")
    monthly = item.get("monthly_instalment")

    if cash_price is not None and cash_status == "confirmed":
        price_html = f'<div class="dbz-card-price">AED {int(round(float(cash_price))):,}</div>'
    elif cash_price is not None and cash_status == "inferred":
        price_html = '<div class="dbz-card-price conflict">Cash price conflict — check details</div>'
    else:
        price_html = '<div class="dbz-card-price unlisted">Price not listed</div>'

    if monthly is not None:
        monthly_html = (
            '<div class="dbz-card-monthly">'
            f"Est. AED {int(round(float(monthly))):,}/mo (instalment only)"
            "</div>"
        )
    else:
        monthly_html = '<div class="dbz-card-monthly">&nbsp;</div>'

    chips: list[str] = []
    mileage_km = item.get("mileage_km")
    mileage_status = item.get("mileage_km_status")
    if mileage_km is not None:
        km_str = f"{int(round(float(mileage_km))):,} km"
        if mileage_status == "inferred":
            chips.append(f'<span class="dbz-chip warn">{km_str} (conflict)</span>')
        else:
            chips.append(f'<span class="dbz-chip">{km_str}</span>')
    else:
        chips.append('<span class="dbz-chip muted">Mileage not listed</span>')

    if item.get("spec_region"):
        chips.append(f'<span class="dbz-chip">{html.escape(str(item["spec_region"]))} Spec</span>')
    if item.get("transmission"):
        chips.append(f'<span class="dbz-chip">{html.escape(str(item["transmission"]))}</span>')
    if item.get("body_type"):
        bt = html.escape(str(item["body_type"]))
        suffix = " (inferred)" if item.get("body_type_status") == "inferred" else ""
        chips.append(f'<span class="dbz-chip">{bt}{suffix}</span>')
    if item.get("colour"):
        chips.append(f'<span class="dbz-chip">{html.escape(str(item["colour"]))}</span>')

    if item.get("warranty"):
        w_val = str(item["warranty"]).strip().lower()
        w_status = item.get("warranty_status")
        if w_val.startswith("yes") and w_status == "confirmed":
            chips.append('<span class="dbz-chip ok">Warranty confirmed</span>')
        elif w_val == "no" and w_status == "confirmed":
            chips.append('<span class="dbz-chip">No warranty</span>')
        elif w_status == "inferred":
            chips.append('<span class="dbz-chip warn">Warranty optional (inferred)</span>')

    if item.get("is_suspected_duplicate"):
        grp = item.get("duplicate_group")
        grp_lbl = f" #{grp}" if grp is not None else ""
        chips.append(f'<span class="dbz-chip warn">Near-duplicate group{grp_lbl}</span>')

    chips_html = f'<div class="dbz-chips">{"".join(chips)}</div>' if chips else ""

    match_reason = _format_match_reason(str(item.get("match_reason") or ""))
    reason_html = (
        f'<div class="dbz-match-reason">{html.escape(match_reason)}</div>'
        if match_reason
        else ""
    )

    photo_src = html.escape(safe_photo_src(item.get("photo_url"), theme), quote=True)
    fallback_src = html.escape(build_placeholder_image_uri(theme), quote=True)
    alt_text = html.escape(title, quote=True)
    lang = str(item.get("language") or "en").lower()
    lang_badge = (
        '<span class="dbz-card-lang-badge">'
        f"{'Arabic' if lang == 'ar' else 'EN / AR'}</span>"
        if lang in {"ar", "mixed"}
        else ""
    )
    card_cls = "dbz-card dbz-card-horizontal" if horizontal else "dbz-card"

    return (
        f'<div class="{card_cls}">'
        '<div class="dbz-card-img-wrap">'
        f'<img src="{photo_src}" alt="{alt_text}" loading="lazy" '
        f'onerror="this.onerror=null;this.src=\'{fallback_src}\';"/>'
        f'<span class="dbz-card-id-badge">ID #{lid}</span>'
        f"{lang_badge}"
        "</div>"
        '<div class="dbz-card-body">'
        f'<div class="dbz-card-title">{html.escape(title)}</div>'
        f'<div class="dbz-card-trim">{trim_display}</div>'
        '<div class="dbz-card-price-row">'
        f"{price_html}"
        "</div>"
        f"{monthly_html}"
        f"{chips_html}"
        f"{reason_html}"
        "</div>"
        "</div>"
    )


def _render_card_action_rows(
    *,
    lid: int,
    is_saved: bool,
    is_comparing: bool,
    client: DubizzleAPIClient,
    user_id: str,
    session_id: str,
    key_prefix: str,
    suffix: str,
) -> None:
    """Render responsive 2x3 action rows with zero label clipping across all viewports."""
    st.markdown('<span class="dbz-keep-2col"></span>', unsafe_allow_html=True)
    r1_a, r1_b = st.columns(2, gap="small")
    if r1_a.button(
        "Details",
        key=f"{key_prefix}_details_{lid}_{suffix}",
        use_container_width=True,
    ):
        open_dialog("details", listing_id=lid)
        st.rerun()

    if r1_b.button(
        "Book viewing",
        key=f"{key_prefix}_book_{lid}_{suffix}",
        use_container_width=True,
    ):
        try:
            client.focus_vehicle(session_id, user_id, lid)
        except DubizzleAPIError:
            pass
        open_dialog("booking", listing_id=lid)
        mark_dirty()
        st.rerun()

    st.markdown('<span class="dbz-keep-3col"></span>', unsafe_allow_html=True)
    r2_a, r2_b, r2_c = st.columns(3, gap="small")
    save_label = "Saved" if is_saved else "Save"
    if r2_a.button(
        save_label,
        key=f"{key_prefix}_save_{lid}_{suffix}",
        use_container_width=True,
        type="primary" if is_saved else "secondary",
    ):
        try:
            if is_saved:
                client.remove_favourite(user_id, lid)
            else:
                client.add_favourite(user_id, lid)
            mark_dirty()
            st.rerun()
        except DubizzleAPIError as exc:
            st.session_state["api_error"] = exc.detail
            st.rerun()

    if r2_b.button(
        "Ask AI",
        key=f"{key_prefix}_ask_{lid}_{suffix}",
        use_container_width=True,
    ):
        try:
            client.focus_vehicle(session_id, user_id, lid)
            mark_dirty()
            st.rerun()
        except DubizzleAPIError as exc:
            st.session_state["api_error"] = exc.detail
            st.rerun()

    cmp_label = "Added ✓" if is_comparing else "Compare"
    if r2_c.button(
        cmp_label,
        key=f"{key_prefix}_cmp_{lid}_{suffix}",
        use_container_width=True,
    ):
        toggle_compare_listing(lid)
        st.rerun()


def render_vehicle_cards_grid(
    cards: list[dict[str, Any]],
    *,
    client: DubizzleAPIClient,
    user_id: str,
    session_id: str,
    favourite_ids: set[int],
    key_prefix: str,
) -> None:
    """Render adaptive vehicle cards (horizontal showcase for 1 card, 2-col responsive grid for 2+)."""
    if not cards:
        return

    theme = str(st.session_state.get("theme", "light"))
    compare_ids = set(int(x) for x in (st.session_state.get("compare_listing_ids") or []))

    if len(cards) == 1:
        item = cards[0]
        lid = int(item["listing_id"])
        st.markdown(_render_card_html(item, theme=theme, horizontal=True), unsafe_allow_html=True)
        _render_card_action_rows(
            lid=lid,
            is_saved=lid in favourite_ids,
            is_comparing=lid in compare_ids,
            client=client,
            user_id=user_id,
            session_id=session_id,
            key_prefix=key_prefix,
            suffix="single_0",
        )
        return

    for row_start in range(0, len(cards), 2):
        row_items = cards[row_start : row_start + 2]
        cols = st.columns(2, gap="medium")
        for idx, item in enumerate(row_items):
            lid = int(item["listing_id"])
            with cols[idx]:
                st.markdown(_render_card_html(item, theme=theme), unsafe_allow_html=True)
                _render_card_action_rows(
                    lid=lid,
                    is_saved=lid in favourite_ids,
                    is_comparing=lid in compare_ids,
                    client=client,
                    user_id=user_id,
                    session_id=session_id,
                    key_prefix=key_prefix,
                    suffix=f"{row_start}_{idx}",
                )


def _render_spec_grid_html(attrs: list[dict[str, Any]]) -> str:
    by_name = {str(a.get("attribute_name")): a for a in attrs}
    cells: list[str] = []

    for attr_name in _DETAIL_ATTRIBUTE_ORDER:
        label = format_attribute_label(attr_name)
        attr = by_name.get(attr_name)
        val = attr.get("attribute_value") if attr else None
        if val is None or not str(val).strip():
            cells.append(
                '<div class="dbz-spec-cell">'
                f'<div class="dbz-spec-k"><span>{html.escape(label)}</span>'
                '<span class="dbz-pill neutral">Not listed</span></div>'
                '<div class="dbz-spec-v missing">Not specified in the listing</div>'
                "</div>"
            )
            continue

        status = str(attr.get("status") or "confirmed").lower()
        val_str = str(val).strip()
        is_conflict = "conflict" in val_str.lower()
        if is_conflict:
            pill_cls, pill_txt = "proposed", "Conflict"
        elif status == "inferred":
            pill_cls, pill_txt = "inferred", "Inferred"
        else:
            pill_cls, pill_txt = "confirmed", "Confirmed"

        evidence = str(attr.get("evidence") or "").strip()
        ev_html = (
            f'<div class="dbz-spec-ev">Evidence: "{html.escape(evidence)}"</div>'
            if evidence
            else ""
        )
        cells.append(
            '<div class="dbz-spec-cell">'
            f'<div class="dbz-spec-k"><span>{html.escape(label)}</span>'
            f'<span class="dbz-pill {pill_cls}">{pill_txt}</span></div>'
            f'<div class="dbz-spec-v">{html.escape(val_str)}</div>'
            f"{ev_html}"
            "</div>"
        )

    return f'<div class="dbz-spec-grid">{"".join(cells)}</div>'


@st.dialog("Vehicle Details & Verified Evidence", width="large", on_dismiss=close_dialog)
def render_vehicle_details_dialog(
    *,
    client: DubizzleAPIClient,
    listing_id: int,
    user_id: str,
    session_id: str,
    favourite_ids: set[int],
    summary_cache: dict[int, dict[str, Any]],
) -> None:
    """Render full structured listing inspection modal without displacing the chat timeline."""
    theme = str(st.session_state.get("theme", "light"))
    try:
        listing = get_cached_listing_detail(client, listing_id)
    except DubizzleAPIError as exc:
        st.error(f"Could not load listing #{listing_id} details: {exc.detail}")
        if st.button("Close", key="dlg_detail_err_close"):
            close_dialog()
            st.rerun()
        return

    if listing_id not in summary_cache:
        try:
            summary_cache = hydrate_listing_summaries(client, [listing_id])
        except DubizzleAPIError:
            pass
    summary = summary_cache.get(listing_id, {})
    title = format_vehicle_title(listing)
    trim = format_trim_name(listing.get("trim"))
    lang = str(listing.get("language") or "en").lower()
    lang_label = {"en": "English", "ar": "Arabic", "mixed": "English & Arabic"}.get(
        lang, lang.upper()
    )

    attrs = listing.get("attributes") or []
    by_name = {str(a.get("attribute_name")): a for a in attrs}
    cash_attr = by_name.get("cash_price")
    monthly_attr = by_name.get("monthly_instalment")
    down_attr = by_name.get("down_payment")

    c_img, c_info = st.columns([1.05, 1.25], gap="medium")
    with c_img:
        photo_src = html.escape(safe_photo_src(listing.get("photo_url"), theme), quote=True)
        fallback_src = html.escape(build_placeholder_image_uri(theme), quote=True)
        st.markdown(
            '<div class="dbz-card-img-wrap" style="border-radius:10px;border:1px solid var(--dbz-border);">'
            f'<img src="{photo_src}" alt="{html.escape(title, quote=True)}" '
            f'onerror="this.onerror=null;this.src=\'{fallback_src}\';"/>'
            f'<span class="dbz-card-id-badge">ID #{listing_id}</span>'
            "</div>",
            unsafe_allow_html=True,
        )

    with c_info:
        st.markdown(f"### {title}")
        if trim:
            st.caption(f"Trim / Variant: **{trim}** · Ad Language: **{lang_label}**")
        else:
            st.caption(f"Listing #{listing_id} · Ad Language: **{lang_label}**")

        if cash_attr and cash_attr.get("numeric_value") is not None:
            if cash_attr.get("status") == "confirmed":
                st.markdown(
                    f'<div class="dbz-card-price" style="font-size:1.35rem;">'
                    f"{format_aed(cash_attr['numeric_value'])}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div class="dbz-card-price conflict">'
                    f"{html.escape(str(cash_attr.get('attribute_value') or 'Cash price conflict'))}</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                '<div class="dbz-card-price unlisted">Cash price not specified in listing</div>',
                unsafe_allow_html=True,
            )

        if monthly_attr and monthly_attr.get("numeric_value") is not None:
            dp_text = (
                f" · Down payment: {down_attr['attribute_value']}"
                if down_attr and down_attr.get("attribute_value")
                else ""
            )
            st.caption(
                f"Est. {format_aed(monthly_attr['numeric_value'])}/mo (instalment only){dp_text}"
            )
        elif down_attr and down_attr.get("attribute_value"):
            st.caption(f"Down payment noted: {down_attr['attribute_value']}")

        is_saved = listing_id in favourite_ids
        compare_ids = list(st.session_state.get("compare_listing_ids") or [])
        is_cmp = listing_id in compare_ids

        a1, a2 = st.columns(2, gap="small")
        if a1.button(
            "Saved ✓" if is_saved else "Save vehicle",
            key=f"dlg_save_{listing_id}",
            type="primary" if is_saved else "secondary",
            use_container_width=True,
        ):
            try:
                if is_saved:
                    client.remove_favourite(user_id, listing_id)
                else:
                    client.add_favourite(user_id, listing_id)
                mark_dirty()
                st.rerun()
            except DubizzleAPIError as exc:
                st.error(exc.detail)

        if a2.button(
            "Book viewing",
            key=f"dlg_book_{listing_id}",
            type="primary",
            use_container_width=True,
        ):
            try:
                client.focus_vehicle(session_id, user_id, listing_id)
            except DubizzleAPIError:
                pass
            open_dialog("booking", listing_id=listing_id)
            mark_dirty()
            st.rerun()

        a3, a4 = st.columns(2, gap="small")
        if a3.button(
            "Remove from Compare" if is_cmp else "Add to Compare",
            key=f"dlg_cmp_{listing_id}",
            use_container_width=True,
        ):
            toggle_compare_listing(listing_id)
            st.rerun()

        if a4.button(
            "Focus in Chat",
            key=f"dlg_focus_{listing_id}",
            use_container_width=True,
        ):
            try:
                client.focus_vehicle(session_id, user_id, listing_id)
                close_dialog()
                mark_dirty()
                st.rerun()
            except DubizzleAPIError as exc:
                st.error(exc.detail)

    dup_group = listing.get("duplicate_group")
    sibling_ids = [int(x) for x in (summary.get("suspected_duplicate_ids") or []) if int(x) != listing_id]
    if dup_group is not None:
        sibling_note = (
            f" (also shares ad text with listings {', '.join(f'#{sid}' for sid in sibling_ids)})"
            if sibling_ids
            else ""
        )
        st.warning(
            f"Near-duplicate listing notice: belongs to ad group #{dup_group}{sibling_note}. "
            "Listing IDs remain separate because dealer ads share template text without VIN confirmation."
        )

    st.markdown("**Verified Specifications & Extraction Provenance**")
    st.markdown(_render_spec_grid_html(attrs), unsafe_allow_html=True)

    st.markdown("**Ask the Assistant About This Listing**")
    q1, q2, q3 = st.columns(3, gap="small")
    if q1.button("Check mileage & history", key=f"dlg_q_km_{listing_id}", use_container_width=True):
        try:
            client.focus_vehicle(session_id, user_id, listing_id)
        except DubizzleAPIError:
            pass
        close_dialog()
        st.session_state["pending_prompt"] = f"What are the mileage and service history details for listing #{listing_id}?"
        mark_dirty()
        st.rerun()
    if q2.button("Check warranty & price", key=f"dlg_q_war_{listing_id}", use_container_width=True):
        try:
            client.focus_vehicle(session_id, user_id, listing_id)
        except DubizzleAPIError:
            pass
        close_dialog()
        st.session_state["pending_prompt"] = f"Does listing #{listing_id} have a confirmed cash price and warranty?"
        mark_dirty()
        st.rerun()
    if q3.button("Close", key=f"dlg_close_det_{listing_id}", use_container_width=True):
        close_dialog()
        st.rerun()

    desc = str(listing.get("description_display") or "").strip()
    if desc:
        with st.expander("Original Seller Description (contact numbers & URLs removed)", expanded=True):
            st.markdown(
                f'<div class="dbz-desc-box" dir="auto">{format_rtl_safe_html(desc)}</div>',
                unsafe_allow_html=True,
            )
