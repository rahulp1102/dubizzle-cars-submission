"""Main Streamlit entrypoint for the dubizzle AI Used-Car Discovery Assistant."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from frontend.api_client import DubizzleAPIClient
from frontend.components.booking import render_booking_dialog
from frontend.components.cards import render_vehicle_details_dialog
from frontend.components.chat import render_chat_workspace
from frontend.components.compare import render_comparison_dialog
from frontend.components.leads import render_leads_dialog
from frontend.components.sidebar import render_sidebar
from frontend.state import (
    get_api_client,
    init_ui_state,
    mark_dirty,
    refresh_backend_snapshot,
)
from frontend.styles import build_theme_css

_FAVICON_PATH = Path(__file__).resolve().parent / "assets" / "favicon.png"


def _render_active_dialog(*, client: DubizzleAPIClient, snapshot: dict[str, Any]) -> None:
    active = st.session_state.get("active_dialog")
    if not active and st.session_state.get("booking_panel_open"):
        active = "booking"

    if active == "details" and st.session_state.get("inspect_listing_id"):
        user_obj = snapshot.get("user") or {}
        session_obj = snapshot.get("session") or {}
        favourite_ids = {int(f["listing_id"]) for f in (snapshot.get("favourites") or [])}
        render_vehicle_details_dialog(
            client=client,
            listing_id=int(st.session_state["inspect_listing_id"]),
            user_id=str(user_obj.get("user_id") or st.session_state["user_id"]),
            session_id=str(session_obj.get("session_id") or st.session_state.get("session_id") or ""),
            favourite_ids=favourite_ids,
            summary_cache=st.session_state.get("listing_summary_cache", {}),
        )
    elif active == "compare":
        render_comparison_dialog(client=client, snapshot=snapshot)
    elif active == "booking":
        render_booking_dialog(client=client, snapshot=snapshot)
    elif active == "leads":
        render_leads_dialog(client=client, snapshot=snapshot)


def main() -> None:
    st.set_page_config(
        page_title="dubizzle AI — UAE Used-Car Discovery",
        page_icon=str(_FAVICON_PATH) if _FAVICON_PATH.exists() else None,
        layout="wide",
        initial_sidebar_state="expanded",
    )

    init_ui_state()
    st.markdown(
        build_theme_css(st.session_state.get("theme", "light")),
        unsafe_allow_html=True,
    )

    client = get_api_client()
    snapshot = refresh_backend_snapshot(client)

    forget_notice = st.session_state.pop("forget_me_success", None)
    if forget_notice:
        st.success(str(forget_notice))

    api_error = st.session_state.get("api_error")
    if api_error:
        err_col, btn_col = st.columns([5, 1])
        err_col.error(api_error)
        if btn_col.button("Retry", key="retry_api_btn", use_container_width=True):
            st.session_state["api_error"] = None
            mark_dirty()
            st.rerun()

    if snapshot is None:
        st.markdown(
            '<div class="dbz-brand" style="margin-bottom:1rem;">'
            '<span class="dbz-wordmark">dubizzle</span>'
            '<span class="dbz-submark">UAE Car Assistant</span>'
            "</div>"
            '<div class="dbz-hero">'
            '<div class="dbz-hero-kicker">Connection Unavailable</div>'
            '<h2 class="dbz-hero-title">Backend service is currently unreachable.</h2>'
            '<p class="dbz-hero-desc">'
            "Start the FastAPI backend server and click Retry above to load live inventory, saved vehicles, and your session."
            "</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    render_sidebar(client=client, snapshot=snapshot)
    render_chat_workspace(client=client, snapshot=snapshot)
    _render_active_dialog(client=client, snapshot=snapshot)


if __name__ == "__main__":
    main()
