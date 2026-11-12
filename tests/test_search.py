"""Automated offline tests for inventory search service, FTS5 ranking, aliases, and duplicate transparency."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.db import get_db
from backend.inventory.aliases import (
    build_fts_query,
    normalize_filters,
    resolve_make,
    resolve_model,
)
from backend.inventory.ingestion import ingest_inventory
from backend.inventory.search import InventorySearchService
from backend.models.schemas import SearchFilters


@pytest.fixture()
def seeded_db(temp_db: Path) -> Path:
    """Ingest the authoritative 100-listing dataset once per test."""
    ingest_inventory(db_path=temp_db)
    return temp_db


def test_search_make_filtering(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(SearchFilters(make="mercedes-benz"))
    assert res.total_matches == 17
    assert res.distinct_listing_groups == 17
    assert res.suspected_duplicate_groups == 0
    assert all(item.make == "mercedes-benz" for item in res.results)

    ferrari = svc.search_inventory(SearchFilters(make="ferrari"))
    assert ferrari.total_matches == 2


def test_search_model_filtering_and_subvariants(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    rr = svc.search_inventory(SearchFilters(make="land rover", model="range rover"))
    models = {item.model for item in rr.results}
    assert "range rover" in models
    assert "range rover sport" in models
    assert "range rover evoque" in models

    c_class = svc.search_inventory(SearchFilters(make="mercedes-benz", model="c-class"))
    assert all(item.model == "c-class" for item in c_class.results)
    assert len(c_class.results) == 4


def test_search_year_range(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(SearchFilters(min_year=2024, max_year=2025))
    assert res.total_matches > 0
    assert all(2024 <= item.year <= 2025 for item in res.results)


def test_search_mileage_filtering_excludes_unknown(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(SearchFilters(min_mileage=0, max_mileage=20000))
    assert res.total_matches > 0
    for item in res.results:
        assert item.mileage_km is not None
        assert 0 <= item.mileage_km <= 20000
    assert res.excluded_unknown_attributes.get("mileage_km", 0) > 0


def test_search_confirmed_price_filtering_and_unknown_price_count(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(SearchFilters(make="mercedes-benz", max_price=120000))
    assert res.total_matches >= 2
    for item in res.results:
        assert item.cash_price is not None
        assert item.cash_price <= 120000
        assert item.cash_price_status is not None and item.cash_price_status.value == "confirmed"
    assert res.excluded_unknown_price > 0
    assert "excluded" in (res.message or "").lower()


def test_inferred_cash_price_excluded_from_strict_budget_filter(seeded_db: Path) -> None:
    # Insert an inferred cash price on listing 1 (Ferrari F430, normally unpriced)
    with get_db(seeded_db) as conn:
        conn.execute(
            """
            INSERT INTO listing_attributes (
                listing_id, attribute_name, attribute_value, numeric_value,
                evidence, extraction_method, status, confidence
            ) VALUES (1, 'cash_price', '90,000 AED', 90000.0, 'F430', 'gemini', 'inferred', 0.5)
            """
        )

    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(SearchFilters(make="ferrari", max_price=100000))
    assert res.total_matches == 0
    assert res.results == []
    assert res.excluded_unknown_price == 2  # both Ferraris lack CONFIRMED cash price


def test_unknown_values_never_satisfy_positive_filters(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    # Arabic listing (ID=4 Chevrolet Malibu) has unknown spec_region and transmission
    res_spec = svc.search_inventory(SearchFilters(make="chevrolet", spec_region="GCC"))
    assert all(item.listing_id != 4 for item in res_spec.results)

    res_trans = svc.search_inventory(SearchFilters(make="chevrolet", transmission="Automatic"))
    assert all(item.listing_id != 4 for item in res_trans.results)


def test_alias_resolution_makes_models_and_specs(seeded_db: Path) -> None:
    assert resolve_make("merc") == "mercedes-benz"
    assert resolve_make("benz") == "mercedes-benz"
    assert resolve_make("mb") == "mercedes-benz"
    assert resolve_make("rr") == "rolls-royce"
    assert resolve_make("beemer") == "bmw"
    assert resolve_make("vw") == "volkswagen"
    assert resolve_model("crv") == "cr-v"
    assert resolve_model("evoque") == "range rover evoque"

    norm = normalize_filters(SearchFilters(make="merc", spec_region="gulf", body_type="4x4", transmission="auto"))
    assert norm.make == "mercedes-benz"
    assert norm.spec_region == "GCC"
    assert norm.body_type == "SUV"
    assert norm.transmission == "Automatic"

    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(SearchFilters(make="merc", spec_region="gulf"))
    assert res.total_matches > 0
    assert all(item.make == "mercedes-benz" and item.spec_region == "GCC" for item in res.results)


def test_fts5_ranking_never_overrides_hard_filters(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    # "panoramic" matches luxury cars > 200k and non-Mercedes cars too;
    # hard filter make='mercedes-benz' + max_price=120000 must still hold strictly!
    res = svc.search_inventory(
        SearchFilters(make="mercedes-benz", max_price=120000, free_text="panoramic")
    )
    for item in res.results:
        assert item.make == "mercedes-benz"
        assert item.cash_price is not None and item.cash_price <= 120000

    # FTS with incompatible hard filter must return 0
    impossible = svc.search_inventory(SearchFilters(make="honda", free_text="Ferrari"))
    assert impossible.total_matches == 0
    assert impossible.results == []


def test_fts5_punctuation_safety(seeded_db: Path) -> None:
    q = build_fts_query("merc c-class (c200)?")
    assert q is not None
    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(SearchFilters(free_text="merc c-class (c200)?"))
    assert res.total_matches >= 1
    assert any(item.make == "mercedes-benz" for item in res.results)


def test_honda_returns_actual_crv_listing(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    by_make = svc.search_inventory(SearchFilters(make="honda"))
    assert by_make.total_matches == 1
    assert len(by_make.results) == 1
    crv = by_make.results[0]
    assert crv.listing_id == 87
    assert crv.make == "honda"
    assert crv.model == "cr-v"
    assert crv.year == 2021

    by_fts = svc.search_inventory(SearchFilters(free_text="Honda"))
    assert by_fts.total_matches == 1
    assert by_fts.results[0].listing_id == 87


def test_porsche_returns_zero_results_gracefully(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    by_make = svc.search_inventory(SearchFilters(make="porsche"))
    assert by_make.total_matches == 0
    assert by_make.results == []
    assert by_make.message is not None and "no vehicles" in by_make.message.lower()

    by_fts = svc.search_inventory(SearchFilters(free_text="Porsche 911"))
    assert by_fts.total_matches == 0
    assert by_fts.results == []


def test_duplicate_transparency_does_not_inflate_counts(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(SearchFilters(make="audi", model="q8"))
    # 5 similar Q8 listings in suspected near-duplicate group 1:
    # Preserve all 5 listings; separate listing count (5), distinct group count (1),
    # suspected duplicate group count (1), and related listing count (4)
    assert res.total_matches == 5
    assert res.distinct_listing_groups == 1
    assert res.suspected_duplicate_groups == 1
    assert res.related_duplicate_listings == 4
    assert len(res.results) == 5

    for item in res.results:
        assert item.is_suspected_duplicate is True
        assert item.duplicate_group is not None
        assert len(item.suspected_duplicate_ids) == 4
        assert "suspected near-duplicate" in (item.match_reason or "").lower()


def test_duplicate_primaries_ordered_before_duplicate_copies(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(SearchFilters(make="audi"))
    # 8 Audi listings across 4 distinct ad groups (Q8 x5, RS Q3 x1, RS7 x1, A3 x1)
    assert res.total_matches == 8
    assert res.distinct_listing_groups == 4
    assert res.suspected_duplicate_groups == 1
    assert res.related_duplicate_listings == 4
    first_four = res.results[:4]
    assert len({item.model for item in first_four}) == 4


def test_match_reasons_are_grounded_and_explainable(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(
        SearchFilters(make="merc", spec_region="GCC", max_mileage=50000)
    )
    assert res.total_matches > 0
    for item in res.results:
        assert item.match_reason
        assert "mercedes-benz" in item.match_reason
        assert "GCC" in item.match_reason
        assert "km" in item.match_reason


def test_warranty_filter_excludes_inferred_mentions(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(SearchFilters(make="bmw", warranty=True))
    ids = {item.listing_id for item in res.results}
    # ID=23 (confirmed BMW Warranty) and ID=37 (5 Years Warranty) should match;
    # ID=26 ("Warranty And Service Contract Available", inferred) must NOT satisfy confirmed warranty=True
    assert 23 in ids
    assert 37 in ids
    assert 26 not in ids


def test_commercial_tova_j14_searchable_as_commercial(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    res = svc.search_inventory(SearchFilters(body_type="commercial"))
    assert res.total_matches == 2
    assert res.distinct_listing_groups == 1
    assert res.suspected_duplicate_groups == 1
    assert res.related_duplicate_listings == 1
    assert {item.listing_id for item in res.results} == {68, 69}


def test_get_vehicle_details_and_summaries(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)
    detail = svc.get_vehicle_details(87)
    assert detail is not None
    assert detail.make == "honda"
    assert detail.model == "cr-v"
    assert "052 996 5849" in detail.description_raw
    assert "052 996 5849" not in detail.description_display
    assert any(a.attribute_name == "mileage_km" and a.numeric_value == 79000.0 for a in detail.attributes)

    assert svc.get_vehicle_details(9999) is None

    summaries = svc.get_vehicle_summaries([87, 1])
    assert [s.listing_id for s in summaries] == [87, 1]


def test_structured_colour_filtering_strict_and_suggested_prompts(seeded_db: Path) -> None:
    svc = InventorySearchService(seeded_db)

    # White SUVs under AED 80,000 -> must return ONLY confirmed White + SUV + cash_price <= 80000
    res = svc.search_inventory(SearchFilters(colour="white", body_type="SUV", max_price=80000))
    for item in res.results:
        assert item.colour is not None and "white" in item.colour.lower()
        assert item.body_type == "SUV"
        assert item.cash_price is not None and item.cash_price <= 80000

    # Verify unknown-colour listings are NEVER returned when filtering by colour, and excluded count is tracked
    red_res = svc.search_inventory(SearchFilters(colour="red"))
    assert red_res.total_matches > 0
    for item in red_res.results:
        assert item.colour is not None and "red" in item.colour.lower()
    assert red_res.excluded_unknown_attributes.get("colour", 0) > 0

    # Grey / Gray alias symmetry
    grey_res = svc.search_inventory(SearchFilters(colour="gray"))
    grey_res2 = svc.search_inventory(SearchFilters(colour="grey"))
    assert [r.listing_id for r in grey_res.results] == [r.listing_id for r in grey_res2.results]

    # Unsupported colour returns 0 results cleanly (no black/white leakage)
    teal_res = svc.search_inventory(SearchFilters(colour="teal"))
    assert teal_res.total_matches == 0

    # Hyphen/space model flexibility: "urban cruiser" resolves to "urban-cruiser" listing
    uc_res = svc.search_inventory(SearchFilters(model="Urban Cruiser"))
    assert uc_res.total_matches >= 1
    assert any("urban" in item.model.lower() for item in uc_res.results)

    # Grounded suggested prompts work in single signal query and stay <= 4 items
    prompts = svc.get_suggested_prompts()
    assert 1 <= len(prompts) <= 4
    assert any("SUV" in p or "Toyota" in p or "AED" in p for p in prompts)


def test_real_inventory_search_correctness_matrix(seeded_db: Path) -> None:
    """Comprehensive regression matrix over the real 100-listing dubizzle dataset."""
    from fastapi.testclient import TestClient
    from pydantic import ValidationError

    from backend.main import create_app

    svc = InventorySearchService(seeded_db)

    # 1. Min & max confirmed cash price range (monthly instalments must not satisfy)
    r_min = svc.search_inventory(SearchFilters(min_price=150000))
    assert r_min.total_matches > 0
    assert r_min.excluded_unknown_price > 0
    assert all(
        item.cash_price is not None
        and item.cash_price >= 150000
        and item.cash_price_status is not None
        and item.cash_price_status.value == "confirmed"
        for item in r_min.results
    )

    r_band = svc.search_inventory(SearchFilters(min_price=80000, max_price=160000))
    assert r_band.total_matches > 0
    assert r_band.excluded_unknown_price > 0
    assert all(
        item.cash_price is not None
        and 80000 <= item.cash_price <= 160000
        and item.cash_price_status is not None
        and item.cash_price_status.value == "confirmed"
        for item in r_band.results
    )

    # 2. GCC vs Non-GCC specifications (unknown spec_region never matches either)
    r_gcc = svc.search_inventory(SearchFilters(spec_region="gcc"))
    r_nongcc = svc.search_inventory(SearchFilters(spec_region="non-gcc"))
    r_imported = svc.search_inventory(SearchFilters(spec_region="imported"))
    assert r_gcc.total_matches == 57
    assert r_nongcc.total_matches == 13
    assert [x.listing_id for x in r_nongcc.results] == [x.listing_id for x in r_imported.results]
    assert all(item.spec_region is not None and item.spec_region != "GCC" for item in r_nongcc.results)
    assert r_gcc.excluded_unknown_attributes.get("spec_region") == 30
    assert r_nongcc.excluded_unknown_attributes.get("spec_region") == 30

    # 3. Transmission, body_type (confirmed vs inferred), colour, warranty + missing-attr counts
    r_auto = svc.search_inventory(SearchFilters(transmission="auto"))
    assert r_auto.total_matches == 35
    assert r_auto.excluded_unknown_attributes.get("transmission", 0) > 0
    assert all(item.transmission == "Automatic" for item in r_auto.results)

    r_man = svc.search_inventory(SearchFilters(transmission="manual"))
    assert r_man.total_matches == 2
    assert all(item.transmission == "Manual" for item in r_man.results)

    r_suv = svc.search_inventory(SearchFilters(body_type="suv"))
    assert r_suv.total_matches > 0
    assert any(item.body_type_status and item.body_type_status.value == "confirmed" for item in r_suv.results)
    assert any(item.body_type_status and item.body_type_status.value == "inferred" for item in r_suv.results)

    r_war = svc.search_inventory(SearchFilters(warranty=True))
    assert r_war.total_matches == 33
    assert r_war.excluded_unknown_attributes.get("warranty", 0) > 0
    assert all(
        item.warranty_status is not None and item.warranty_status.value == "confirmed"
        for item in r_war.results
    )

    # 4. Contradictory constraints & FTS5 hard-filter enforcement
    with pytest.raises(ValidationError):
        SearchFilters(min_price=200000, max_price=100000)
    with pytest.raises(ValidationError):
        SearchFilters(min_mileage=50000, max_mileage=10000)

    r_contra = svc.search_inventory(SearchFilters(make="mercedes-benz", model="cr-v"))
    assert r_contra.total_matches == 0
    assert r_contra.results == []

    r_fts_locked = svc.search_inventory(
        SearchFilters(make="honda", max_price=1, free_text="Honda CR-V")
    )
    assert r_fts_locked.total_matches == 0
    assert r_fts_locked.results == []

    # 5. API parameter enforcement (min_price, spec_region=non-gcc, contradictory range 422)
    with TestClient(create_app(db_path=seeded_db, auto_ingest=False)) as tc:
        ok_band = tc.get("/inventory", params={"min_price": 80000, "max_price": 160000})
        assert ok_band.status_code == 200
        assert ok_band.json()["total_matches"] == r_band.total_matches

        ok_ngcc = tc.get("/inventory", params={"spec_region": "non-gcc"})
        assert ok_ngcc.status_code == 200
        assert ok_ngcc.json()["total_matches"] == 13

        bad_range = tc.get("/inventory", params={"min_price": 200000, "max_price": 100000})
        assert bad_range.status_code == 422
