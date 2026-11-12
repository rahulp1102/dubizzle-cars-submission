"""Idempotent Excel-to-SQLite inventory ingestion pipeline.

Reads the authoritative `raw dataset` sheet without ever modifying it, detects
duplicate posting groups, builds display-safe descriptions, runs/caches attribute
extraction, and persists all 100 listings into SQLite + FTS5.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

import pandas as pd
from pydantic import BaseModel

from backend.config import settings
from backend.db import get_db, init_db
from backend.inventory.extraction import (
    LLMExtractor,
    compute_content_hash,
    detect_language,
    extract_attributes,
    normalize_for_matching,
    sanitize_display_description,
)
from backend.models.entities import ListingAttribute


class IngestionReport(BaseModel):
    """Summary statistics produced by an ingestion run."""

    total_rows: int
    inserted: int
    updated: int
    unchanged: int
    cache_hits: int
    duplicate_groups: int
    duplicate_listings: int
    attribute_totals: dict[str, int]


def _detect_duplicate_groups(rows: list[tuple[int, str, str]]) -> dict[int, Optional[int]]:
    """Detect duplicate groups across listings matching by title or description.

    Args:
        rows: List of (listing_id, title, description_raw).

    Returns:
        Mapping from listing_id to integer duplicate_group (1..N) or None if unique.
    """
    parent: dict[int, int] = {lid: lid for lid, _, _ in rows}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            # Anchor on smaller listing_id for determinism
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    by_title: dict[str, int] = {}
    by_desc: dict[str, int] = {}

    for lid, title, desc in rows:
        t_norm = " ".join(normalize_for_matching(title).lower().split())
        d_norm = " ".join(normalize_for_matching(desc).lower().split())
        if t_norm:
            if t_norm in by_title:
                union(by_title[t_norm], lid)
            else:
                by_title[t_norm] = lid
        if len(d_norm) > 10:
            if d_norm in by_desc:
                union(by_desc[d_norm], lid)
            else:
                by_desc[d_norm] = lid

    clusters: dict[int, list[int]] = {}
    for lid, _, _ in rows:
        root = find(lid)
        clusters.setdefault(root, []).append(lid)

    group_map: dict[int, Optional[int]] = {}
    group_counter = 0
    for root in sorted(clusters):
        members = clusters[root]
        if len(members) > 1:
            group_counter += 1
            for lid in members:
                group_map[lid] = group_counter
        else:
            group_map[members[0]] = None

    return group_map


def _load_from_cache(conn: sqlite3.Connection, content_hash: str) -> Optional[list[ListingAttribute]]:
    row = conn.execute(
        "SELECT attributes_json FROM extraction_cache WHERE content_hash = ?",
        (content_hash,),
    ).fetchone()
    if row is None:
        return None
    raw_list = json.loads(row["attributes_json"])
    return [ListingAttribute.model_validate(item) for item in raw_list]


def _save_to_cache(
    conn: sqlite3.Connection,
    content_hash: str,
    attributes: list[ListingAttribute],
    source: str,
) -> None:
    payload = json.dumps([attr.model_dump(mode="json") for attr in attributes])
    conn.execute(
        """
        INSERT INTO extraction_cache (content_hash, attributes_json, extraction_source)
        VALUES (?, ?, ?)
        ON CONFLICT(content_hash) DO UPDATE SET
            attributes_json = excluded.attributes_json,
            extraction_source = excluded.extraction_source,
            created_at = datetime('now')
        """,
        (content_hash, payload, source),
    )


def ingest_inventory(
    *,
    dataset_path: Optional[Path] = None,
    sheet_name: Optional[str] = None,
    db_path: Optional[Path] = None,
    force_reextract: bool = False,
    llm_extractor: Optional[LLMExtractor] = None,
) -> IngestionReport:
    """Ingest all rows from the authoritative Excel sheet into SQLite.

    Idempotent: re-running updates changed records without creating duplicates.
    """
    xlsx_path = dataset_path or settings.DATASET_PATH
    sheet = sheet_name or settings.DATASET_SHEET

    init_db(db_path)

    df = pd.read_excel(xlsx_path, sheet_name=sheet)
    raw_records: list[tuple[int, str, str]] = []
    parsed_rows: list[dict] = []

    for idx, row in df.iterrows():
        listing_id = int(idx) + 1
        make = str(row["make"]).strip().lower()
        model = str(row["model"]).strip().lower()
        trim = str(row["trim"]).strip().lower()
        year = int(row["year"])
        title = str(row["title"]).strip()
        description_raw = str(row["description"])
        photo_url = str(row["photo_url"]).strip()

        raw_records.append((listing_id, title, description_raw))
        parsed_rows.append(
            {
                "listing_id": listing_id,
                "make": make,
                "model": model,
                "trim": trim,
                "year": year,
                "title": title,
                "description_raw": description_raw,
                "description_display": sanitize_display_description(description_raw),
                "photo_url": photo_url,
                "language": detect_language(title, description_raw).value,
                "content_hash": compute_content_hash(title, description_raw),
            }
        )

    dup_map = _detect_duplicate_groups(raw_records)

    inserted = 0
    updated = 0
    unchanged = 0
    cache_hits = 0
    attr_totals: dict[str, int] = {}

    with get_db(db_path) as conn:
        for item in parsed_rows:
            lid = item["listing_id"]
            dup_group = dup_map[lid]
            item["duplicate_group"] = dup_group

            existing = conn.execute(
                "SELECT content_hash, duplicate_group FROM listings WHERE listing_id = ?",
                (lid,),
            ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO listings (
                        listing_id, make, model, trim, year, title,
                        description_raw, description_display, photo_url,
                        language, duplicate_group, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lid,
                        item["make"],
                        item["model"],
                        item["trim"],
                        item["year"],
                        item["title"],
                        item["description_raw"],
                        item["description_display"],
                        item["photo_url"],
                        item["language"],
                        dup_group,
                        item["content_hash"],
                    ),
                )
                inserted += 1
            elif (
                existing["content_hash"] != item["content_hash"]
                or existing["duplicate_group"] != dup_group
                or force_reextract
            ):
                conn.execute(
                    """
                    UPDATE listings SET
                        make = ?, model = ?, trim = ?, year = ?, title = ?,
                        description_raw = ?, description_display = ?, photo_url = ?,
                        language = ?, duplicate_group = ?, content_hash = ?
                    WHERE listing_id = ?
                    """,
                    (
                        item["make"],
                        item["model"],
                        item["trim"],
                        item["year"],
                        item["title"],
                        item["description_raw"],
                        item["description_display"],
                        item["photo_url"],
                        item["language"],
                        dup_group,
                        item["content_hash"],
                        lid,
                    ),
                )
                updated += 1
            else:
                unchanged += 1

            # Extract or load cached attributes
            attributes: Optional[list[ListingAttribute]] = None
            if not force_reextract:
                attributes = _load_from_cache(conn, item["content_hash"])
                if attributes is not None:
                    cache_hits += 1

            if attributes is None:
                attributes = extract_attributes(
                    make=item["make"],
                    model=item["model"],
                    trim=item["trim"],
                    year=item["year"],
                    title=item["title"],
                    description_raw=item["description_raw"],
                    llm_extractor=llm_extractor,
                )
                source = "gemini+rules" if any(a.extraction_method.value == "gemini" for a in attributes) else "rules"
                _save_to_cache(conn, item["content_hash"], attributes, source)

            # Upsert attributes idempotently
            conn.execute("DELETE FROM listing_attributes WHERE listing_id = ?", (lid,))
            for attr in attributes:
                conn.execute(
                    """
                    INSERT INTO listing_attributes (
                        listing_id, attribute_name, attribute_value, numeric_value,
                        evidence, extraction_method, status, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lid,
                        attr.attribute_name,
                        attr.attribute_value,
                        attr.numeric_value,
                        attr.evidence,
                        attr.extraction_method.value,
                        attr.status.value,
                        attr.confidence,
                    ),
                )
                key = f"{attr.attribute_name}:{attr.status.value}"
                attr_totals[key] = attr_totals.get(key, 0) + 1

    dup_groups = len({g for g in dup_map.values() if g is not None})
    dup_listings = sum(1 for g in dup_map.values() if g is not None)

    return IngestionReport(
        total_rows=len(parsed_rows),
        inserted=inserted,
        updated=updated,
        unchanged=unchanged,
        cache_hits=cache_hits,
        duplicate_groups=dup_groups,
        duplicate_listings=dup_listings,
        attribute_totals=dict(sorted(attr_totals.items())),
    )
