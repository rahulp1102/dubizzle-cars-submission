"""Offline tests for Phase 6 long-term memory, provenance, currency conversion, and Forget Me."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agent.llm_client import LLMResponse, LLMToolCall
from backend.agent.orchestrator import AgentOrchestrator
from backend.agent.tools import build_tool_context, execute_tool
from backend.config import settings
from backend.db import get_db, init_db
from backend.inventory.ingestion import ingest_inventory
from backend.main import create_app
from backend.memory.service import MemoryService, convert_budget_to_aed
from backend.models.schemas import SearchFilters
from backend.services.exceptions import BadRequestError
from backend.services.sessions import SessionService
from backend.services.users import UserService
from tests.test_agent import ScriptedLLMClient


@pytest.fixture()
def seeded_db(temp_db: Path) -> Path:
    ingest_inventory(db_path=temp_db)
    return temp_db


def test_explicit_preference_creation_and_provenance(seeded_db: Path) -> None:
    users = UserService(seeded_db)
    sessions = SessionService(seeded_db)
    memory = MemoryService(seeded_db)

    users.create_or_get_user("u_mem", "Layla")
    sessions.create_session("u_mem", "s_mem_1")
    msg = sessions.add_message(
        "s_mem_1", "u_mem", "user", "I prefer GCC specs and automatic transmission."
    )

    pref = memory.save_preference(
        user_id="u_mem",
        preference_key="spec_region",
        preference_value="GCC",
        original_wording="I prefer GCC specs",
        source_type="explicit",
        session_id="s_mem_1",
        current_user_message=msg.content,
        source_turn=1,
    )
    assert pref.preference_key == "spec_region"
    assert pref.preference_value == "GCC"
    assert pref.original_wording == "I prefer GCC specs"
    assert pref.source_type == "explicit"
    assert pref.is_explicit is True
    assert pref.status == "confirmed"
    assert pref.source_message_id == msg.id
    assert pref.created_at is not None
    assert pref.updated_at is not None


def test_preference_updates_contradictions_and_history(seeded_db: Path) -> None:
    UserService(seeded_db).create_or_get_user("u_upd", "Omar")
    sessions = SessionService(seeded_db)
    memory = MemoryService(seeded_db)

    sessions.create_session("u_upd", "s_upd")
    sessions.add_message("s_upd", "u_upd", "user", "I only want GCC specs.")
    memory.save_preference(
        user_id="u_upd",
        preference_key="spec_region",
        preference_value="GCC",
        original_wording="I only want GCC specs",
        source_type="explicit",
        session_id="s_upd",
    )

    # Later user contradicts their earlier preference
    sessions.add_message("s_upd", "u_upd", "user", "Actually American specs are fine too.")
    updated = memory.save_preference(
        user_id="u_upd",
        preference_key="spec_region",
        preference_value="American",
        original_wording="American specs are fine",
        source_type="explicit",
        session_id="s_upd",
    )
    assert updated.preference_value == "US / American"

    history = memory.get_preference_history("u_upd", "spec_region")
    assert len(history) == 2
    assert history[0].old_value == "GCC"
    assert history[0].new_value == "US / American"
    assert history[1].old_value is None
    assert history[1].new_value == "GCC"


def test_rejects_unsupported_and_fabricated_inferred_preferences(seeded_db: Path) -> None:
    UserService(seeded_db).create_or_get_user("u_guard", "Rami")
    sessions = SessionService(seeded_db)
    memory = MemoryService(seeded_db)
    sessions.create_session("u_guard", "s_guard")
    sessions.add_message("s_guard", "u_guard", "user", "Show me family SUVs under 100k.")

    # Fabricated Ferrari preference not grounded in user message -> MUST REJECT
    with pytest.raises(BadRequestError) as exc_info:
        memory.save_preference(
            user_id="u_guard",
            preference_key="preferred_makes",
            preference_value="Ferrari",
            original_wording="I love Ferrari",
            source_type="explicit",
            session_id="s_guard",
        )
    assert exc_info.value.error_code == "unsupported_preference"

    # Unsupported inferred preference also rejected
    with pytest.raises(BadRequestError):
        memory.save_preference(
            user_id="u_guard",
            preference_key="transmission",
            preference_value="Manual",
            original_wording="prefers manual",
            source_type="inferred",
            session_id="s_guard",
        )

    # Supported inferred preference stays labelled inferred, NEVER promoted to explicit
    inferred = memory.save_preference(
        user_id="u_guard",
        preference_key="preferred_body_types",
        preference_value="SUV",
        original_wording="family SUVs",
        source_type="inferred",
        session_id="s_guard",
    )
    assert inferred.is_explicit is False
    assert inferred.status == "inferred"
    assert inferred.confidence < 1.0


def test_temporary_search_filters_do_not_become_preferences(seeded_db: Path) -> None:
    UserService(seeded_db).create_or_get_user("u_tmp", "Zayd")
    sessions = SessionService(seeded_db)
    memory = MemoryService(seeded_db)
    sessions.create_session("u_tmp", "s_tmp")

    sessions.search_in_session(
        "s_tmp",
        "u_tmp",
        SearchFilters(make="mercedes-benz", spec_region="GCC"),
        query_text="Show me Mercedes-Benz GCC cars",
    )
    assert memory.get_preferences("u_tmp") == []
    searches = memory.get_search_history("u_tmp")
    assert len(searches) == 1
    assert searches[0].query_text == "Show me Mercedes-Benz GCC cars"
    assert searches[0].filters["make"] == "mercedes-benz"
    assert searches[0].result_count > 0
    assert len(searches[0].listing_ids) > 0


def test_memory_persists_across_new_sessions_and_restarts(seeded_db: Path) -> None:
    with TestClient(create_app(db_path=seeded_db)) as c1:
        c1.post("/users", json={"user_id": "u_persist", "name": "Hana"})
        c1.put(
            "/users/u_persist/preferences",
            json={
                "preference_key": "preferred_body_types",
                "preference_value": "SUV",
                "original_wording": "SUV",
                "source_type": "ui",
            },
        )
        c1.post("/users/u_persist/favourites", json={"listing_id": 87})
        c1.post("/sessions", json={"user_id": "u_persist", "session_id": "s_old"})
        c1.post(
            "/sessions/s_old/search",
            json={"user_id": "u_persist", "filters": {"spec_region": "GCC", "body_type": "SUV"}},
        )

    # Full app restart + completely new session
    with TestClient(create_app(db_path=seeded_db)) as c2:
        c2.post("/sessions", json={"user_id": "u_persist", "session_id": "s_brand_new"})
        ret = c2.get(
            "/users/u_persist/returning-summary",
            params={"session_id": "s_brand_new"},
        )
        assert ret.status_code == 200
        summary = ret.json()["summary"]
        assert summary["is_returning"] is True
        assert summary["name"] == "Hana"
        assert summary["favourite_count"] == 1
        assert "Welcome back, Hana!" in summary["welcome_message"]
        assert "honda cr-v" in summary["welcome_message"].lower()

        prefs = c2.get("/users/u_persist/preferences").json()["preferences"]
        assert prefs[0]["preference_value"] == "SUV"
        searches = c2.get("/users/u_persist/searches").json()["searches"]
        assert len(searches) == 1


def test_returning_user_summary_factual_and_not_repeated(seeded_db: Path) -> None:
    UserService(seeded_db).create_or_get_user("u_new", "Newbie")
    SessionService(seeded_db).create_session("u_new", "s_new_1")
    memory = MemoryService(seeded_db)

    # New user with no memory must NOT get a fake returning welcome
    fresh = memory.build_returning_user_summary("u_new", session_id="s_new_1")
    assert fresh.is_returning is False
    assert fresh.welcome_message is None

    # User with preference ONLY (no searches) must NOT claim a previous search
    memory.save_preference(
        user_id="u_new",
        preference_key="budget_max",
        preference_value="100000",
        original_wording="100000",
        source_type="ui",
    )
    SessionService(seeded_db).create_session("u_new", "s_new_2")
    returning_no_search = memory.build_returning_user_summary("u_new", session_id="s_new_2")
    assert returning_no_search.is_returning is True
    assert "last time you explored" not in (returning_no_search.welcome_message or "").lower()
    assert "100,000" in (returning_no_search.welcome_message or "")

    # Agent Turn 1 shows welcome context; Turn 2 inside same session does NOT repeat it
    mock_llm = ScriptedLLMClient(
        [
            LLMResponse(text="Welcome back, Newbie! Here to help with your AED 100,000 search."),
            LLMResponse(text="Sure, let's check SUVs."),
        ]
    )
    orch = AgentOrchestrator(db_path=seeded_db, llm_client=mock_llm)
    orch.handle_turn(session_id="s_new_2", user_id="u_new", message="Hi, I'm back.")
    after_turn1 = memory.build_returning_user_summary("u_new", session_id="s_new_2")
    assert after_turn1.welcome_message is None


def test_memory_tools_integration_and_agent_calls(seeded_db: Path) -> None:
    UserService(seeded_db).create_or_get_user("u_tools", "Samir")
    SessionService(seeded_db).create_session("u_tools", "s_tools")
    UserService(seeded_db).add_favourite("u_tools", 87)

    mock_llm = ScriptedLLMClient(
        [
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        name="save_preference",
                        arguments={
                            "preference_key": "budget_max",
                            "preference_value": "20,000 USD",
                            "original_wording": "My budget is $20,000",
                        },
                    )
                ]
            ),
            LLMResponse(
                tool_calls=[LLMToolCall(name="get_saved_vehicles", arguments={})]
            ),
            LLMResponse(
                text="I've noted your USD 20,000 budget (approx. AED 73,450) and found your saved 2021 Honda CR-V."
            ),
        ]
    )
    orch = AgentOrchestrator(db_path=seeded_db, llm_client=mock_llm)
    res = orch.handle_turn(
        session_id="s_tools",
        user_id="u_tools",
        message="My budget is $20,000 and what did I save last time?",
    )
    assert res.tools_called == ["save_preference", "get_saved_vehicles"]
    assert res.vehicle_cards[0].listing_id == 87

    ctx = build_tool_context(user_id="u_tools", session_id="s_tools", db_path=seeded_db)
    ok_p, prefs_out = execute_tool("get_user_preferences", {}, ctx)
    assert ok_p and len(prefs_out["preferences"]) == 1
    assert prefs_out["preferences"][0]["metadata"]["original_currency"] == "USD"

    ok_h, hist_out = execute_tool("get_search_history", {}, ctx)
    assert ok_h and hist_out["searches"] == []


def test_usd_to_aed_conversion_and_original_currency_preservation(seeded_db: Path) -> None:
    conv = convert_budget_to_aed(20000, "USD")
    assert conv["original_amount"] == 20000.0
    assert conv["original_currency"] == "USD"
    assert conv["normalized_amount_aed"] == round(20000 * settings.USD_TO_AED_RATE, 2)
    assert conv["is_approximate"] is True
    assert "not a live market rate" in conv["rate_note"].lower()

    with pytest.raises(BadRequestError):
        convert_budget_to_aed(20000, "EUR")

    UserService(seeded_db).create_or_get_user("u_curr", "Kareem")
    sessions = SessionService(seeded_db)
    sessions.create_session("u_curr", "s_curr")
    sessions.add_message("s_curr", "u_curr", "user", "My budget is $20,000.")

    pref = MemoryService(seeded_db).save_preference(
        user_id="u_curr",
        preference_key="budget_max",
        preference_value="$20,000",
        original_wording="My budget is $20,000",
        source_type="explicit",
        session_id="s_curr",
    )
    assert pref.preference_value == str(int(round(20000 * settings.USD_TO_AED_RATE)))
    assert pref.metadata is not None
    assert pref.metadata["original_amount"] == 20000.0
    assert pref.metadata["original_currency"] == "USD"
    assert pref.metadata["is_approximate"] is True


def test_forget_me_deletes_all_tables_and_allows_fresh_recreation(
    seeded_db: Path, tmp_path: Path
) -> None:
    from backend.services.leads import LeadService

    exports_dir = tmp_path / "forget_exports"
    users = UserService(seeded_db, exports_dir=exports_dir)
    sessions = SessionService(seeded_db)
    memory = MemoryService(seeded_db, exports_dir=exports_dir)
    leads = LeadService(seeded_db, exports_dir=exports_dir)

    users.create_or_get_user("u_forget", "Maya")
    users.create_or_get_user("u_keep", "KeepUser")

    sessions.create_session("u_forget", "s_forget")
    sessions.create_session("u_keep", "s_keep")

    sessions.add_message("s_forget", "u_forget", "user", "My budget is AED 90,000.")
    memory.save_preference(
        user_id="u_forget",
        preference_key="budget_max",
        preference_value="90000",
        original_wording="AED 90,000",
        source_type="explicit",
        session_id="s_forget",
    )
    users.add_favourite("u_forget", 87)
    sessions.search_in_session("s_forget", "u_forget", SearchFilters(make="honda"), query_text="Honda")

    # Also add agent_trace, booking, and lead rows for u_forget + a preference and lead for u_keep
    memory.save_preference(
        user_id="u_keep",
        preference_key="spec_region",
        preference_value="GCC",
        original_wording="GCC",
        source_type="ui",
    )
    leads.upsert_lead(
        user_id="u_forget",
        name="Maya",
        phone="+971501119999",
        budget=90000,
    )
    leads.upsert_lead(
        user_id="u_keep",
        name="KeepUser",
        phone="+971502228888",
        budget=150000,
    )
    csv_path = exports_dir / "leads.csv"
    assert csv_path.exists()
    csv_before = csv_path.read_text(encoding="utf-8")
    assert "u_forget" in csv_before
    assert "+971501119999" in csv_before
    assert "u_keep" in csv_before

    with get_db(seeded_db) as conn:
        conn.execute(
            """
            INSERT INTO agent_traces (session_id, user_id, user_message, tool_names, tool_calls_json, execution_ms, assistant_response)
            VALUES ('s_forget', 'u_forget', 'hi', '[]', '[]', 10, 'hello')
            """
        )
        conn.execute(
            """
            INSERT INTO bookings (user_id, listing_id, booking_date, start_time, end_time, status)
            VALUES ('u_forget', 87, '2026-11-14', '14:00', '15:00', 'proposed')
            """
        )

    # Blank user_id rejected
    with pytest.raises(BadRequestError) as blank_exc:
        memory.forget_user("   ", confirm=True)
    assert blank_exc.value.error_code == "invalid_user_id"

    # Unconfirmed deletion must fail
    with pytest.raises(BadRequestError) as exc_info:
        memory.forget_user("u_forget", confirm=False)
    assert exc_info.value.error_code == "confirmation_required"

    # Confirmed deletion wipes ALL records for u_forget and refreshes leads.csv
    memory.forget_user("u_forget", confirm=True)

    with get_db(seeded_db) as conn:
        for table in (
            "users",
            "preferences",
            "preference_history",
            "searches",
            "favourites",
            "sessions",
            "bookings",
            "leads",
            "agent_traces",
        ):
            cnt = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id = 'u_forget'"
            ).fetchone()[0]
            assert cnt == 0, f"Residual rows in {table} for u_forget"
        msg_cnt = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = 's_forget'"
        ).fetchone()[0]
        assert msg_cnt == 0

    csv_after = csv_path.read_text(encoding="utf-8")
    assert "u_forget" not in csv_after
    assert "+971501119999" not in csv_after
    assert "Maya" not in csv_after
    assert "u_keep" in csv_after
    assert "+971502228888" in csv_after

    # u_keep remains completely untouched
    assert len(memory.get_preferences("u_keep")) == 1
    assert leads.get_lead("u_keep").lead.phone == "+971502228888"

    # Same user_id can be recreated cleanly with zero stale history
    recreated, is_new = users.create_or_get_user("u_forget", "Fresh Maya")
    assert is_new is True
    assert recreated.preferences == []
    assert recreated.favourite_count == 0
    assert memory.build_returning_user_summary("u_forget").is_returning is False


def test_cross_user_memory_isolation(seeded_db: Path) -> None:
    users = UserService(seeded_db)
    sessions = SessionService(seeded_db)
    memory = MemoryService(seeded_db)

    users.create_or_get_user("u_one", "User One")
    users.create_or_get_user("u_two", "User Two")
    sessions.create_session("u_one", "s_one")
    sessions.create_session("u_two", "s_two")

    memory.save_preference(
        user_id="u_one",
        preference_key="budget_max",
        preference_value="150000",
        original_wording="150000",
        source_type="ui",
    )
    users.add_favourite("u_one", 87)
    sessions.search_in_session("s_one", "u_one", SearchFilters(make="honda"), query_text="Honda")

    assert memory.get_preferences("u_two") == []
    assert memory.get_search_history("u_two") == []
    assert users.list_favourites("u_two") == []
    assert memory.build_returning_user_summary("u_two").is_returning is False

    # Cross-user session_id must be rejected on both returning summary and preference provenance
    from backend.services.exceptions import ForbiddenError

    sessions.add_message("s_one", "u_one", "user", "I prefer GCC specs")
    with pytest.raises(ForbiddenError) as sum_exc:
        memory.build_returning_user_summary("u_two", session_id="s_one")
    assert sum_exc.value.error_code == "session_forbidden"

    with pytest.raises(ForbiddenError) as pref_exc:
        memory.save_preference(
            user_id="u_two",
            preference_key="spec_region",
            preference_value="GCC",
            original_wording="I prefer GCC specs",
            source_type="explicit",
            session_id="s_one",
        )
    assert pref_exc.value.error_code == "session_forbidden"


def test_non_destructive_schema_migration_on_legacy_db(tmp_path: Path) -> None:
    legacy_db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy_db))
    conn.executescript(
        """
        CREATE TABLE users (user_id TEXT PRIMARY KEY, name TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            preference_key TEXT NOT NULL,
            preference_value TEXT NOT NULL,
            original_wording TEXT,
            metadata_json TEXT,
            source_turn INTEGER,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, preference_key)
        );
        CREATE TABLE searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            filters_json TEXT NOT NULL,
            result_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO users (user_id, name) VALUES ('u_legacy', 'Legacy');
        INSERT INTO preferences (user_id, preference_key, preference_value, original_wording)
            VALUES ('u_legacy', 'spec_region', 'GCC', 'GCC');
        """
    )
    conn.commit()
    conn.close()

    # Running init_db must non-destructively upgrade columns and keep legacy row
    init_db(legacy_db)
    prefs = MemoryService(legacy_db).get_preferences("u_legacy")
    assert len(prefs) == 1
    assert prefs[0].preference_value == "GCC"
    assert prefs[0].created_at is not None


def test_returning_user_new_session_hi_preserves_greeting_and_blocks_false_welcome_flag(
    seeded_db: Path,
) -> None:
    users = UserService(seeded_db)
    sessions = SessionService(seeded_db)
    memory = MemoryService(seeded_db)

    users.create_or_get_user("u_rahul", "Rahul")
    sessions.create_session("u_rahul", "s_old")
    sessions.add_message("s_old", "u_rahul", "user", "I want an SUV under $20k.")
    memory.save_preference(
        user_id="u_rahul",
        preference_key="budget_max",
        preference_value="20,000 USD",
        original_wording="under $20k",
        source_type="explicit",
        session_id="s_old",
    )
    sessions.search_in_session(
        "s_old",
        "u_rahul",
        SearchFilters(max_price=73400.0, body_type="SUV", free_text="white"),
        query_text="white SUV under $20k",
    )
    users.add_favourite("u_rahul", 16)

    # New session + user says "Hi!": model echoes trusted welcome AED 73,400 without tools
    sessions.create_session("u_rahul", "s_fresh")
    mock_llm = ScriptedLLMClient(
        [
            LLMResponse(
                text=(
                    "Welcome back, Rahul! Last time you explored SUVs under AED 73,400 "
                    "and saved the 2021 Bestune T99 (#16). Would you like to continue?"
                )
            )
        ]
    )
    orch = AgentOrchestrator(db_path=seeded_db, llm_client=mock_llm)
    res = orch.handle_turn(session_id="s_fresh", user_id="u_rahul", message="Hi!")
    assert "Rahul" in res.reply
    assert "73,400" in res.reply
    assert "only share verified prices" not in res.reply.lower()
    assert "welcome_shown=true" in (sessions.get_session("s_fresh", user_id="u_rahul").context_summary or "")

    # If the model fabricates an ungrounded price not in trusted context, grounding refuses AND does NOT mark welcome_shown
    sessions.create_session("u_rahul", "s_fresh_bad")
    bad_llm = ScriptedLLMClient(
        [LLMResponse(text="This car costs AED 999,000 today!")]
    )
    bad_res = AgentOrchestrator(db_path=seeded_db, llm_client=bad_llm).handle_turn(
        session_id="s_fresh_bad", user_id="u_rahul", message="Hi!"
    )
    assert "only share verified prices" in bad_res.reply.lower()
    assert "welcome_shown=true" not in (
        sessions.get_session("s_fresh_bad", user_id="u_rahul").context_summary or ""
    )


def test_preferred_colour_provenance_and_returning_summary_merges_prefs_and_searches(
    seeded_db: Path,
) -> None:
    users = UserService(seeded_db)
    sessions = SessionService(seeded_db)
    memory = MemoryService(seeded_db)

    users.create_or_get_user("u_colour_mem", "Lina")
    sessions.create_session("u_colour_mem", "s_cm_1")
    sessions.add_message("s_cm_1", "u_colour_mem", "user", "I prefer a white SUV under 90k AED.")

    pref = memory.save_preference(
        user_id="u_colour_mem",
        preference_key="preferred_colour",
        preference_value="white",
        original_wording="white SUV",
        source_type="explicit",
        session_id="s_cm_1",
    )
    assert pref.preference_key == "preferred_colour"
    assert pref.preference_value == "White"

    # Unsupported colour preference rejected by provenance verifier
    with pytest.raises(BadRequestError):
        memory.save_preference(
            user_id="u_colour_mem",
            preference_key="preferred_colour",
            preference_value="Red",
            original_wording="red car",
            source_type="explicit",
            session_id="s_cm_1",
        )

    # Even when recent searches exist, build_returning_user_summary keeps BOTH saved prefs and recent search!
    sessions.search_in_session(
        "s_cm_1",
        "u_colour_mem",
        SearchFilters(colour="White", body_type="SUV", max_price=90000),
        query_text="white SUV under 90k AED",
    )
    sessions.create_session("u_colour_mem", "s_cm_2")
    summary = memory.build_returning_user_summary("u_colour_mem", session_id="s_cm_2")
    assert summary.is_returning is True
    assert summary.welcome_message is not None
    assert "white" in summary.welcome_message.lower()
    assert "saved preferences" in summary.welcome_message.lower()
    assert "last time you explored" in summary.welcome_message.lower()
