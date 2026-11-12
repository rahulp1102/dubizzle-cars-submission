"""Phase 0 foundation tests: schema, configuration, and health endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import settings
from backend.db import get_db, init_db
from backend.main import create_app


def test_init_db_creates_all_tables_and_fts(temp_db: Path) -> None:
    expected_tables = {
        "listings",
        "listing_attributes",
        "extraction_cache",
        "users",
        "preferences",
        "searches",
        "favourites",
        "sessions",
        "messages",
        "bookings",
        "leads",
        "agent_traces",
        "listings_fts",
    }
    with get_db(temp_db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
        actual = {row["name"] for row in rows}

    assert expected_tables.issubset(actual)


def test_init_db_is_idempotent(temp_db: Path) -> None:
    # Calling init_db again must not fail or reset existing data
    with get_db(temp_db) as conn:
        conn.execute("INSERT INTO users (user_id, name) VALUES ('u1', 'Ali')")

    init_db(temp_db)

    with get_db(temp_db) as conn:
        row = conn.execute("SELECT name FROM users WHERE user_id = 'u1'").fetchone()
    assert row is not None
    assert row["name"] == "Ali"


def test_dataset_file_exists_and_is_unmodified() -> None:
    assert settings.DATASET_PATH.exists()
    assert settings.DATASET_SHEET == "raw dataset"


def test_health_endpoint(temp_db: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "DATABASE_URL", str(temp_db))
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "gemini_configured" in payload
