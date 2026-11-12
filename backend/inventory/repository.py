"""SQLite repository layer for vehicle inventory and FTS5 queries.

Responsible solely for database reads/writes and FTS5 operations.
Contains no business rules, alias resolution, or ranking interpretation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from backend.db import get_db
from backend.models.entities import (
    ExtractionMethod,
    ExtractionStatus,
    Listing,
    ListingAttribute,
    ListingLanguage,
)
from backend.models.schemas import SearchFilters


class InventoryRepository:
    """Repository for querying listings and extracted attributes from SQLite."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.db_path = db_path

    def query_listings(
        self,
        filters: SearchFilters,
        *,
        fts_query: Optional[str] = None,
        apply_price_filter: bool = True,
    ) -> list[dict[str, Any]]:
        """Execute structured filter query with optional FTS5 relevance scoring.

        Structured filters are always enforced in SQL WHERE clauses; FTS5 never
        overrides hard filters.
        """
        where_clauses: list[str] = []
        params: list[Any] = []

        if fts_query:
            select_fts = ", bm25(listings_fts) AS fts_rank"
            from_sql = "FROM listings l JOIN listings_fts ON listings_fts.rowid = l.listing_id"
            where_clauses.append("listings_fts MATCH ?")
            params.append(fts_query)
        else:
            select_fts = ", NULL AS fts_rank"
            from_sql = "FROM listings l"

        if filters.make:
            where_clauses.append("LOWER(l.make) = LOWER(?)")
            params.append(filters.make)

        if filters.model:
            model_norm = " ".join(filters.model.replace("-", " ").split())
            where_clauses.append(
                "(REPLACE(LOWER(l.model), '-', ' ') = LOWER(?) OR REPLACE(LOWER(l.model), '-', ' ') LIKE LOWER(?) || ' %')"
            )
            params.extend([model_norm, model_norm])

        if filters.min_year is not None:
            where_clauses.append("l.year >= ?")
            params.append(filters.min_year)

        if filters.max_year is not None:
            where_clauses.append("l.year <= ?")
            params.append(filters.max_year)

        if apply_price_filter and filters.min_price is not None:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM listing_attributes a
                    WHERE a.listing_id = l.listing_id
                      AND a.attribute_name = 'cash_price'
                      AND a.status = 'confirmed'
                      AND a.numeric_value >= ?
                )
                """
            )
            params.append(filters.min_price)

        if apply_price_filter and filters.max_price is not None:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM listing_attributes a
                    WHERE a.listing_id = l.listing_id
                      AND a.attribute_name = 'cash_price'
                      AND a.status = 'confirmed'
                      AND a.numeric_value <= ?
                )
                """
            )
            params.append(filters.max_price)

        if filters.min_mileage is not None:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM listing_attributes a
                    WHERE a.listing_id = l.listing_id
                      AND a.attribute_name = 'mileage_km'
                      AND a.status = 'confirmed'
                      AND a.numeric_value >= ?
                )
                """
            )
            params.append(filters.min_mileage)

        if filters.max_mileage is not None:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM listing_attributes a
                    WHERE a.listing_id = l.listing_id
                      AND a.attribute_name = 'mileage_km'
                      AND a.status = 'confirmed'
                      AND a.numeric_value <= ?
                )
                """
            )
            params.append(filters.max_mileage)

        if filters.spec_region:
            if filters.spec_region.strip().lower() == "non-gcc":
                where_clauses.append(
                    """
                    EXISTS (
                        SELECT 1 FROM listing_attributes a
                        WHERE a.listing_id = l.listing_id
                          AND a.attribute_name = 'spec_region'
                          AND a.status = 'confirmed'
                          AND LOWER(a.attribute_value) != 'gcc'
                    )
                    """
                )
            else:
                where_clauses.append(
                    """
                    EXISTS (
                        SELECT 1 FROM listing_attributes a
                        WHERE a.listing_id = l.listing_id
                          AND a.attribute_name = 'spec_region'
                          AND a.status = 'confirmed'
                          AND LOWER(a.attribute_value) = LOWER(?)
                    )
                    """
                )
                params.append(filters.spec_region)

        if filters.body_type:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM listing_attributes a
                    WHERE a.listing_id = l.listing_id
                      AND a.attribute_name = 'body_type'
                      AND a.status IN ('confirmed', 'inferred')
                      AND LOWER(a.attribute_value) = LOWER(?)
                )
                """
            )
            params.append(filters.body_type)

        if filters.transmission:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM listing_attributes a
                    WHERE a.listing_id = l.listing_id
                      AND a.attribute_name = 'transmission'
                      AND a.status = 'confirmed'
                      AND LOWER(a.attribute_value) = LOWER(?)
                )
                """
            )
            params.append(filters.transmission)

        if filters.colour:
            col_low = filters.colour.strip().lower()
            col_variants = [col_low]
            if col_low == "grey":
                col_variants.append("gray")
            elif col_low == "gray":
                col_variants.append("grey")
            col_conds = " OR ".join(
                "(LOWER(a.attribute_value) = ? OR LOWER(a.attribute_value) LIKE '% ' || ?)"
                for _ in col_variants
            )
            where_clauses.append(
                f"""
                EXISTS (
                    SELECT 1 FROM listing_attributes a
                    WHERE a.listing_id = l.listing_id
                      AND a.attribute_name = 'colour'
                      AND a.status = 'confirmed'
                      AND ({col_conds})
                )
                """
            )
            for cv in col_variants:
                params.extend([cv, cv])

        if filters.warranty is True:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM listing_attributes a
                    WHERE a.listing_id = l.listing_id
                      AND a.attribute_name = 'warranty'
                      AND a.status = 'confirmed'
                      AND LOWER(a.attribute_value) != 'no'
                )
                """
            )
        elif filters.warranty is False:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM listing_attributes a
                    WHERE a.listing_id = l.listing_id
                      AND a.attribute_name = 'warranty'
                      AND a.status = 'confirmed'
                      AND LOWER(a.attribute_value) = 'no'
                )
                """
            )

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        order_sql = (
            "ORDER BY fts_rank ASC, l.year DESC, l.listing_id ASC"
            if fts_query
            else "ORDER BY l.year DESC, l.listing_id ASC"
        )

        sql = f"""
            SELECT
                l.listing_id, l.make, l.model, l.trim, l.year,
                l.title, l.description_raw, l.description_display,
                l.photo_url, l.language, l.duplicate_group, l.content_hash
                {select_fts}
            {from_sql}
            {where_sql}
            {order_sql}
        """

        with get_db(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_attributes_for_listings(
        self, listing_ids: list[int]
    ) -> dict[int, list[ListingAttribute]]:
        """Batch-fetch extracted attributes for a list of listing IDs."""
        if not listing_ids:
            return {}
        placeholders = ",".join("?" for _ in listing_ids)
        sql = f"""
            SELECT listing_id, attribute_name, attribute_value, numeric_value,
                   evidence, extraction_method, status, confidence
            FROM listing_attributes
            WHERE listing_id IN ({placeholders})
            ORDER BY listing_id, id
        """
        result: dict[int, list[ListingAttribute]] = {lid: [] for lid in listing_ids}
        with get_db(self.db_path) as conn:
            rows = conn.execute(sql, listing_ids).fetchall()
            for r in rows:
                result[r["listing_id"]].append(
                    ListingAttribute(
                        attribute_name=r["attribute_name"],
                        attribute_value=r["attribute_value"],
                        numeric_value=r["numeric_value"],
                        evidence=r["evidence"],
                        extraction_method=ExtractionMethod(r["extraction_method"]),
                        status=ExtractionStatus(r["status"]),
                        confidence=r["confidence"],
                    )
                )
        return result

    def get_duplicate_group_members(self, group_ids: list[int]) -> dict[int, list[int]]:
        """Return all listing_ids belonging to each requested duplicate_group."""
        clean_groups = sorted({g for g in group_ids if g is not None})
        if not clean_groups:
            return {}
        placeholders = ",".join("?" for _ in clean_groups)
        sql = f"""
            SELECT duplicate_group, listing_id
            FROM listings
            WHERE duplicate_group IN ({placeholders})
            ORDER BY duplicate_group, listing_id
        """
        mapping: dict[int, list[int]] = {g: [] for g in clean_groups}
        with get_db(self.db_path) as conn:
            for r in conn.execute(sql, clean_groups).fetchall():
                mapping[r["duplicate_group"]].append(r["listing_id"])
        return mapping

    def get_listing_by_id(self, listing_id: int) -> Optional[Listing]:
        """Fetch a full Listing with all extracted attributes by listing_id."""
        with get_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT listing_id, make, model, trim, year, title,
                       description_raw, description_display, photo_url,
                       language, duplicate_group, content_hash
                FROM listings
                WHERE listing_id = ?
                """,
                (listing_id,),
            ).fetchone()
        if row is None:
            return None

        attrs_map = self.get_attributes_for_listings([listing_id])
        return Listing(
            listing_id=row["listing_id"],
            make=row["make"],
            model=row["model"],
            trim=row["trim"],
            year=row["year"],
            title=row["title"],
            description_raw=row["description_raw"],
            description_display=row["description_display"],
            photo_url=row["photo_url"],
            language=ListingLanguage(row["language"]),
            duplicate_group=row["duplicate_group"],
            content_hash=row["content_hash"],
            attributes=attrs_map.get(listing_id, []),
        )

    def get_listings_by_ids(self, listing_ids: list[int]) -> list[dict[str, Any]]:
        """Fetch listing rows for specific IDs, preserving the requested order."""
        if not listing_ids:
            return []
        placeholders = ",".join("?" for _ in listing_ids)
        sql = f"""
            SELECT listing_id, make, model, trim, year, title,
                   description_raw, description_display, photo_url,
                   language, duplicate_group, content_hash
            FROM listings
            WHERE listing_id IN ({placeholders})
        """
        with get_db(self.db_path) as conn:
            by_id = {r["listing_id"]: dict(r) for r in conn.execute(sql, listing_ids).fetchall()}
        return [by_id[lid] for lid in listing_ids if lid in by_id]

    def get_make_counts(self) -> list[dict[str, Any]]:
        """Return make counts ordered by popularity."""
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT make, COUNT(*) AS count
                FROM listings
                GROUP BY make
                ORDER BY count DESC, make ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def count_listings(self) -> int:
        """Return total number of listings currently stored in SQLite."""
        with get_db(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM listings").fetchone()
        return int(row["cnt"]) if row is not None else 0

    def get_suggested_prompt_signals(self) -> dict[str, Any]:
        """Compute inventory availability flags for suggested prompts in one DB connection."""
        with get_db(self.db_path) as conn:
            make_rows = conn.execute(
                """
                SELECT make, COUNT(*) AS count
                FROM listings
                GROUP BY make
                ORDER BY count DESC, make ASC
                """
            ).fetchall()
            makes = [dict(r) for r in make_rows]
            if not makes:
                return {
                    "makes": [],
                    "has_gcc_suv": False,
                    "has_top_make_under_150k": False,
                    "has_any_under_150k": False,
                    "has_low_km": False,
                }
            top_make = makes[0]["make"]
            flags = conn.execute(
                """
                SELECT
                    EXISTS (
                        SELECT 1 FROM listings l
                        WHERE EXISTS (
                            SELECT 1 FROM listing_attributes a1
                            WHERE a1.listing_id = l.listing_id
                              AND a1.attribute_name = 'spec_region'
                              AND a1.status = 'confirmed'
                              AND LOWER(a1.attribute_value) = 'gcc'
                        )
                        AND EXISTS (
                            SELECT 1 FROM listing_attributes a2
                            WHERE a2.listing_id = l.listing_id
                              AND a2.attribute_name = 'body_type'
                              AND a2.status IN ('confirmed', 'inferred')
                              AND LOWER(a2.attribute_value) = 'suv'
                        )
                    ) AS has_gcc_suv,
                    EXISTS (
                        SELECT 1 FROM listings l
                        JOIN listing_attributes a ON a.listing_id = l.listing_id
                        WHERE LOWER(l.make) = LOWER(?)
                          AND a.attribute_name = 'cash_price'
                          AND a.status = 'confirmed'
                          AND a.numeric_value <= 150000
                    ) AS has_top_make_under_150k,
                    EXISTS (
                        SELECT 1 FROM listing_attributes a
                        WHERE a.attribute_name = 'cash_price'
                          AND a.status = 'confirmed'
                          AND a.numeric_value <= 150000
                    ) AS has_any_under_150k,
                    EXISTS (
                        SELECT 1 FROM listing_attributes a
                        WHERE a.attribute_name = 'mileage_km'
                          AND a.status = 'confirmed'
                          AND a.numeric_value <= 50000
                    ) AS has_low_km
                """,
                (top_make,),
            ).fetchone()
        return {
            "makes": makes,
            "has_gcc_suv": bool(flags["has_gcc_suv"]) if flags else False,
            "has_top_make_under_150k": bool(flags["has_top_make_under_150k"]) if flags else False,
            "has_any_under_150k": bool(flags["has_any_under_150k"]) if flags else False,
            "has_low_km": bool(flags["has_low_km"]) if flags else False,
        }
