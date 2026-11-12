"""Automated UI, API client, and integration tests for Phase 8 Streamlit frontend."""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from backend.agent.llm_client import LLMClient, LLMMessage, LLMResponse, LLMToolCall, ToolSchema
from backend.inventory.ingestion import ingest_inventory
from backend.inventory.search import InventorySearchService
from backend.main import create_app
from backend.models.schemas import SearchFilters
from backend.services.booking import DUBAI_TZ
from frontend.api_client import DubizzleAPIClient, DubizzleAPIError
from frontend.components.cards import PLACEHOLDER_IMAGE_URI, _render_card_html

APP_PATH = str(Path(__file__).resolve().parent.parent / "frontend" / "app.py")


class _ScriptedLLMClient(LLMClient):
    """Offline deterministic mock LLM for full Streamlit AppTest flows."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)

    def generate(
        self,
        *,
        system_prompt: str,
        messages: list[LLMMessage],
        tools: list[ToolSchema],
    ) -> LLMResponse:
        if not self._responses:
            return LLMResponse(text="I can help clarify your search or viewing slot.")
        return self._responses.pop(0)


@pytest.fixture()
def ui_env(temp_db: Path, tmp_path: Path) -> dict[str, Any]:
    ingest_inventory(db_path=temp_db)
    merc_rows = InventorySearchService(temp_db).search_inventory(
        SearchFilters(make="mercedes-benz", spec_region="GCC"), limit=4
    ).results
    assert len(merc_rows) >= 2

    mock_llm = _ScriptedLLMClient(
        [
            # Turn 1: Search Mercedes-Benz GCC cars
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="search_inventory",
                        arguments={"make": "mercedes-benz", "spec_region": "GCC", "limit": 4},
                    )
                ]
            ),
            LLMResponse(text="Here are the verified Mercedes-Benz GCC listings available."),
            # Turn 2: Contextual follow-up after "Ask about this"
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="resolve_listing_reference",
                        arguments={"reference_text": "this car"},
                    )
                ]
            ),
            LLMResponse(text="I checked the mileage from the active listing record."),
        ]
    )

    def fixed_now() -> datetime:
        # Thursday 2026-11-12 09:00 Asia/Dubai
        return datetime(2026, 11, 12, 9, 0, tzinfo=DUBAI_TZ)

    app = create_app(
        db_path=temp_db,
        auto_ingest=False,
        llm_client=mock_llm,
        now_fn=fixed_now,
        exports_dir=tmp_path / "exports",
    )
    return {
        "db": temp_db,
        "app": app,
        "exports_dir": tmp_path / "exports",
        "now_fn": fixed_now,
        "merc_ids": [m.listing_id for m in merc_rows],
    }


def _make_asgi_client(app: Any) -> DubizzleAPIClient:
    tc = TestClient(app)

    def _dispatch(req: httpx.Request) -> httpx.Response:
        raw_path = req.url.raw_path.decode("ascii")
        resp = tc.request(
            req.method,
            raw_path,
            headers={k: v for k, v in req.headers.items() if k.lower() != "host"},
            content=req.content,
        )
        return httpx.Response(
            status_code=resp.status_code,
            headers=resp.headers,
            content=resp.content,
            request=req,
        )

    return DubizzleAPIClient(
        base_url="http://testserver",
        transport=httpx.MockTransport(_dispatch),
    )


def test_frontend_has_no_direct_db_or_gemini_imports() -> None:
    forbidden = {"sqlite3", "google", "google.genai", "backend"}
    frontend_dir = Path("frontend")
    py_files = list(frontend_dir.rglob("*.py"))
    assert py_files
    for py_file in py_files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden, f"{py_file} imports forbidden module {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                assert top not in forbidden, f"{py_file} imports forbidden module {node.module}"
        assert "GEMINI_API_KEY" not in py_file.read_text(encoding="utf-8")
        assert "ADMIN_API_KEY" not in py_file.read_text(encoding="utf-8")


def test_inventory_summaries_endpoint_and_api_client_errors(ui_env: dict[str, Any]) -> None:
    app = ui_env["app"]
    target_ids = ui_env["merc_ids"][:2]
    with TestClient(app) as client:
        ok = client.get("/inventory/summaries", params=[("ids", target_ids[1]), ("ids", target_ids[0])])
        assert ok.status_code == 200
        body = ok.json()
        assert [row["listing_id"] for row in body] == [target_ids[1], target_ids[0]]

        empty = client.get("/inventory/summaries")
        assert empty.status_code == 200
        assert empty.json() == []

        bad = client.get("/inventory/summaries", params=[("ids", 0)])
        assert bad.status_code == 400

    # API client unreachable & HTTP error translation
    bad_transport = httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("connection refused", request=req))
    )
    down_client = DubizzleAPIClient(base_url="http://127.0.0.1:59999", transport=bad_transport)
    with pytest.raises(DubizzleAPIError) as exc_info:
        down_client.health()
    assert exc_info.value.is_connection_error is True

    timeout_transport = httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("timed out", request=req))
    )
    to_client = DubizzleAPIClient(base_url="http://127.0.0.1:59999", transport=timeout_transport)
    with pytest.raises(DubizzleAPIError) as to_exc:
        to_client.health()
    assert to_exc.value.error_code == "timeout"


def test_card_html_missing_images_and_missing_attributes() -> None:
    sparse_item = {
        "listing_id": 999,
        "year": 2021,
        "make": "honda",
        "model": "cr-v",
        "trim": "",
        "title": "Honda CR-V",
        "photo_url": "",
        "cash_price": None,
        "cash_price_status": None,
        "monthly_instalment": 1450.0,
        "mileage_km": None,
        "spec_region": None,
        "transmission": None,
        "fuel_type": None,
        "body_type": None,
        "warranty": None,
        "is_suspected_duplicate": False,
        "match_reason": "Match <script>alert(1)</script>",
    }
    rendered = _render_card_html(sparse_item)
    assert PLACEHOLDER_IMAGE_URI in rendered
    assert "Price not listed" in rendered
    assert "Est. AED 1,450/mo" in rendered
    assert "Mileage not listed" in rendered
    assert '<div class="dbz-card-trim">&nbsp;</div>' in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered

    sparse_with_colour = {**sparse_item, "colour": "Pearl White", "mileage_km": 42000}
    rendered_col = _render_card_html(sparse_with_colour)
    assert "Pearl White" in rendered_col
    assert "42,000 km" in rendered_col
    assert "Mileage not listed" not in rendered_col
    assert "&lt;script&gt;" in rendered


def test_streamlit_new_user_theme_chat_cards_save_focus_booking_restart_and_forget_me(
    ui_env: dict[str, Any],
) -> None:
    app = ui_env["app"]
    api_client = _make_asgi_client(app)

    # 1. First-visit new user experience
    at = AppTest.from_file(APP_PATH, default_timeout=20)
    at.session_state["_injected_api_client"] = api_client
    at.session_state["user_id"] = "u_ui_ali"
    at.session_state["_pending_profile_name"] = "Ali"
    at.run()
    assert not at.exception
    html_text = "\n".join(str(m.value) for m in at.markdown)
    assert "Find verified used cars through natural conversation." in html_text
    assert "Find GCC-spec SUVs." in [b.label for b in at.button]

    # 2. Light theme styling is active without a dark-mode toggle
    assert not any(t.key == "theme_toggle" for t in at.toggle)
    assert at.session_state["theme"] == "light"
    assert "#F6F7F9" in html_text

    # 3. Chat turn triggers POST /chat and renders DB-backed vehicle cards
    at.chat_input[0].set_value("Show me Mercedes-Benz GCC cars.").run()
    assert not at.exception
    after_chat_html = "\n".join(str(m.value) for m in at.markdown)
    assert "Here are the verified Mercedes-Benz GCC listings available." in after_chat_html
    assert "Mercedes-Benz" in after_chat_html

    # 4. Card actions: Save & Unsave -> updates sidebar Saved Vehicles; Details -> loads detail endpoint; Ask about this -> backend focus
    save_buttons = [b for b in at.button if b.label == "Save"]
    assert save_buttons
    save_buttons[0].click().run()
    assert not at.exception
    assert "Saved Vehicles (1)" in "\n".join(str(m.value) for m in at.sidebar.markdown)

    # Unsave then re-save to verify toggle
    next(b for b in at.button if b.label == "Saved").click().run()
    assert not at.exception
    assert "Saved Vehicles (0)" in "\n".join(str(m.value) for m in at.sidebar.markdown)
    next(b for b in at.button if b.label == "Save").click().run()
    assert "Saved Vehicles (1)" in "\n".join(str(m.value) for m in at.sidebar.markdown)

    details_btns = [b for b in at.button if b.label == "Details"]
    assert details_btns
    details_btns[0].click().run()
    assert not at.exception
    dlg_md = "\n".join(str(m.value) for m in at.markdown)
    assert "Verified Specifications & Extraction Provenance" in dlg_md
    next(b for b in at.button if str(b.key).startswith("dlg_close_det_")).click().run()

    ask_buttons = [b for b in at.button if b.label == "Ask AI"]
    assert ask_buttons
    ask_buttons[0].click().run()
    assert not at.exception
    assert "Active vehicle in context" in "\n".join(str(m.value) for m in at.markdown)

    # Contextual follow-up via focus strip quick button
    km_btn = next(b for b in at.button if b.label == "What's the mileage?")
    km_btn.click().run()
    assert not at.exception
    assert "I checked the mileage from the active listing record." in "\n".join(
        str(m.value) for m in at.markdown
    )

    # 5. Booking experience: invalid Sunday -> alternatives -> propose -> explicit confirm
    book_btns = [b for b in at.button if b.label == "Book viewing"]
    book_btns[0].click().run()
    assert not at.exception
    assert at.session_state["booking_panel_open"] is True

    # Proposing without phone must fail inline validation cleanly
    date_picker = next(d for d in at.date_input if d.key == "booking_date_picker")
    date_picker.set_value(datetime(2026, 11, 15).date())
    next(b for b in at.button if b.key == "propose_booking_btn").click().run()
    assert not at.exception
    assert any("Phone number" in str(e.value) and "required" in str(e.value) for e in at.error)

    # Provide required phone -> Sunday 2026-11-15 -> rejected + alternatives shown
    next(t for t in at.text_input if t.key == "_booking_phone_draft").set_value("+971501234567")
    next(b for b in at.button if b.key == "propose_booking_btn").click().run()
    assert not at.exception
    assert "Slot Rejected / Unavailable" in "\n".join(str(m.value) for m in at.markdown)
    alt_btns = [b for b in at.button if str(b.key).startswith("booking_alt_")]
    assert len(alt_btns) > 0

    # Click first valid alternative -> status becomes PROPOSED (not reserved yet) & form widgets sync
    alt_btns[0].click().run()
    assert not at.exception
    assert "Proposed — Not Reserved Yet" in "\n".join(str(m.value) for m in at.markdown)
    synced_time = next(s for s in at.selectbox if s.key == "booking_time_select").value
    synced_date = next(d for d in at.date_input if d.key == "booking_date_picker").value
    assert synced_time == at.session_state["booking_time_override"]
    assert synced_date == at.session_state["booking_date_override"]
    assert synced_date != datetime(2026, 11, 15).date()

    # Confirm button starts disabled until checkbox is checked
    confirm_btn = next(b for b in at.button if str(b.key).startswith("confirm_btn_"))
    assert confirm_btn.disabled is True
    confirm_chk = next(c for c in at.checkbox if str(c.key).startswith("confirm_chk_"))
    confirm_chk.set_value(True).run()
    next(b for b in at.button if str(b.key).startswith("confirm_btn_")).click().run()
    assert not at.exception
    page_md = "\n".join(str(m.value) for m in at.markdown)
    assert "Confirmed Booking" in page_md
    assert "Booking #1 Confirmed" in page_md
    assert "Booking History (1)" in "\n".join(str(m.value) for m in at.sidebar.markdown)

    # 6. Simulated restart / fresh browser session with same user_id & session_id restores state from FastAPI
    saved_sid = at.session_state["session_id"]
    restarted_app = create_app(
        db_path=ui_env["db"],
        auto_ingest=False,
        llm_client=_ScriptedLLMClient([]),
        now_fn=ui_env["now_fn"],
        exports_dir=ui_env["exports_dir"],
    )
    at2 = AppTest.from_file(APP_PATH, default_timeout=20)
    at2.session_state["_injected_api_client"] = _make_asgi_client(restarted_app)
    at2.session_state["user_id"] = "u_ui_ali"
    at2.session_state["session_id"] = saved_sid
    at2.run()
    assert not at2.exception
    assert "Here are the verified Mercedes-Benz GCC listings available." in "\n".join(
        str(m.value) for m in at2.markdown
    )
    assert "Saved Vehicles (1)" in "\n".join(str(m.value) for m in at2.sidebar.markdown)
    assert "Booking History (1)" in "\n".join(str(m.value) for m in at2.sidebar.markdown)

    # 7. New chat session keeps profile, favourites, bookings & shows returning welcome banner
    next(b for b in at2.sidebar.button if b.key == "sidebar_new_chat").click().run()
    assert not at2.exception
    assert at2.session_state["session_id"] != saved_sid
    assert "Welcome back, Ali" in "\n".join(str(m.value) for m in at2.markdown)
    assert "Saved Vehicles (1)" in "\n".join(str(m.value) for m in at2.sidebar.markdown)

    # 8. User isolation & booking conflict alternatives: another user has clean isolated state
    at3 = AppTest.from_file(APP_PATH, default_timeout=20)
    at3.session_state["_injected_api_client"] = _make_asgi_client(restarted_app)
    at3.session_state["user_id"] = "u_ui_sara"
    at3.run()
    assert not at3.exception
    sara_sb = "\n".join(str(m.value) for m in at3.sidebar.markdown)
    assert "Saved Vehicles (0)" in sara_sb
    assert "Booking History (0)" in sara_sb

    # Sara requests the exact slot Ali already confirmed -> rejected with alternatives
    ali_bookings = _make_asgi_client(restarted_app).list_user_bookings("u_ui_ali")
    assert len(ali_bookings) == 1
    booked_slot = ali_bookings[0]
    next(b for b in at3.sidebar.button if b.key == "sidebar_open_booking").click().run()
    sara_sel = next(s for s in at3.selectbox if s.key == "booking_vehicle_select")
    sara_sel.set_value(int(booked_slot["listing_id"]))
    sara_date = next(d for d in at3.date_input if d.key == "booking_date_picker")
    sara_date.set_value(datetime.fromisoformat(booked_slot["booking_date"]).date())
    sara_time = next(s for s in at3.selectbox if s.key == "booking_time_select")
    sara_time.set_value(booked_slot["start_time"])
    # Empty name and phone both required
    next(b for b in at3.button if b.key == "propose_booking_btn").click().run()
    assert any("both required" in str(e.value) for e in at3.error)

    next(t for t in at3.text_input if t.key == "_booking_name_draft").set_value("Sara")
    next(t for t in at3.text_input if t.key == "_booking_phone_draft").set_value("+971509876543")
    next(b for b in at3.button if b.key == "propose_booking_btn").click().run()
    assert not at3.exception
    assert "Slot Rejected / Unavailable" in "\n".join(str(m.value) for m in at3.markdown)
    assert any(str(b.key).startswith("booking_alt_") for b in at3.button)

    # 9. Forget Me deletes all data for u_ui_ali only after explicit confirmation,
    # updates leads.csv on disk, clears local state, and displays UI confirmation
    csv_before = (ui_env["exports_dir"] / "leads.csv").read_text(encoding="utf-8")
    assert "u_ui_ali" in csv_before
    forget_btn_init = next(b for b in at2.sidebar.button if str(b.key).startswith("forget_me_btn_"))
    assert forget_btn_init.disabled is True
    forget_chk = next(c for c in at2.sidebar.checkbox if str(c.key).startswith("forget_me_confirm_chk_"))
    forget_chk.set_value(True).run()
    next(b for b in at2.sidebar.button if str(b.key).startswith("forget_me_btn_")).click().run()
    assert not at2.exception
    assert at2.session_state["user_id"] != "u_ui_ali"
    assert at2.session_state["session_id"] != saved_sid
    assert at2.session_state["lead_status_cache"] is None
    assert any("permanently deleted" in str(s.value).lower() for s in at2.success)
    assert "Saved Vehicles (0)" in "\n".join(str(m.value) for m in at2.sidebar.markdown)

    csv_after = (ui_env["exports_dir"] / "leads.csv").read_text(encoding="utf-8")
    assert "u_ui_ali" not in csv_after

    with TestClient(restarted_app) as check_client:
        assert check_client.get("/users/u_ui_ali").status_code == 404
        assert check_client.get("/users/u_ui_ali/bookings").status_code == 404
        assert check_client.get("/leads/u_ui_ali").status_code == 404
        assert check_client.get(f"/sessions/{saved_sid}", params={"user_id": "u_ui_ali"}).status_code == 404


def test_streamlit_api_unavailable_renders_graceful_error() -> None:
    bad_transport = httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ConnectError("connection refused", request=req))
    )
    at = AppTest.from_file(APP_PATH, default_timeout=15)
    at.session_state["_injected_api_client"] = DubizzleAPIClient(
        base_url="http://127.0.0.1:59999",
        transport=bad_transport,
    )
    at.run()
    assert not at.exception
    assert any("Cannot reach backend API" in str(e.value) for e in at.error)
    assert "Backend service is currently unreachable." in "\n".join(str(m.value) for m in at.markdown)


def test_card_html_epistemic_warranty_and_conflicts() -> None:
    item = {
        "listing_id": 42,
        "year": 2024,
        "make": "mercedes-benz",
        "model": "glc-class",
        "trim": "glc 200 4matic",
        "title": "Mercedes-Benz GLC",
        "photo_url": "",
        "language": "ar",
        "cash_price": 180000.0,
        "cash_price_status": "inferred",
        "monthly_instalment": None,
        "mileage_km": 42000.0,
        "mileage_km_status": "inferred",
        "spec_region": "GCC",
        "transmission": "Automatic",
        "body_type": "SUV",
        "body_type_status": "inferred",
        "colour": "Black",
        "warranty": "Yes (Agency Warranty)",
        "warranty_status": "confirmed",
        "is_suspected_duplicate": True,
        "duplicate_group": 2,
        "match_reason": "Make: mercedes-benz | Spec: GCC",
    }
    rendered = _render_card_html(item, theme="dark")
    assert "GLC 200 4MATIC" in rendered
    assert "Cash price conflict" in rendered
    assert "42,000 km (conflict)" in rendered
    assert "Warranty confirmed" in rendered
    assert "SUV (inferred)" in rendered
    assert "Near-duplicate group #2" in rendered
    assert "Make: Mercedes-Benz" in rendered
    assert "Arabic" in rendered

    no_war_item = {**item, "warranty": "No", "warranty_status": "confirmed"}
    rendered_no_war = _render_card_html(no_war_item)
    assert "No warranty" in rendered_no_war


def test_streamlit_compare_leads_filters_and_booking_dismiss_flows(
    ui_env: dict[str, Any],
) -> None:
    app = ui_env["app"]
    api_client = _make_asgi_client(app)

    at = AppTest.from_file(APP_PATH, default_timeout=20)
    at.session_state["_injected_api_client"] = api_client
    at.session_state["user_id"] = "u_ui_buyer2"
    at.session_state["_pending_profile_name"] = "Maya"
    at.run()
    assert not at.exception

    # 1. Structured filters include Convertible and formatted Make labels
    next(b for b in at.button if b.key == "tb_toggle_filters").click().run()
    assert not at.exception
    body_sel = next(s for s in at.selectbox if s.label == "Body type")
    assert "Convertible" in body_sel.options
    assert "MPV" in body_sel.options
    body_sel.set_value("Convertible")
    next(b for b in at.button if b.label == "Search Inventory").click().run()
    assert not at.exception
    md_filter = "\n".join(str(m.value) for m in at.markdown)
    assert "matching listing(s)" in md_filter

    # 2. Add two vehicles to Compare and open Compare dialog
    cmp_btns = [b for b in at.button if b.label == "Compare"]
    assert len(cmp_btns) >= 2
    cmp_btns[0].click().run()
    cmp_btns2 = [b for b in at.button if b.label == "Compare"]
    cmp_btns2[0].click().run()
    assert len(at.session_state["compare_listing_ids"]) == 2
    next(b for b in at.button if b.key == "tb_open_compare").click().run()
    assert not at.exception
    cmp_md = "\n".join(str(m.value) for m in at.markdown)
    assert "dbz-compare-table" in cmp_md
    assert "Accident-Free" in cmp_md
    next(b for b in at.button if b.key == "cmp_close_btn").click().run()

    # 3. Leads dialog: buyer-only enquiry form (no global lead directory or CSV export),
    # invalid phone rejection -> valid lead save -> backend CSV sync preserved & global routes gated
    assert not hasattr(api_client, "list_leads")
    assert not hasattr(api_client, "export_leads_csv")
    next(b for b in at.button if b.key == "tb_open_leads").click().run()
    assert not at.exception
    dialog_md = "\n".join(str(m.value) for m in at.markdown)
    assert "Lead Directory" not in dialog_md
    assert not any("Download leads.csv" in str(b.label) for b in at.button)

    phone_in = next(t for t in at.text_input if "Phone number" in t.label)
    phone_in.set_value("not-a-phone")
    next(b for b in at.button if b.label == "Save Buyer Enquiry").click().run()
    assert any("Invalid phone" in str(e.value) for e in at.error)

    phone_in2 = next(t for t in at.text_input if "Phone number" in t.label)
    phone_in2.set_value("+971501234567")
    budget_in = next(n for n in at.number_input if "Target max budget" in n.label)
    budget_in.set_value(140000)
    next(b for b in at.button if b.label == "Save Buyer Enquiry").click().run()
    assert not at.exception
    assert any("Qualification status" in str(s.value) for s in at.success)
    assert not any("leads.csv" in str(s.value) for s in at.success)

    with TestClient(app) as tc:
        assert tc.get("/leads").status_code == 403
        assert tc.get("/leads/export").status_code == 403
    csv_bytes = (ui_env["exports_dir"] / "leads.csv").read_bytes()
    assert b"u_ui_buyer2" in csv_bytes
    assert b"+971501234567" in csv_bytes
    next(b for b in at.button if b.key == "close_leads_dialog_btn").click().run()

    # 4. Booking draft dismiss stays dismissed without infinite re-display loop
    next(b for b in at.button if b.key == "tb_open_booking").click().run()
    assert not at.exception
    b_date = next(d for d in at.date_input if d.key == "booking_date_picker")
    b_date.set_value(datetime(2026, 11, 14).date())
    next(t for t in at.text_input if t.key == "_booking_phone_draft").set_value("+971501234567")
    next(b for b in at.button if b.key == "propose_booking_btn").click().run()
    assert "Proposed — Not Reserved Yet" in "\n".join(str(m.value) for m in at.markdown)
    dismiss_btn = next(b for b in at.button if str(b.key).startswith("dismiss_draft_"))
    dismiss_btn.click().run()
    assert not at.exception
    assert "Proposed — Not Reserved Yet" not in "\n".join(str(m.value) for m in at.markdown)
    next(b for b in at.button if b.key == "close_booking_panel").click().run()

    # 5. New chat cleanly closes filter drawer & blank User ID shows immediate warning
    assert at.session_state["show_filter_search"] is True
    next(b for b in at.sidebar.button if b.key == "sidebar_new_chat").click().run()
    assert not at.exception
    assert at.session_state["show_filter_search"] is False

    uid_input = next(t for t in at.sidebar.text_input if t.label == "User ID")
    uid_input.set_value("   ")
    next(b for b in at.sidebar.button if b.label == "Load / Create Profile").click().run()
    assert any("User ID cannot be empty" in str(w.value) for w in at.sidebar.warning)


def test_browser_favicon_assets_and_page_config() -> None:
    from PIL import Image

    assets_dir = Path(__file__).resolve().parent.parent / "frontend" / "assets"
    expected_pngs = {
        "favicon-16x16.png": (16, 16),
        "favicon-32x32.png": (32, 32),
        "favicon-48x48.png": (48, 48),
        "favicon.png": (64, 64),
    }
    for fname, dims in expected_pngs.items():
        path = assets_dir / fname
        assert path.exists(), f"Missing favicon PNG: {path}"
        with Image.open(path) as img:
            assert img.size == dims
            assert img.mode == "RGBA"
            assert any(px[3] == 0 for px in img.get_flattened_data())

    ico_path = assets_dir / "favicon.ico"
    assert ico_path.exists(), f"Missing favicon ICO: {ico_path}"
    with Image.open(ico_path) as ico:
        assert {(16, 16), (32, 32), (48, 48), (64, 64)}.issubset(
            set(ico.info.get("sizes") or ())
        )

    app_src = Path(APP_PATH).read_text(encoding="utf-8")
    assert 'Path(__file__).resolve().parent / "assets" / "favicon.png"' in app_src
    assert "page_icon=" in app_src


def test_api_client_sanitizes_html_and_network_errors() -> None:
    html_502_transport = httpx.MockTransport(
        lambda req: httpx.Response(502, text="<html><body>Bad Gateway secret=AIza123</body></html>", request=req)
    )
    c502 = DubizzleAPIClient(base_url="http://127.0.0.1:8000", transport=html_502_transport)
    with pytest.raises(DubizzleAPIError) as exc502:
        c502.chat("s1", "u1", "hi", request_id="r1")
    assert exc502.value.status_code == 502
    assert "<html>" not in exc502.value.detail
    assert "AIza123" not in exc502.value.detail
    assert "temporarily unavailable" in exc502.value.detail.lower()

    rl_429_transport = httpx.MockTransport(
        lambda req: httpx.Response(429, text="Rate limit exceeded for upstream", request=req)
    )
    c429 = DubizzleAPIClient(base_url="http://127.0.0.1:8000", transport=rl_429_transport)
    with pytest.raises(DubizzleAPIError) as exc429:
        c429.chat("s1", "u1", "hi", request_id="r2")
    assert exc429.value.status_code == 429
    assert "too many requests" in exc429.value.detail.lower()

    net_transport = httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.RemoteProtocolError("secret internal socket detail", request=req))
    )
    cnet = DubizzleAPIClient(base_url="http://127.0.0.1:8000", transport=net_transport)
    with pytest.raises(DubizzleAPIError) as exc_net:
        cnet.chat("s1", "u1", "hi", request_id="r3")
    assert "secret internal socket detail" not in exc_net.value.detail
    assert "unable to communicate" in exc_net.value.detail.lower()


def test_streamlit_chat_timeout_preserves_turn_and_retries_without_duplication(
    ui_env: dict[str, Any],
) -> None:
    app = ui_env["app"]
    tc = TestClient(app)
    seen_request_ids: list[str] = []
    fail_once = {"chat": True}

    def _dispatch(req: httpx.Request) -> httpx.Response:
        raw_path = req.url.raw_path.decode("ascii")
        if req.method == "POST" and raw_path == "/chat":
            import json as _json

            body = _json.loads(req.content.decode("utf-8"))
            seen_request_ids.append(str(body.get("request_id") or ""))
            if fail_once["chat"]:
                fail_once["chat"] = False
                raise httpx.ReadTimeout("Simulated slow response", request=req)
        resp = tc.request(
            req.method,
            raw_path,
            headers={k: v for k, v in req.headers.items() if k.lower() != "host"},
            content=req.content,
        )
        return httpx.Response(
            status_code=resp.status_code,
            headers=resp.headers,
            content=resp.content,
            request=req,
        )

    flaky_client = DubizzleAPIClient(
        base_url="http://testserver",
        transport=httpx.MockTransport(_dispatch),
    )

    at = AppTest.from_file(APP_PATH, default_timeout=20)
    at.session_state["_injected_api_client"] = flaky_client
    at.session_state["user_id"] = "u_retry_buyer"
    at.run()
    assert not at.exception

    # Initial chat attempt times out -> preserves pending turn and displays Retry / Dismiss
    at.chat_input[0].set_value("Find Mercedes GCC").run()
    assert not at.exception
    assert any("timed out" in str(e.value).lower() for e in at.error)
    assert at.session_state["pending_chat_turn"] is not None
    assert at.session_state["pending_chat_turn"]["message"] == "Find Mercedes GCC"
    assert len(seen_request_ids) == 1 and seen_request_ids[0]

    # Clicking "Retry message" reuses exact same request_id and succeeds cleanly without duplicates
    next(b for b in at.button if b.key == "retry_failed_chat_btn").click().run()
    assert not at.exception
    assert at.session_state["pending_chat_turn"] is None
    assert at.session_state["chat_error"] is None
    assert len(seen_request_ids) == 2
    assert seen_request_ids[0] == seen_request_ids[1]

    sid = at.session_state["session_id"]
    msgs = tc.get(f"/sessions/{sid}", params={"user_id": "u_retry_buyer"}).json()["session"]["recent_messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_streamlit_guest_session_and_profile_switch_state_isolation(
    ui_env: dict[str, Any],
) -> None:
    app = ui_env["app"]
    tc = TestClient(app)
    fail_target = {"fail_user_b": False}

    def _dispatch(req: httpx.Request) -> httpx.Response:
        raw_path = req.url.raw_path.decode("ascii")
        if fail_target["fail_user_b"] and "/users/u_switch_b/" in raw_path:
            return httpx.Response(503, json={"detail": "Temporary failure", "error_code": "unavailable"}, request=req)
        resp = tc.request(
            req.method,
            raw_path,
            headers={k: v for k, v in req.headers.items() if k.lower() != "host"},
            content=req.content,
        )
        return httpx.Response(
            status_code=resp.status_code,
            headers=resp.headers,
            content=resp.content,
            request=req,
        )

    client = DubizzleAPIClient(
        base_url="http://testserver",
        transport=httpx.MockTransport(_dispatch),
    )

    # 1. Guest session initializes cleanly with buyer_<16-hex> user_id
    at = AppTest.from_file(APP_PATH, default_timeout=20)
    at.session_state["_injected_api_client"] = client
    at.run()
    assert not at.exception
    guest_uid = str(at.session_state["user_id"])
    assert guest_uid.startswith("buyer_") and len(guest_uid) == len("buyer_") + 16
    assert at.session_state["session_id"]

    # Seed per-user UI state on guest_uid before switching profiles
    at.session_state["show_all_favs"] = True
    at.session_state["lead_inline_error"] = "Old lead error"
    at.session_state["booking_inline_error"] = "Old booking error"

    # 2. Failed profile switch must NEVER leak guest_uid's backend_snapshot onto u_switch_b
    fail_target["fail_user_b"] = True
    uid_input = next(t for t in at.sidebar.text_input if t.label == "User ID")
    uid_input.set_value("u_switch_b")
    next(b for b in at.sidebar.button if b.label == "Load / Create Profile").click().run()
    assert not at.exception
    assert at.session_state["user_id"] == "u_switch_b"
    assert at.session_state["backend_snapshot"] is None
    assert at.session_state["show_all_favs"] is False
    assert at.session_state["lead_inline_error"] is None
    assert at.session_state["booking_inline_error"] is None
    assert "Backend service is currently unreachable." in "\n".join(str(m.value) for m in at.markdown)

    # 3. Recover cleanly once u_switch_b succeeds
    fail_target["fail_user_b"] = False
    next(b for b in at.button if b.key == "retry_api_btn").click().run()
    assert not at.exception
    assert at.session_state["backend_snapshot"]["user"]["user_id"] == "u_switch_b"


def test_streamlit_confirm_and_lead_update_preserve_phone_when_api_masked(
    ui_env: dict[str, Any],
) -> None:
    """Confirming bookings or updating enquiries in UI must preserve DB phone when API masks phones."""
    from backend.db import get_db

    app = ui_env["app"]
    lid = ui_env["merc_ids"][0]
    api_client = _make_asgi_client(app)

    # Seed user with a proposed booking & lead containing a real phone
    with TestClient(app) as tc:
        assert tc.post("/users", json={"user_id": "u_masked_ui", "name": "Tariq"}).status_code == 201
        prop = tc.post(
            "/bookings/propose",
            json={
                "user_id": "u_masked_ui",
                "listing_id": lid,
                "requested_date": "2026-11-14",
                "requested_time": "14:00",
                "customer_name": "Tariq",
                "customer_phone": "+971507778899",
            },
        )
        assert prop.status_code == 200
        bid = prop.json()["booking"]["booking_id"]
        assert prop.json()["booking"]["customer_phone"] == "+971******899"

    # Open fresh Streamlit session (no _booking_phone_draft in RAM) -> API returns masked phone
    at = AppTest.from_file(APP_PATH, default_timeout=20)
    at.query_params["user_id"] = "u_masked_ui"
    at.session_state["_injected_api_client"] = api_client
    at.run()
    assert not at.exception

    # 1. Open Booking dialog -> _booking_phone_draft stays blank rather than masked string, Confirm succeeds
    next(b for b in at.button if b.key == "tb_open_booking").click().run()
    assert at.session_state.get("_booking_phone_draft") == ""
    next(c for c in at.checkbox if c.key == f"confirm_chk_{bid}").set_value(True).run()
    next(b for b in at.button if b.key == f"confirm_btn_{bid}").click().run()
    assert not at.exception
    assert not at.session_state.get("booking_inline_error")
    assert at.session_state["booking_result"]["booking"]["status"] == "confirmed"
    assert at.session_state["booking_result"]["booking"]["customer_phone"] == "+971******899"
    next(b for b in at.button if b.key == "close_booking_panel").click().run()

    # 2. Open My Enquiry dialog, update budget without retyping phone -> preserves real phone in DB
    next(b for b in at.button if b.key == "tb_open_leads").click().run()
    budget_num = next(n for n in at.number_input if "budget" in str(n.label).lower())
    budget_num.set_value(195000)
    next(b for b in at.button if "Save Buyer Enquiry" in str(b.label)).click().run()
    assert not at.exception
    assert not at.session_state.get("lead_inline_error")
    assert at.session_state["lead_status_cache"]["lead"]["phone"] == "+971******899"

    with get_db(ui_env["db"]) as conn:
        bk_phone = conn.execute(
            "SELECT customer_phone FROM bookings WHERE booking_id = ?", (bid,)
        ).fetchone()[0]
        ld_phone = conn.execute(
            "SELECT phone FROM leads WHERE user_id = 'u_masked_ui'"
        ).fetchone()[0]
    assert bk_phone == "+971507778899"
    assert ld_phone == "+971507778899"



