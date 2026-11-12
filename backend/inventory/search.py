"""Inventory search service with structured filtering, FTS5 ranking, and transparency.

All business rules for search live here:
- Deterministic alias resolution (via aliases.py)
- Structured filters take strict priority over FTS5 ranking
- Only confirmed cash prices qualify for strict budget filtering
- Unknown values never satisfy positive filters
- Unknown-price exclusions are counted and reported honestly
- Duplicate postings are preserved and flagged transparently without inflating counts
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from backend.inventory.aliases import build_fts_query, normalize_filters
from backend.inventory.repository import InventoryRepository
from backend.models.entities import (
    ExtractionStatus,
    Listing,
    ListingAttribute,
    ListingLanguage,
    ListingSummary,
)
from backend.models.schemas import SearchFilters, SearchResult


def _count_distinct_listing_groups(rows: list[dict[str, Any]]) -> int:
    """Count ungrouped listings plus distinct suspected near-duplicate groups."""
    seen_groups: set[int] = set()
    count = 0
    for row in rows:
        grp = row.get("duplicate_group")
        if grp is None:
            count += 1
        elif grp not in seen_groups:
            seen_groups.add(grp)
            count += 1
    return count


def _attrs_to_lookup(attrs: list[ListingAttribute]) -> dict[str, ListingAttribute]:
    return {a.attribute_name: a for a in attrs}


def _build_match_reason(
    row: dict[str, Any],
    attr_lookup: dict[str, ListingAttribute],
    filters: SearchFilters,
    *,
    suspected_duplicate_ids: Optional[list[int]] = None,
) -> str:
    """Construct a grounded, explainable match-reason string from DB attributes."""
    reasons: list[str] = []

    if filters.make:
        reasons.append(f"Make: {row['make']}")
    if filters.model:
        reasons.append(f"Model: {row['model']}")
    if filters.min_year is not None or filters.max_year is not None:
        reasons.append(f"Year: {row['year']}")
    if filters.spec_region and "spec_region" in attr_lookup:
        reasons.append(f"Spec: {attr_lookup['spec_region'].attribute_value}")
    if filters.body_type and "body_type" in attr_lookup:
        bt = attr_lookup["body_type"]
        reasons.append(f"Body type: {bt.attribute_value} ({bt.status.value})")
    if filters.transmission and "transmission" in attr_lookup:
        reasons.append(f"Transmission: {attr_lookup['transmission'].attribute_value}")
    if filters.colour and "colour" in attr_lookup:
        reasons.append(f"Colour: {attr_lookup['colour'].attribute_value}")
    if filters.warranty is not None and "warranty" in attr_lookup:
        reasons.append(f"Warranty: {attr_lookup['warranty'].attribute_value}")
    if (filters.min_price is not None or filters.max_price is not None) and "cash_price" in attr_lookup:
        cp = attr_lookup["cash_price"]
        if cp.numeric_value is not None:
            reasons.append(f"Confirmed cash price: {int(round(cp.numeric_value)):,} AED")
    if (filters.min_mileage is not None or filters.max_mileage is not None) and "mileage_km" in attr_lookup:
        mk = attr_lookup["mileage_km"]
        if mk.numeric_value is not None:
            reasons.append(f"Mileage: {int(round(mk.numeric_value)):,} km")
    if filters.free_text:
        reasons.append(f"Text match: {filters.free_text}")

    if not reasons:
        reasons.append(f"{row['year']} {row['make']} {row['model']}")

    if row.get("duplicate_group") is not None and suspected_duplicate_ids:
        others = ", ".join(f"#{oid}" for oid in suspected_duplicate_ids)
        reasons.append(
            f"Suspected near-duplicate group #{row['duplicate_group']} (shares ad text with {others})"
        )

    return " | ".join(reasons)


def _row_to_summary(
    row: dict[str, Any],
    attrs: list[ListingAttribute],
    *,
    is_suspected_duplicate: bool = False,
    suspected_duplicate_ids: Optional[list[int]] = None,
    match_reason: Optional[str] = None,
) -> ListingSummary:
    lookup = _attrs_to_lookup(attrs)
    cash_attr = lookup.get("cash_price")
    monthly_attr = lookup.get("monthly_instalment")
    mileage_attr = lookup.get("mileage_km")
    spec_attr = lookup.get("spec_region")
    warranty_attr = lookup.get("warranty")
    body_attr = lookup.get("body_type")
    trans_attr = lookup.get("transmission")
    colour_attr = lookup.get("colour")

    return ListingSummary(
        listing_id=row["listing_id"],
        make=row["make"],
        model=row["model"],
        trim=row["trim"],
        year=row["year"],
        title=row["title"],
        photo_url=row["photo_url"],
        language=ListingLanguage(row["language"]),
        duplicate_group=row.get("duplicate_group"),
        is_suspected_duplicate=is_suspected_duplicate,
        suspected_duplicate_ids=suspected_duplicate_ids or [],
        cash_price=cash_attr.numeric_value if cash_attr else None,
        cash_price_status=cash_attr.status if cash_attr else None,
        monthly_instalment=monthly_attr.numeric_value if monthly_attr else None,
        mileage_km=mileage_attr.numeric_value if mileage_attr else None,
        mileage_km_status=mileage_attr.status if mileage_attr else None,
        spec_region=spec_attr.attribute_value if spec_attr else None,
        warranty=warranty_attr.attribute_value if warranty_attr else None,
        warranty_status=warranty_attr.status if warranty_attr else None,
        body_type=body_attr.attribute_value if body_attr else None,
        body_type_status=body_attr.status if body_attr else None,
        transmission=trans_attr.attribute_value if trans_attr else None,
        colour=colour_attr.attribute_value if colour_attr else None,
        match_reason=match_reason,
        fts_rank=row.get("fts_rank"),
    )


def _matches_colour_filter(
    colour_attr: Optional[ListingAttribute], target_colour: str
) -> bool:
    if (
        colour_attr is None
        or colour_attr.status != ExtractionStatus.CONFIRMED
        or not colour_attr.attribute_value
    ):
        return False
    val_low = colour_attr.attribute_value.strip().lower()
    col_low = target_colour.strip().lower()
    variants = [col_low]
    if col_low == "grey":
        variants.append("gray")
    elif col_low == "gray":
        variants.append("grey")
    return any(val_low == cv or val_low.endswith(f" {cv}") for cv in variants)


class InventorySearchService:
    """Service layer for searching and retrieving inventory listings."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.repo = InventoryRepository(db_path)

    def search_inventory(
        self,
        filters: SearchFilters,
        *,
        limit: Optional[int] = None,
    ) -> SearchResult:
        """Search inventory with hard structured filters and optional FTS5 ranking."""
        norm = normalize_filters(filters)
        fts_query = build_fts_query(norm.free_text) if norm.free_text else None

        has_attr_filters = any(
            [
                norm.min_price is not None,
                norm.max_price is not None,
                norm.min_mileage is not None,
                norm.max_mileage is not None,
                norm.colour is not None,
                norm.spec_region is not None,
                norm.body_type is not None,
                norm.transmission is not None,
                norm.warranty is not None,
            ]
        )

        excluded_unknown_price = 0
        excluded_unknown_attrs: dict[str, int] = {}

        if has_attr_filters:
            base_filters = norm.model_copy(
                update={
                    "min_price": None,
                    "max_price": None,
                    "min_mileage": None,
                    "max_mileage": None,
                    "colour": None,
                    "spec_region": None,
                    "body_type": None,
                    "transmission": None,
                    "warranty": None,
                }
            )
            base_rows = self.repo.query_listings(
                base_filters,
                fts_query=fts_query,
                apply_price_filter=False,
            )
            base_ids = [r["listing_id"] for r in base_rows]
            attrs_by_id = self.repo.get_attributes_for_listings(base_ids)

            matched_rows: list[dict[str, Any]] = []
            for r in base_rows:
                lid = r["listing_id"]
                lookup = _attrs_to_lookup(attrs_by_id.get(lid, []))

                cp = lookup.get("cash_price")
                price_active = norm.min_price is not None or norm.max_price is not None
                price_known = (
                    cp is not None
                    and cp.status == ExtractionStatus.CONFIRMED
                    and cp.numeric_value is not None
                )
                price_ok = True
                if norm.min_price is not None:
                    price_ok = price_ok and price_known and cp.numeric_value >= norm.min_price
                if norm.max_price is not None:
                    price_ok = price_ok and price_known and cp.numeric_value <= norm.max_price

                mk = lookup.get("mileage_km")
                mileage_active = norm.min_mileage is not None or norm.max_mileage is not None
                mileage_known = (
                    mk is not None
                    and mk.status == ExtractionStatus.CONFIRMED
                    and mk.numeric_value is not None
                )
                mileage_ok = True
                if norm.min_mileage is not None:
                    mileage_ok = mileage_ok and mileage_known and mk.numeric_value >= norm.min_mileage
                if norm.max_mileage is not None:
                    mileage_ok = mileage_ok and mileage_known and mk.numeric_value <= norm.max_mileage

                col = lookup.get("colour")
                colour_active = norm.colour is not None
                colour_known = (
                    col is not None
                    and col.status == ExtractionStatus.CONFIRMED
                    and bool(col.attribute_value)
                )
                colour_ok = (not colour_active) or _matches_colour_filter(col, norm.colour)

                sp = lookup.get("spec_region")
                spec_active = norm.spec_region is not None
                spec_known = (
                    sp is not None
                    and sp.status == ExtractionStatus.CONFIRMED
                    and bool(sp.attribute_value)
                )
                if not spec_active:
                    spec_ok = True
                elif not spec_known:
                    spec_ok = False
                elif norm.spec_region.strip().lower() == "non-gcc":
                    spec_ok = sp.attribute_value.strip().lower() != "gcc"
                else:
                    spec_ok = sp.attribute_value.strip().lower() == norm.spec_region.strip().lower()

                bt = lookup.get("body_type")
                body_active = norm.body_type is not None
                body_known = (
                    bt is not None
                    and bt.status in {ExtractionStatus.CONFIRMED, ExtractionStatus.INFERRED}
                    and bool(bt.attribute_value)
                )
                body_ok = (not body_active) or (
                    body_known
                    and bt.attribute_value.strip().lower() == norm.body_type.strip().lower()
                )

                tr = lookup.get("transmission")
                trans_active = norm.transmission is not None
                trans_known = (
                    tr is not None
                    and tr.status == ExtractionStatus.CONFIRMED
                    and bool(tr.attribute_value)
                )
                trans_ok = (not trans_active) or (
                    trans_known
                    and tr.attribute_value.strip().lower() == norm.transmission.strip().lower()
                )

                wr = lookup.get("warranty")
                warranty_active = norm.warranty is not None
                warranty_known = (
                    wr is not None
                    and wr.status == ExtractionStatus.CONFIRMED
                    and bool(wr.attribute_value)
                )
                if not warranty_active:
                    warranty_ok = True
                elif not warranty_known:
                    warranty_ok = False
                elif norm.warranty is True:
                    warranty_ok = wr.attribute_value.strip().lower() != "no"
                else:
                    warranty_ok = wr.attribute_value.strip().lower() == "no"

                checks = [
                    ("cash_price", price_active, price_known, price_ok),
                    ("mileage_km", mileage_active, mileage_known, mileage_ok),
                    ("colour", colour_active, colour_known, colour_ok),
                    ("spec_region", spec_active, spec_known, spec_ok),
                    ("body_type", body_active, body_known, body_ok),
                    ("transmission", trans_active, trans_known, trans_ok),
                    ("warranty", warranty_active, warranty_known, warranty_ok),
                ]
                for attr_key, is_active, is_known, _ in checks:
                    if is_active and not is_known:
                        others_ok = all(
                            other_ok for k, _, _, other_ok in checks if k != attr_key
                        )
                        if others_ok:
                            if attr_key == "cash_price":
                                excluded_unknown_price += 1
                            else:
                                excluded_unknown_attrs[attr_key] = (
                                    excluded_unknown_attrs.get(attr_key, 0) + 1
                                )

                if all(ok for _, _, _, ok in checks):
                    matched_rows.append(r)
        else:
            matched_rows = self.repo.query_listings(
                norm,
                fts_query=fts_query,
                apply_price_filter=False,
            )
            attrs_by_id = self.repo.get_attributes_for_listings(
                [r["listing_id"] for r in matched_rows]
            )

        if not matched_rows:
            msg = "No vehicles found matching your criteria."
            if (norm.min_price is not None or norm.max_price is not None) and excluded_unknown_price > 0:
                if norm.min_price is not None and norm.max_price is not None:
                    range_txt = f"between AED {norm.min_price:,.0f} and AED {norm.max_price:,.0f}"
                elif norm.max_price is not None:
                    range_txt = f"within AED {norm.max_price:,.0f}"
                else:
                    range_txt = f"from AED {norm.min_price:,.0f}"
                msg = (
                    f"No listings found {range_txt}. "
                    f"Another {excluded_unknown_price} matching listing(s) do not list a "
                    f"confirmed cash price and were excluded from the budget filter."
                )
            return SearchResult(
                results=[],
                total_matches=0,
                distinct_listing_groups=0,
                suspected_duplicate_groups=0,
                related_duplicate_listings=0,
                excluded_unknown_price=excluded_unknown_price,
                excluded_unknown_attributes=excluded_unknown_attrs,
                filters_applied=norm,
                message=msg,
            )

        group_ids = [r["duplicate_group"] for r in matched_rows if r.get("duplicate_group") is not None]
        dup_members_map = self.repo.get_duplicate_group_members(group_ids)

        # Keep first-seen member of each suspected duplicate group ahead of other
        # group members so repeated ad text does not crowd out distinct listings,
        # while preserving all records and marking all group members transparently.
        lead_items: list[ListingSummary] = []
        additional_group_items: list[ListingSummary] = []
        seen_groups: set[int] = set()

        for row in matched_rows:
            lid = row["listing_id"]
            grp = row.get("duplicate_group")
            attrs = attrs_by_id.get(lid, [])
            lookup = _attrs_to_lookup(attrs)

            if grp is None:
                is_suspected_dup = False
                suspected_others: list[int] = []
                is_first_in_group = True
            else:
                all_in_group = dup_members_map.get(grp, [lid])
                suspected_others = [oid for oid in all_in_group if oid != lid]
                is_suspected_dup = len(suspected_others) > 0
                is_first_in_group = grp not in seen_groups
                seen_groups.add(grp)

            reason = _build_match_reason(
                row,
                lookup,
                norm,
                suspected_duplicate_ids=suspected_others,
            )
            summary = _row_to_summary(
                row,
                attrs,
                is_suspected_duplicate=is_suspected_dup,
                suspected_duplicate_ids=suspected_others,
                match_reason=reason,
            )
            if is_first_in_group:
                lead_items.append(summary)
            else:
                additional_group_items.append(summary)

        ordered_results = lead_items + additional_group_items
        if limit is not None and limit > 0:
            ordered_results = ordered_results[:limit]

        total_listings = len(matched_rows)
        distinct_groups = _count_distinct_listing_groups(matched_rows)
        suspected_group_count = len(seen_groups)
        related_dup_count = total_listings - distinct_groups

        msg_parts = [f"Found {total_listings} matching listing(s)"]
        if suspected_group_count > 0:
            msg_parts[0] += (
                f" ({distinct_groups} distinct ad group(s); {suspected_group_count} suspected "
                f"near-duplicate group(s) with {related_dup_count} related listing(s) sharing ad text)"
            )
        if excluded_unknown_price > 0:
            msg_parts.append(
                f"Another {excluded_unknown_price} relevant listing(s) don't mention a confirmed cash price, so I excluded them from the budget filter"
            )
        message = ". ".join(msg_parts) + "."

        return SearchResult(
            results=ordered_results,
            total_matches=total_listings,
            distinct_listing_groups=distinct_groups,
            suspected_duplicate_groups=suspected_group_count,
            related_duplicate_listings=related_dup_count,
            excluded_unknown_price=excluded_unknown_price,
            excluded_unknown_attributes=excluded_unknown_attrs,
            filters_applied=norm,
            message=message,
        )

    def get_vehicle_details(self, listing_id: int) -> Optional[Listing]:
        """Return a complete Listing with all extracted attributes and evidence."""
        return self.repo.get_listing_by_id(listing_id)

    def get_vehicle_summaries(self, listing_ids: list[int]) -> list[ListingSummary]:
        """Fetch ListingSummary objects for specific IDs in their given order."""
        rows = self.repo.get_listings_by_ids(listing_ids)
        if not rows:
            return []
        attrs_by_id = self.repo.get_attributes_for_listings([r["listing_id"] for r in rows])
        group_ids = [r["duplicate_group"] for r in rows if r.get("duplicate_group") is not None]
        dup_members_map = self.repo.get_duplicate_group_members(group_ids)

        summaries: list[ListingSummary] = []
        for row in rows:
            lid = row["listing_id"]
            grp = row.get("duplicate_group")
            dup_others = (
                [oid for oid in dup_members_map.get(grp, []) if oid != lid]
                if grp is not None
                else []
            )
            summaries.append(
                _row_to_summary(
                    row,
                    attrs_by_id.get(lid, []),
                    is_suspected_duplicate=len(dup_others) > 0,
                    suspected_duplicate_ids=dup_others,
                )
            )
        return summaries

    def get_inventory_makes(self) -> list[dict[str, Any]]:
        """Return all available makes and counts."""
        return self.repo.get_make_counts()

    def get_inventory_count(self) -> int:
        """Return total number of ingested listings."""
        return self.repo.count_listings()

    def get_suggested_prompts(self) -> list[str]:
        """Generate up to 4 grounded suggested search prompts in a single DB query."""
        sig = self.repo.get_suggested_prompt_signals()
        makes = sig["makes"]
        if not makes:
            return []

        def _fmt_make(raw: str) -> str:
            special = {
                "mercedes-benz": "Mercedes-Benz",
                "bmw": "BMW",
                "gwm": "GWM",
                "byd": "BYD",
                "jac": "JAC",
                "mg": "MG",
                "gmc": "GMC",
                "vw": "Volkswagen",
                "land rover": "Land Rover",
                "rolls-royce": "Rolls-Royce",
                "tova": "TOVA",
            }
            low = raw.strip().lower()
            return special.get(low, " ".join(p.capitalize() for p in low.replace("-", " ").split()))

        prompts: list[str] = []
        if sig["has_gcc_suv"]:
            prompts.append("Find GCC-spec SUVs.")
        top_make = makes[0]["make"]
        if sig["has_top_make_under_150k"]:
            prompts.append(f"Show me {_fmt_make(top_make)} under AED 150,000.")
        elif sig["has_any_under_150k"]:
            prompts.append("Show me cars under AED 150,000.")
        if sig["has_low_km"]:
            prompts.append("Find cars with low mileage.")

        make_names = [m["make"] for m in makes if m.get("count", 0) > 0]
        if "toyota" in make_names:
            prompts.append("Show me available Toyota vehicles.")
        elif len(make_names) > 1:
            prompts.append(f"Show me available {_fmt_make(make_names[1])} vehicles.")
        elif make_names:
            prompts.append(f"Show me available {_fmt_make(make_names[0])} vehicles.")
        return prompts[:4]
