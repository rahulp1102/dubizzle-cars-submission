"""SQLite database initialisation and connection management.

Creates all tables, indexes, and FTS5 virtual tables on first run.
Provides a connection factory for use by repository modules.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from backend.config import settings

# ──────────────────────────────────────────────
# Schema DDL
# ──────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Listings (original ingested data) ──
CREATE TABLE IF NOT EXISTS listings (
    listing_id       INTEGER PRIMARY KEY,
    make             TEXT    NOT NULL,
    model            TEXT    NOT NULL,
    trim             TEXT    NOT NULL,
    year             INTEGER NOT NULL,
    title            TEXT    NOT NULL,
    description_raw  TEXT    NOT NULL,   -- original description, never modified
    description_display TEXT NOT NULL,   -- phone/URL stripped, <br> cleaned
    photo_url        TEXT    NOT NULL,
    language         TEXT    NOT NULL DEFAULT 'en',  -- 'en', 'ar', 'mixed'
    duplicate_group  INTEGER,            -- NULL = unique, same int = duplicate group
    content_hash     TEXT,               -- SHA-256 of title+description for dedup detection
    ingested_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Extracted attributes (one row per attribute per listing) ──
CREATE TABLE IF NOT EXISTS listing_attributes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id       INTEGER NOT NULL REFERENCES listings(listing_id) ON DELETE CASCADE,
    attribute_name   TEXT    NOT NULL,   -- e.g. 'cash_price', 'mileage_km', 'warranty'
    attribute_value  TEXT,               -- normalised value as text
    numeric_value    REAL,               -- numeric representation where applicable
    evidence         TEXT,               -- source snippet from listing text
    extraction_method TEXT   NOT NULL,   -- 'regex', 'rule', 'gemini', 'manual'
    status           TEXT    NOT NULL DEFAULT 'confirmed',  -- 'confirmed', 'inferred', 'rejected'
    confidence       REAL    DEFAULT 1.0,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(listing_id, attribute_name)   -- one value per attribute per listing
);

-- ── Extraction cache (keyed by content hash) ──
CREATE TABLE IF NOT EXISTS extraction_cache (
    content_hash      TEXT    PRIMARY KEY,
    attributes_json   TEXT    NOT NULL,
    extraction_source TEXT    NOT NULL,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Users ──
CREATE TABLE IF NOT EXISTS users (
    user_id          TEXT    PRIMARY KEY,
    name             TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Preferences (long-term memory, per user) ──
CREATE TABLE IF NOT EXISTS preferences (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT    NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    preference_key    TEXT    NOT NULL,   -- e.g. 'budget_max', 'preferred_makes'
    preference_value  TEXT    NOT NULL,   -- normalised value
    original_wording  TEXT,               -- what the user actually said
    metadata_json     TEXT,               -- structured metadata (e.g. original currency/amount)
    source_type       TEXT    NOT NULL DEFAULT 'explicit',  -- 'explicit', 'ui', 'inferred'
    is_explicit       INTEGER NOT NULL DEFAULT 1,
    status            TEXT    NOT NULL DEFAULT 'confirmed', -- 'confirmed', 'inferred'
    confidence        REAL    NOT NULL DEFAULT 1.0,
    source_turn       INTEGER,            -- conversation turn index
    source_message_id INTEGER,            -- originating message ID if from chat
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, preference_key)
);

-- ── Preference History (audit trail of updates & contradictions) ──
CREATE TABLE IF NOT EXISTS preference_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           TEXT    NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    preference_key    TEXT    NOT NULL,
    old_value         TEXT,
    new_value         TEXT    NOT NULL,
    original_wording  TEXT,
    metadata_json     TEXT,
    source_type       TEXT    NOT NULL DEFAULT 'explicit',
    source_message_id INTEGER,
    changed_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Search history ──
CREATE TABLE IF NOT EXISTS searches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT    NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    query_text       TEXT,               -- optional original query text (redacted)
    filters_json     TEXT    NOT NULL,   -- JSON-serialised normalised search filters
    result_count     INTEGER NOT NULL DEFAULT 0,
    listing_ids_json TEXT,               -- JSON array of matched listing IDs
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Favourites ──
CREATE TABLE IF NOT EXISTS favourites (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT    NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    listing_id       INTEGER NOT NULL REFERENCES listings(listing_id) ON DELETE CASCADE,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, listing_id)
);

-- ── Sessions ──
CREATE TABLE IF NOT EXISTS sessions (
    session_id       TEXT    PRIMARY KEY,
    user_id          TEXT    NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    last_result_ids  TEXT,               -- JSON array of listing IDs from last search
    current_vehicle  INTEGER REFERENCES listings(listing_id) ON DELETE SET NULL,
    active_filters   TEXT,               -- JSON object of current filters
    booking_draft    TEXT,               -- JSON object of in-progress booking
    context_summary  TEXT,               -- recent conversation summary
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Messages (conversation history) ──
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT    NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    role             TEXT    NOT NULL,   -- 'user', 'assistant', 'system'
    content          TEXT    NOT NULL,
    vehicle_cards    TEXT,               -- JSON array of listing IDs to render as cards
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Bookings ──
CREATE TABLE IF NOT EXISTS bookings (
    booking_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT    NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    session_id       TEXT,
    listing_id       INTEGER NOT NULL REFERENCES listings(listing_id) ON DELETE CASCADE,
    booking_date     TEXT    NOT NULL,   -- ISO date YYYY-MM-DD
    start_time       TEXT    NOT NULL,   -- HH:MM
    end_time         TEXT    NOT NULL,   -- HH:MM
    duration_minutes INTEGER NOT NULL DEFAULT 60,
    status           TEXT    NOT NULL DEFAULT 'proposed',  -- 'proposed', 'confirmed', 'cancelled'
    timezone         TEXT    NOT NULL DEFAULT 'Asia/Dubai',
    customer_name    TEXT,
    customer_phone   TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    confirmed_at     TEXT
);

-- Prevent double-booking same vehicle at same time only when confirmed
CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_confirmed_slot
    ON bookings(listing_id, booking_date, start_time)
    WHERE status = 'confirmed';

-- Prevent same user confirming multiple bookings at the exact same slot
CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_user_confirmed_slot
    ON bookings(user_id, booking_date, start_time)
    WHERE status = 'confirmed';

-- ── Leads ──
CREATE TABLE IF NOT EXISTS leads (
    lead_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT    NOT NULL UNIQUE REFERENCES users(user_id) ON DELETE CASCADE,
    name                TEXT,
    phone               TEXT,
    budget              REAL,
    currency            TEXT    DEFAULT 'AED',
    original_budget     REAL,
    original_currency   TEXT    DEFAULT 'AED',
    preferred_makes     TEXT,               -- JSON array
    preferred_models    TEXT,               -- JSON array
    preferred_body_type TEXT,
    must_have_features  TEXT,               -- JSON array
    purchase_timeline   TEXT,
    financing_interest  TEXT,
    listing_ids         TEXT,               -- JSON array of listing IDs of interest
    booking_id          INTEGER REFERENCES bookings(booking_id) ON DELETE SET NULL,
    status              TEXT    NOT NULL DEFAULT 'cold',  -- 'hot', 'warm', 'cold'
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Agent traces ──
CREATE TABLE IF NOT EXISTS agent_traces (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id         TEXT    NOT NULL,
    user_id            TEXT,
    user_message       TEXT,
    tool_names         TEXT,               -- JSON array of tool names
    tool_calls_json    TEXT,               -- JSON array of redacted tool calls/results
    execution_ms       INTEGER,
    llm_error          TEXT,
    assistant_response TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Chat turn idempotency ──
CREATE TABLE IF NOT EXISTS chat_requests (
    request_id         TEXT    PRIMARY KEY,
    session_id         TEXT    NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    user_id            TEXT    NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    response_json      TEXT    NOT NULL,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Indexes ──
CREATE INDEX IF NOT EXISTS idx_listings_make ON listings(make);
CREATE INDEX IF NOT EXISTS idx_listings_model ON listings(model);
CREATE INDEX IF NOT EXISTS idx_listings_year ON listings(year);
CREATE INDEX IF NOT EXISTS idx_listings_duplicate_group ON listings(duplicate_group);
CREATE INDEX IF NOT EXISTS idx_listing_attrs_listing ON listing_attributes(listing_id);
CREATE INDEX IF NOT EXISTS idx_listing_attrs_name ON listing_attributes(attribute_name);
CREATE INDEX IF NOT EXISTS idx_preferences_user ON preferences(user_id);
CREATE INDEX IF NOT EXISTS idx_pref_history_user ON preference_history(user_id);
CREATE INDEX IF NOT EXISTS idx_searches_user ON searches(user_id);
CREATE INDEX IF NOT EXISTS idx_favourites_user ON favourites(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_bookings_user ON bookings(user_id);
CREATE INDEX IF NOT EXISTS idx_bookings_listing ON bookings(listing_id);
CREATE INDEX IF NOT EXISTS idx_bookings_date ON bookings(booking_date, start_time);
CREATE INDEX IF NOT EXISTS idx_leads_user ON leads(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_traces_session ON agent_traces(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_requests_session ON chat_requests(session_id);
"""

FTS_SQL = """
-- FTS5 virtual table for full-text search over listings
CREATE VIRTUAL TABLE IF NOT EXISTS listings_fts USING fts5(
    title,
    description_display,
    make,
    model,
    trim,
    content='listings',
    content_rowid='listing_id',
    tokenize='unicode61 remove_diacritics 2'
);
"""

FTS_TRIGGER_SQL = """
-- Keep FTS index in sync with listings table
CREATE TRIGGER IF NOT EXISTS listings_fts_insert AFTER INSERT ON listings BEGIN
    INSERT INTO listings_fts(rowid, title, description_display, make, model, trim)
    VALUES (new.listing_id, new.title, new.description_display, new.make, new.model, new.trim);
END;

CREATE TRIGGER IF NOT EXISTS listings_fts_delete AFTER DELETE ON listings BEGIN
    INSERT INTO listings_fts(listings_fts, rowid, title, description_display, make, model, trim)
    VALUES ('delete', old.listing_id, old.title, old.description_display, old.make, old.model, old.trim);
END;

CREATE TRIGGER IF NOT EXISTS listings_fts_update AFTER UPDATE ON listings BEGIN
    INSERT INTO listings_fts(listings_fts, rowid, title, description_display, make, model, trim)
    VALUES ('delete', old.listing_id, old.title, old.description_display, old.make, old.model, old.trim);
    INSERT INTO listings_fts(rowid, title, description_display, make, model, trim)
    VALUES (new.listing_id, new.title, new.description_display, new.make, new.model, new.trim);
END;
"""


def _get_db_path() -> Path:
    """Return the resolved database file path, creating parent dirs if needed."""
    db_path = settings.database_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create a new SQLite connection with recommended pragmas.

    Args:
        db_path: Override the default database path (useful for testing).

    Returns:
        A sqlite3.Connection configured with row factory and foreign keys.
    """
    if db_path is None:
        db_path = _get_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def get_db(db_path: Path | str | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections with automatic commit/rollback.

    Usage:
        with get_db() as conn:
            conn.execute("SELECT ...")
    """
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Non-destructively add new columns to existing pre-Phase 6 databases."""
    pref_cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(preferences)").fetchall()
    }
    pref_additions = [
        ("source_type", "TEXT NOT NULL DEFAULT 'explicit'"),
        ("is_explicit", "INTEGER NOT NULL DEFAULT 1"),
        ("status", "TEXT NOT NULL DEFAULT 'confirmed'"),
        ("confidence", "REAL NOT NULL DEFAULT 1.0"),
        ("source_message_id", "INTEGER"),
        ("created_at", "TEXT"),
    ]
    for col_name, col_def in pref_additions:
        if col_name not in pref_cols:
            conn.execute(f"ALTER TABLE preferences ADD COLUMN {col_name} {col_def}")
    if "created_at" not in pref_cols:
        conn.execute("UPDATE preferences SET created_at = updated_at WHERE created_at IS NULL")

    search_cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(searches)").fetchall()
    }
    if "query_text" not in search_cols:
        conn.execute("ALTER TABLE searches ADD COLUMN query_text TEXT")
    if "listing_ids_json" not in search_cols:
        conn.execute("ALTER TABLE searches ADD COLUMN listing_ids_json TEXT")

    booking_cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(bookings)").fetchall()
    }
    for col_name, col_def in [
        ("session_id", "TEXT"),
        ("customer_name", "TEXT"),
        ("customer_phone", "TEXT"),
    ]:
        if col_name not in booking_cols:
            conn.execute(f"ALTER TABLE bookings ADD COLUMN {col_name} {col_def}")

    lead_cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(leads)").fetchall()
    }
    for col_name, col_def in [
        ("original_budget", "REAL"),
        ("original_currency", "TEXT DEFAULT 'AED'"),
        ("preferred_models", "TEXT"),
    ]:
        if col_name not in lead_cols:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col_name} {col_def}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_user_unique ON leads(user_id)")


def init_db(db_path: Path | str | None = None) -> None:
    """Create all tables, indexes, FTS5 tables, and triggers.

    Safe to call multiple times (uses IF NOT EXISTS and non-destructive column guards).
    """
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate_schema(conn)
        conn.executescript(FTS_SQL)
        conn.executescript(FTS_TRIGGER_SQL)


def reset_db(db_path: Path | str | None = None) -> None:
    """Drop and recreate all tables. USE ONLY IN TESTS."""
    if db_path is None:
        db_path = _get_db_path()

    # Close any connections, delete file and WAL/SHM sidecars, reinitialise
    path = Path(db_path)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    init_db(db_path)
