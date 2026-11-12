"""Automated tests for Excel-to-SQLite ingestion, idempotency, duplicates, and caching."""

from __future__ import annotations

import re
from pathlib import Path

from backend.db import get_db
from backend.inventory.extraction import verify_evidence
from backend.inventory.ingestion import ingest_inventory


def test_ingest_loads_all_100_listings_truthfully(temp_db: Path) -> None:
    report = ingest_inventory(db_path=temp_db)

    assert report.total_rows == 100
    assert report.inserted == 100
    assert report.duplicate_groups == 7
    assert report.duplicate_listings == 17

    with get_db(temp_db) as conn:
        total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        makes = conn.execute("SELECT COUNT(DISTINCT make) FROM listings").fetchone()[0]
        merc = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE make = 'mercedes-benz'"
        ).fetchone()[0]
        honda = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE make = 'honda'"
        ).fetchone()[0]
        porsche = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE make = 'porsche'"
        ).fetchone()[0]

    assert total == 100
    assert makes == 29
    assert merc == 17
    assert honda == 1
    assert porsche == 0


def test_ingest_is_idempotent_and_uses_cache(temp_db: Path) -> None:
    first = ingest_inventory(db_path=temp_db)
    assert first.inserted == 100
    assert first.cache_hits == 10  # 10 duplicate-content rows hit cache on first pass

    second = ingest_inventory(db_path=temp_db)
    assert second.inserted == 0
    assert second.updated == 0
    assert second.unchanged == 100
    assert second.cache_hits == 100

    with get_db(temp_db) as conn:
        count_listings = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        count_cache = conn.execute("SELECT COUNT(*) FROM extraction_cache").fetchone()[0]
    assert count_listings == 100
    assert count_cache == 90


def test_duplicate_groups_flagged_correctly(temp_db: Path) -> None:
    ingest_inventory(db_path=temp_db)

    with get_db(temp_db) as conn:
        groups = conn.execute(
            """
            SELECT duplicate_group, COUNT(*) as cnt
            FROM listings
            WHERE duplicate_group IS NOT NULL
            GROUP BY duplicate_group
            ORDER BY duplicate_group
            """
        ).fetchall()
        unique_cnt = conn.execute(
            "SELECT COUNT(*) FROM listings WHERE duplicate_group IS NULL"
        ).fetchone()[0]

    assert len(groups) == 7
    assert sum(r["cnt"] for r in groups) == 17
    assert unique_cnt == 83


def test_display_descriptions_strip_phones_urls_and_html(temp_db: Path) -> None:
    ingest_inventory(db_path=temp_db)

    with get_db(temp_db) as conn:
        rows = conn.execute(
            "SELECT listing_id, description_raw, description_display FROM listings"
        ).fetchall()

    for row in rows:
        disp = row["description_display"]
        assert "<br" not in disp.lower(), f"HTML <br> remained in listing {row['listing_id']}"
        assert not re.search(r"https?://|www\.", disp, re.I), (
            f"URL remained in listing {row['listing_id']}"
        )
        assert not re.search(r"\+971|00971|\b05\d[\s\-]?\d{3}[\s\-]?\d{4}\b", disp), (
            f"Phone remained in listing {row['listing_id']}"
        )
        # Ensure original raw description was preserved intact
        assert row["description_raw"]


def test_all_persisted_attributes_retain_verifiable_evidence(temp_db: Path) -> None:
    ingest_inventory(db_path=temp_db)

    with get_db(temp_db) as conn:
        rows = conn.execute(
            """
            SELECT l.listing_id, l.make, l.model, l.trim, l.year, l.title, l.description_raw,
                   a.attribute_name, a.evidence
            FROM listing_attributes a
            JOIN listings l ON l.listing_id = a.listing_id
            """
        ).fetchall()

    assert len(rows) > 300
    for row in rows:
        source = f"{row['make']} {row['model']} {row['trim']} {row['year']}\n{row['title']}\n{row['description_raw']}"
        assert verify_evidence(row["evidence"], source), (
            f"Invalid evidence on ID={row['listing_id']} {row['attribute_name']}: {row['evidence']!r}"
        )


def test_fts5_index_populated_by_triggers(temp_db: Path) -> None:
    ingest_inventory(db_path=temp_db)

    with get_db(temp_db) as conn:
        honda_rows = conn.execute(
            "SELECT rowid FROM listings_fts WHERE listings_fts MATCH 'honda'"
        ).fetchall()
        tova_rows = conn.execute(
            "SELECT rowid FROM listings_fts WHERE listings_fts MATCH 'tova'"
        ).fetchall()

    assert [r["rowid"] for r in honda_rows] == [87]
    assert [r["rowid"] for r in tova_rows] == [68, 69]
