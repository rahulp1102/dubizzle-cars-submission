"""Side-by-side vehicle comparison dialog with strict data honesty."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from frontend.api_client import DubizzleAPIClient, DubizzleAPIError
from frontend.components.primitives import (
    format_aed,
    format_trim_name,
    format_vehicle_label,
    format_vehicle_title,
    render_empty_state,
)
from frontend.state import (
    close_dialog,
    get_cached_listing_detail,
    hydrate_listing_summaries,
    open_dialog,
    seed_summary_cache,
    set_compare_listings,
)


def _attr_map(listing: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(a.get("attribute_name")): a
        for a in (listing.get("attributes") or [])
        if a.get("attribute_value") is not None
    }


def _format_cell_attr(
    attrs: dict[str, dict[str, Any]],
    key: str,
    *,
    is_price: bool = False,
    is_monthly: bool = False,
) -> str:
    attr = attrs.get(key)
    if not attr or not str(attr.get("attribute_value") or "").strip():
        return '<span class="dbz-val-missing">Not specified</span>'

    val_str = str(attr["attribute_value"]).strip()
    status = str(attr.get("status") or "confirmed").lower()

    if is_price:
        num = attr.get("numeric_value")
        if status == "confirmed" and num is not None:
            return f'<span class="dbz-val-price">{html.escape(format_aed(num))}</span>'
        return f'<span class="dbz-pill warm">Conflict</span> {html.escape(val_str)}'

    if is_monthly:
        num = attr.get("numeric_value")
        if num is not None:
            return f"{html.escape(format_aed(num))}/mo (instalment only)"
        return html.escape(val_str)

    badge = ""
    if "conflict" in val_str.lower():
        badge = ' <span class="dbz-pill warm">Conflict</span>'
    elif status == "inferred":
        badge = ' <span class="dbz-pill inferred">Inferred</span>'

    return f"{html.escape(val_str)}{badge}"


@st.dialog("Compare Vehicles Side-by-Side", width="large", on_dismiss=close_dialog)
def render_comparison_dialog(
    *,
    client: DubizzleAPIClient,
    snapshot: dict[str, Any],
) -> None:
    """Render structured multi-vehicle comparison matrix with missing-vs-negative clarity."""
    compare_ids: list[int] = [int(x) for x in (st.session_state.get("compare_listing_ids") or [])]

    cache: dict[int, dict[str, Any]] = st.session_state.get("listing_summary_cache", {})
    if len(cache) < 10:
        try:
            res = client.search_inventory(limit=50)
            seed_summary_cache(res.get("results") or [])
            cache = st.session_state.get("listing_summary_cache", {})
        except DubizzleAPIError:
            pass

    if compare_ids:
        try:
            cache = hydrate_listing_summaries(client, compare_ids)
        except DubizzleAPIError:
            pass

    # Add vehicle picker from cached / saved / recent listings
    available_ids = [lid for lid in cache if lid not in compare_ids]
    if available_ids and len(compare_ids) < 4:
        c_sel, c_add = st.columns([3.4, 1], gap="small")
        pick_id = c_sel.selectbox(
            "Add vehicle to compare (up to 4)",
            options=available_ids,
            format_func=lambda lid: format_vehicle_label(cache.get(lid, {"listing_id": lid})),
            key="cmp_add_vehicle_select",
        )
        c_add.markdown('<div style="height:0.9rem;"></div>', unsafe_allow_html=True)
        if c_add.button("Add", key="cmp_add_btn", use_container_width=True):
            set_compare_listings(compare_ids + [int(pick_id)])
            st.rerun()

    favs = snapshot.get("favourites") or []
    last_ids = [int(x) for x in ((snapshot.get("session") or {}).get("last_result_ids") or [])]
    if not compare_ids:
        render_empty_state(
            "No vehicles selected for comparison",
            "Use 'Compare' on any listing card, pick vehicles above, or load your shortlist.",
        )
        c_emp_a, c_emp_b, c_emp_c = st.columns(3, gap="small")
        if len(favs) >= 2:
            if c_emp_a.button(
                f"Load Saved ({min(len(favs), 4)})",
                key="cmp_load_favs_empty",
                type="primary",
                use_container_width=True,
            ):
                set_compare_listings([int(f["listing_id"]) for f in favs[:4]])
                st.rerun()
        if len(last_ids) >= 2:
            if c_emp_b.button(
                f"Load Recent Results ({min(len(last_ids), 4)})",
                key="cmp_load_recent_empty",
                use_container_width=True,
            ):
                set_compare_listings(last_ids[:4])
                st.rerun()
        if c_emp_c.button("Close", key="cmp_empty_close_btn", use_container_width=True):
            close_dialog()
            st.rerun()
        return

    if len(compare_ids) == 1:
        st.info("Add at least one more vehicle above to compare specifications side-by-side.")

    listings: list[dict[str, Any]] = []
    attr_maps: dict[int, dict[str, dict[str, Any]]] = {}
    for lid in compare_ids:
        try:
            full = get_cached_listing_detail(client, lid)
            listings.append(full)
            attr_maps[lid] = _attr_map(full)
        except DubizzleAPIError:
            summary_fallback = cache.get(lid)
            if summary_fallback:
                listings.append(summary_fallback)
                attr_maps[lid] = {}

    if not listings:
        st.error("Could not load details for the selected vehicles.")
        return

    # Quick manage row per compared vehicle
    st.markdown("**Selected Vehicles**")
    rm_cols = st.columns(len(listings), gap="small")
    for idx, item in enumerate(listings):
        lid = int(item["listing_id"])
        lbl = format_vehicle_title(item)
        with rm_cols[idx]:
            st.caption(f"#{lid} · {lbl}")
            b_det, b_rm = st.columns(2, gap="small")
            if b_det.button("Book", key=f"cmp_book_{lid}", use_container_width=True):
                open_dialog("booking", listing_id=lid)
                st.rerun()
            if b_rm.button("Remove", key=f"cmp_rm_{lid}", use_container_width=True):
                set_compare_listings([x for x in compare_ids if x != lid])
                st.rerun()

    # Build comparison matrix HTML
    header_cells = ["<th>Specification</th>"]
    for item in listings:
        lid = int(item["listing_id"])
        title = html.escape(format_vehicle_title(item))
        trim = format_trim_name(item.get("trim"))
        trim_line = f"<br/><small style='font-weight:500;color:var(--dbz-text-sec);'>{html.escape(trim)}</small>" if trim else ""
        header_cells.append(f"<th>#{lid} · {title}{trim_line}</th>")

    rows_spec: list[tuple[str, list[str]]] = []

    rows_spec.append(
        (
            "Cash Price",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "cash_price", is_price=True) for it in listings],
        )
    )
    rows_spec.append(
        (
            "Monthly Instalment",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "monthly_instalment", is_monthly=True) for it in listings],
        )
    )
    rows_spec.append(
        (
            "Down Payment",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "down_payment") for it in listings],
        )
    )
    rows_spec.append(
        ("Model Year", [html.escape(str(it.get("year") or "—")) for it in listings])
    )
    rows_spec.append(
        (
            "Make & Model",
            [html.escape(format_vehicle_title(it)) for it in listings],
        )
    )
    rows_spec.append(
        (
            "Mileage",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "mileage_km") for it in listings],
        )
    )
    rows_spec.append(
        (
            "Regional Spec",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "spec_region") for it in listings],
        )
    )
    rows_spec.append(
        (
            "Body Type",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "body_type") for it in listings],
        )
    )
    rows_spec.append(
        (
            "Transmission",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "transmission") for it in listings],
        )
    )
    rows_spec.append(
        (
            "Engine",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "engine") for it in listings],
        )
    )
    rows_spec.append(
        (
            "Warranty",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "warranty") for it in listings],
        )
    )
    rows_spec.append(
        (
            "Exterior Colour",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "colour") for it in listings],
        )
    )
    rows_spec.append(
        (
            "Service History",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "service_history") for it in listings],
        )
    )
    rows_spec.append(
        (
            "Accident-Free",
            [_format_cell_attr(attr_maps[int(it["listing_id"])], "accident_free") for it in listings],
        )
    )
    rows_spec.append(
        (
            "Duplicate Check",
            [
                (
                    f'<span class="dbz-pill warm">Ad group #{int(it["duplicate_group"])}</span>'
                    if it.get("duplicate_group") is not None
                    else '<span class="dbz-pill neutral">Unique ad text</span>'
                )
                for it in listings
            ],
        )
    )

    body_rows_html: list[str] = []
    for label, vals in rows_spec:
        tds = "".join(f"<td>{v}</td>" for v in vals)
        body_rows_html.append(f"<tr><th>{html.escape(label)}</th>{tds}</tr>")

    table_html = (
        '<div class="dbz-compare-scroll">'
        '<table class="dbz-compare-table">'
        f'<thead><tr>{"".join(header_cells)}</tr></thead>'
        f'<tbody>{"".join(body_rows_html)}</tbody>'
        "</table>"
        "</div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)
    st.caption(
        "Values come exclusively from verified inventory extraction. 'Not specified' indicates missing ad details—not a negative feature."
    )

    c_clr, c_done = st.columns(2, gap="small")
    if c_clr.button("Clear comparison", key="cmp_clear_all", use_container_width=True):
        set_compare_listings([])
        st.rerun()
    if c_done.button("Close", key="cmp_close_btn", type="primary", use_container_width=True):
        close_dialog()
        st.rerun()
