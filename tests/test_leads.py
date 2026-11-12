"""Automated offline tests for Phase 7 lead management, deterministic scoring, and CSV export."""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.agent.guardrails import redact_sensitive_text
from backend.agent.llm_client import LLMClient, LLMMessage, LLMResponse, LLMToolCall, ToolSchema
from backend.agent.orchestrator import AgentOrchestrator
from backend.db import get_db
from backend.inventory.ingestion import ingest_inventory
from backend.inventory.search import InventorySearchService
from backend.main import create_app
from backend.memory.repository import MemoryRepository
from backend.memory.service import MemoryService
from backend.models.entities import LeadStatus
from backend.models.schemas import SearchFilters
from backend.services.booking import DUBAI_TZ, BookingService
from backend.services.exceptions import BadRequestError, NotFoundError
from backend.services.leads import LeadService, mask_phone, score_lead, validate_and_normalize_phone
from backend.services.sessions import SessionService
from backend.services.users import UserService


class _ScriptedLLMClient(LLMClient):
    """Offline mock LLM client for lead tool tests."""

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
            raise AssertionError("ScriptedLLMClient ran out of scripted responses.")
        return self._responses.pop(0)


@pytest.fixture()
def lead_env(temp_db: Path, tmp_path: Path) -> dict[str, Any]:
    ingest_inventory(db_path=temp_db)
    users = UserService(temp_db)
    users.create_or_get_user("u_lead1", "Khalid")
    users.create_or_get_user("u_lead2")
    SessionService(temp_db).create_session("u_lead1", "s_lead1")
    listings = InventorySearchService(temp_db).search_inventory(SearchFilters(), limit=3).results
    exports_dir = tmp_path / "exports"

    def now_fn() -> datetime:
        return datetime(2026, 11, 12, 9, 0, tzinfo=DUBAI_TZ)
    return {
        "db": temp_db,
        "exports_dir": exports_dir,
        "now_fn": now_fn,
        "listing_id": listings[0].listing_id,
        "listing_id_2": listings[1].listing_id,
    }


def test_phone_validation_normalization_and_masking() -> None:
    assert validate_and_normalize_phone("050 123-4567") == "+971501234567"
    assert validate_and_normalize_phone("00971521234567") == "+971521234567"
    assert validate_and_normalize_phone("+971559876543") == "+971559876543"
    assert validate_and_normalize_phone("+44 7700 900123") == "+447700900123"

    for invalid in ["", "12345", "abc-phone", "050123", "+971000000000", "9999999999"]:
        with pytest.raises(BadRequestError):
            validate_and_normalize_phone(invalid)

    masked = mask_phone("+971501234567")
    assert masked == "+971******567"
    assert "501234" not in masked


def test_deterministic_lead_scoring_rules() -> None:
    # Cold: insufficient info (only makes, or only budget, or only contact info)
    assert score_lead(budget=None, listing_ids=[1], preferred_makes=["bmw"]) == LeadStatus.COLD
    assert score_lead(budget=150000) == LeadStatus.COLD
    assert score_lead(budget=None, has_confirmed_booking=True) == LeadStatus.COLD

    # Warm: known budget + demonstrated interest (listing, favourites, makes, or proposed booking)
    assert (
        score_lead(budget=150000, preferred_makes=["mercedes-benz"]) == LeadStatus.WARM
    )
    assert score_lead(budget=150000, favourite_count=1) == LeadStatus.WARM
    assert score_lead(budget=150000, has_proposed_booking=True) == LeadStatus.WARM

    # Hot: confirmed booking + known budget
    assert (
        score_lead(
            budget=150000,
            listing_ids=[1],
            has_confirmed_booking=True,
        )
        == LeadStatus.HOT
    )


def test_lead_upsert_no_duplicates_no_fabrication_and_csv_export(
    lead_env: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    db = lead_env["db"]
    exports_dir = lead_env["exports_dir"]
    svc = LeadService(db, exports_dir=exports_dir)
    lid1 = lead_env["listing_id"]
    lid2 = lead_env["listing_id_2"]

    caplog.set_level(logging.INFO)

    # First upsert: budget in USD + no interest yet -> COLD, unknown fields stay None
    d1 = svc.upsert_lead(
        user_id="u_lead2",
        phone="0501234567",
        budget=40000,
        currency="USD",
    )
    assert d1.lead.status == LeadStatus.COLD
    assert d1.lead.original_budget == 40000.0
    assert d1.lead.original_currency == "USD"
    assert d1.lead.budget == pytest.approx(40000 * 3.6725, abs=0.01)
    assert d1.lead.currency == "AED"
    assert d1.lead.name is None
    assert d1.lead.purchase_timeline is None
    assert d1.lead.financing_interest is None
    assert d1.next_question is not None

    # Logs must mask phone number
    assert "0501234567" not in caplog.text
    assert "+971501234567" not in caplog.text
    assert "+971******567" in caplog.text

    # Second upsert for SAME user merges interest -> 1 row in SQLite, now WARM
    d2 = svc.upsert_lead(
        user_id="u_lead2",
        preferred_makes=["Mercedes"],
        listing_ids=[lid1],
        purchase_timeline="this month",
    )
    assert d2.lead.lead_id == d1.lead.lead_id
    assert d2.lead.status == LeadStatus.WARM
    assert d2.lead.listing_ids == [lid1]
    assert d2.lead.phone == "+971501234567"

    with get_db(db) as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE user_id = 'u_lead2'"
        ).fetchone()[0]
    assert cnt == 1

    # Confirm a booking -> automatically promotes lead to HOT and updates CSV
    bsvc = BookingService(db, now_fn=lead_env["now_fn"], exports_dir=exports_dir)
    prop = bsvc.propose_booking(
        user_id="u_lead2",
        listing_id=lid2,
        requested_date="2026-11-14",
        requested_time="10:00",
    )
    assert prop.booking and prop.booking.booking_id
    bsvc.confirm_booking(
        booking_id=prop.booking.booking_id,
        user_id="u_lead2",
        confirm=True,
    )

    hot_lead = svc.get_lead("u_lead2")
    assert hot_lead.lead.status == LeadStatus.HOT
    assert hot_lead.lead.booking_id == prop.booking.booking_id
    assert hot_lead.lead.listing_ids == [lid1, lid2]

    csv_path = exports_dir / "leads.csv"
    assert csv_path.exists()
    with csv_path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["user_id"] == "u_lead2"
    assert rows[0]["status"] == "hot"
    assert rows[0]["original_currency"] == "USD"


def test_lead_api_and_qualification_questions(lead_env: dict[str, Any]) -> None:
    admin_secret = "test-admin-secret-key"
    app = create_app(
        db_path=lead_env["db"],
        auto_ingest=False,
        now_fn=lead_env["now_fn"],
        exports_dir=lead_env["exports_dir"],
        admin_api_key=admin_secret,
    )
    with TestClient(app) as client:
        # Nonexistent user & missing lead
        assert client.get("/leads/no_such_user").status_code == 404
        assert client.get("/leads/u_lead1").status_code == 404

        # Invalid phone rejected cleanly
        bad = client.put(
            "/leads/u_lead1",
            json={"phone": "not-a-phone"},
        )
        assert bad.status_code == 400

        # Create lead via PUT without repeating user_id in body
        up1 = client.put(
            "/leads/u_lead1",
            json={"budget": 180000, "currency": "AED", "preferred_makes": ["audi"]},
        )
        assert up1.status_code == 200
        body1 = up1.json()
        assert body1["lead"]["status"] == "warm"
        assert body1["next_question"] is not None
        assert "timeline" in body1["next_question"].lower()

        # Fill timeline -> next single question moves to cash vs financing
        up2 = client.put(
            "/leads/u_lead1",
            json={"purchase_timeline": "within 2 weeks"},
        )
        assert "financing" in up2.json()["next_question"].lower() or "cash" in up2.json()["next_question"].lower()

        # Unauthenticated global list/export blocked server-side
        assert client.get("/leads").status_code == 401
        assert client.get("/leads/export").status_code == 401

        # Authorised admin access succeeds via X-Admin-Key
        listed = client.get("/leads", headers={"X-Admin-Key": admin_secret})
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        exported = client.get("/leads/export", headers={"X-Admin-Key": admin_secret})
        assert exported.status_code == 200
        assert "text/csv" in exported.headers["content-type"]
        assert "u_lead1" in exported.text

    with pytest.raises(NotFoundError):
        LeadService(lead_env["db"]).get_lead("u_lead2")


def test_agent_update_lead_tool_and_trace_phone_redaction(
    lead_env: dict[str, Any],
) -> None:
    db = lead_env["db"]
    lid = lead_env["listing_id"]
    mock_llm = _ScriptedLLMClient(
        [
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="update_lead_information",
                        arguments={
                            "name": "Khalid",
                            "phone": "0559876543",
                            "budget": 200000,
                            "currency": "AED",
                            "preferred_makes": ["bmw"],
                            "listing_ids": [lid],
                        },
                    )
                ]
            ),
            LLMResponse(
                text=(
                    "Thanks Khalid, I noted your AED 200,000 budget and saved 0559876543. "
                    "What is your expected timeline for purchasing?"
                )
            ),
        ]
    )

    orch = AgentOrchestrator(
        db_path=db,
        llm_client=mock_llm,
        now_fn=lead_env["now_fn"],
        exports_dir=lead_env["exports_dir"],
    )
    resp = orch.handle_turn(
        session_id="s_lead1",
        user_id="u_lead1",
        message="My budget is 200k AED for a BMW and my number is 0559876543.",
    )
    assert "update_lead_information" in resp.tools_called
    assert "0559876543" not in resp.reply
    assert "[REDACTED_PHONE]" in resp.reply

    traces = MemoryRepository(db).get_session_traces("s_lead1", limit=5)
    assert "0559876543" not in str(traces)
    assert "+971559876543" not in str(traces)

    # DB & CSV keep validated normalised phone cleanly
    lead = LeadService(db, exports_dir=lead_env["exports_dir"]).get_lead("u_lead1").lead
    assert lead.phone == "+971559876543"
    assert lead.status == LeadStatus.WARM


def test_lead_scoring_includes_body_type_and_csv_exports_slot_and_vehicles(
    lead_env: dict[str, Any],
) -> None:
    db = lead_env["db"]
    exports_dir = lead_env["exports_dir"]
    svc = LeadService(db, exports_dir=exports_dir)
    lid = lead_env["listing_id"]

    # Budget + preferred_body_type alone (without makes/listings) -> WARM, not COLD;
    # preferred_body_type without budget -> still COLD.
    assert score_lead(budget=None, preferred_body_type="SUV") == LeadStatus.COLD
    assert score_lead(budget=150000, preferred_body_type="SUV") == LeadStatus.WARM

    # User with body type + budget + timeline + phone (no booking, no listing_ids) -> WARM and clean CSV
    d_body = svc.upsert_lead(
        user_id="u_lead2",
        name="Sarah",
        phone="0509876543",
        budget=150000,
        preferred_body_type="SUV",
        purchase_timeline="this month",
        financing_interest="cash",
    )
    assert d_body.lead.status == LeadStatus.WARM

    # User with booking + listing -> CSV populates booking_slot and interested_vehicles cleanly
    bsvc = BookingService(db, now_fn=lead_env["now_fn"], exports_dir=exports_dir)
    prop = bsvc.propose_booking(
        user_id="u_lead1",
        listing_id=lid,
        requested_date="2026-11-14",
        requested_time="11:00",
    )
    assert prop.booking is not None

    csv_path = exports_dir / "leads.csv"
    with csv_path.open(encoding="utf-8") as fh:
        rows = {r["user_id"]: r for r in csv.DictReader(fh)}

    assert "booking_slot" in rows["u_lead2"]
    assert "interested_vehicles" in rows["u_lead2"]
    assert rows["u_lead2"]["booking_slot"] == ""
    assert rows["u_lead2"]["interested_vehicles"] == ""
    assert rows["u_lead2"]["status"] == "warm"

    assert "2026-11-14 11:00-12:00 (Asia/Dubai)" == rows["u_lead1"]["booking_slot"]
    assert f"#{lid}" in rows["u_lead1"]["interested_vehicles"]


def test_lead_scoring_and_csv_sync_with_financing_and_preferences(
    lead_env: dict[str, Any],
) -> None:
    db = lead_env["db"]
    exports_dir = lead_env["exports_dir"]
    svc = LeadService(db, exports_dir=exports_dir)
    lid = lead_env["listing_id"]

    # Budget + financing_interest + purchase_timeline (even without phone) boosts to WARM
    assert score_lead(budget=120000, financing_interest="bank financing", purchase_timeline="this month") == LeadStatus.WARM
    assert score_lead(budget=120000, must_have_features=["white exterior", "warranty"]) == LeadStatus.WARM

    # Financing extraction helper via agent tool & CSV sync on favourite/preference
    UserService(db).create_or_get_user("u_lead_fin", "Tariq")
    SessionService(db).create_session("u_lead_fin", "s_lead_fin")
    SessionService(db).add_message(
        "s_lead_fin", "u_lead_fin", "user", "I need bank financing and a white SUV."
    )
    MemoryService(db).save_preference(
        user_id="u_lead_fin",
        preference_key="preferred_colour",
        preference_value="white",
        original_wording="white SUV",
        source_type="explicit",
        session_id="s_lead_fin",
    )
    UserService(db).add_favourite("u_lead_fin", lid)
    svc.upsert_lead(
        user_id="u_lead_fin",
        name="Tariq",
        phone="+971501112233",
        budget=120000,
        preferred_body_type="SUV",
        financing_interest="bank financing",
        listing_ids=[lid],
    )
    svc.sync_user_lead("u_lead_fin")
    lead = svc.get_lead("u_lead_fin").lead
    assert lead.financing_interest == "bank financing"
    assert lead.must_have_features and any("white" in f.lower() for f in lead.must_have_features)
    assert lid in lead.listing_ids

    csv_path = exports_dir / "leads.csv"
    with csv_path.open(encoding="utf-8") as fh:
        rows = {r["user_id"]: r for r in csv.DictReader(fh)}
    assert rows["u_lead_fin"]["financing_interest"] == "bank financing"
    assert f"#{lid}" in rows["u_lead_fin"]["interested_vehicles"]


def test_lead_admin_auth_fail_closed_and_log_redaction(
    lead_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Global lead routes fail closed without admin credentials and redact PII/secrets in logs."""
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)

    # 1. Fail-closed when ADMIN_API_KEY is unconfigured
    unconfigured_app = create_app(
        db_path=lead_env["db"],
        auto_ingest=False,
        now_fn=lead_env["now_fn"],
        exports_dir=lead_env["exports_dir"],
        admin_api_key="",
    )
    with TestClient(unconfigured_app) as client:
        for path in ("/leads", "/leads/export"):
            resp = client.get(path)
            assert resp.status_code == 403
            assert resp.json()["error_code"] == "admin_not_configured"
        assert not (lead_env["exports_dir"] / "leads.csv").exists()

    # 2. Configured admin key: reject missing/wrong/query-param credentials, accept header credentials
    admin_secret = "super-secret-admin-token-99"
    monkeypatch.setenv("ADMIN_API_KEY", admin_secret)
    LeadService(lead_env["db"], exports_dir=lead_env["exports_dir"]).upsert_lead(
        user_id="u_lead1",
        name="Ali Hassan",
        phone="0501234567",
        budget=150000,
    )

    configured_app = create_app(
        db_path=lead_env["db"],
        auto_ingest=False,
        now_fn=lead_env["now_fn"],
        exports_dir=lead_env["exports_dir"],
        admin_api_key=admin_secret,
    )
    with TestClient(configured_app) as client:
        for path in ("/leads", "/leads/export"):
            missing = client.get(path)
            assert missing.status_code == 401
            assert missing.json()["error_code"] == "admin_auth_required"
            assert admin_secret not in missing.text
            assert "+971501234567" not in missing.text

            # Query-parameter tokens must not be accepted
            query_attempt = client.get(path, params={"admin_key": admin_secret})
            assert query_attempt.status_code == 401

            wrong = client.get(path, headers={"X-Admin-Key": "wrong-key"})
            assert wrong.status_code == 403
            assert wrong.json()["error_code"] == "admin_forbidden"
            assert admin_secret not in wrong.text

            bearer_ok = client.get(path, headers={"Authorization": f"Bearer {admin_secret}"})
            assert bearer_ok.status_code == 200

    # 3. Unexpected exception logs and redact_sensitive_text strip ADMIN_API_KEY and phone numbers
    redacted = redact_sensitive_text(
        f"Failed calling with key={admin_secret} for phone=+971501234567"
    )
    assert admin_secret not in redacted
    assert "+971501234567" not in redacted
    assert "[REDACTED_API_KEY]" in redacted
    assert "[REDACTED_PHONE]" in redacted

    @configured_app.get("/_test_boom_privacy")
    def _boom_route() -> None:
        raise RuntimeError(f"boom key={admin_secret} phone=+971501234567")

    with caplog.at_level(logging.ERROR, logger="backend.main"):
        with TestClient(configured_app, raise_server_exceptions=False) as err_client:
            err_resp = err_client.get("/_test_boom_privacy")
            assert err_resp.status_code == 500
            assert admin_secret not in err_resp.text
            assert "+971501234567" not in err_resp.text

    assert admin_secret not in caplog.text
    assert "+971501234567" not in caplog.text
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_exports_dir_isolates_alternate_database_url_and_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overriding DATABASE_URL or EXPORTS_DIR isolates LeadService CSVs from default data/exports."""
    from backend.config import settings
    from backend.db import init_db

    alt_db = tmp_path / "isolated" / "runtime.db"
    alt_db.parent.mkdir(parents=True, exist_ok=True)
    init_db(alt_db)
    monkeypatch.delenv("EXPORTS_DIR", raising=False)
    monkeypatch.setenv("DATABASE_URL", str(alt_db))

    svc = LeadService()
    assert settings.exports_dir == (alt_db.resolve().parent / "exports")
    assert svc.exports_dir == (alt_db.resolve().parent / "exports")

    custom_exports = tmp_path / "custom_exports"
    monkeypatch.setenv("EXPORTS_DIR", str(custom_exports))
    svc_override = LeadService()
    assert settings.exports_dir == custom_exports
    assert svc_override.exports_dir == custom_exports
