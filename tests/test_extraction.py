"""Automated unit and gold-evaluation tests for extraction engine (no network calls)."""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd
import pytest

from backend.config import settings
from backend.inventory.extraction import (
    detect_language,
    extract_attributes,
    sanitize_display_description,
    verify_evidence,
)
from backend.models.entities import (
    ExtractionMethod,
    ExtractionStatus,
    ListingAttribute,
    ListingLanguage,
)


def _attr_map(attrs: list[ListingAttribute]) -> dict[str, ListingAttribute]:
    return {a.attribute_name: a for a in attrs}


def test_cash_price_explicit_patterns() -> None:
    cases = [
        ("Selling Price: AED 204,999", 204999.0),
        ("AED 115,750.00 in cash", 115750.0),
        ("Price: 329,000 AED or 4,567 AED / Month", 329000.0),
        ("Summer Offer: AED 225,000/-", 225000.0),
        ("Tesla Model X 2018 GCC specs for 85,000.00 AED", 85000.0),
        ("AED 89 900 or AED 1380/ Month", 89900.0),
        ("25.000AED", 25000.0),
        ("AED 106000", 106000.0),
    ]
    for text, expected in cases:
        attrs = _attr_map(
            extract_attributes(
                make="audi",
                model="q8",
                trim="s line",
                year=2023,
                title="Test Listing",
                description_raw=text,
            )
        )
        assert "cash_price" in attrs, f"Failed to extract cash_price from {text!r}"
        assert attrs["cash_price"].numeric_value == expected
        assert attrs["cash_price"].status == ExtractionStatus.CONFIRMED


@pytest.mark.parametrize(
    "trap_text,expected_monthly",
    [
        ("AED 1,611/month | GCC Specs", 1611.0),
        ("4,000 P.M | 0% DownPayment | Under Warranty", 4000.0),
        ("1,430AED / Month | 0% DP AVAIL", 1430.0),
        ("Starting from 1063 per month\nPayable in 60 months", 1063.0),
        ("0% Downpayment EMI 1375 Free Insurance", 1375.0),
        ("Monthly starting from 2299 AED per month", 2299.0),
    ],
)
def test_monthly_payment_traps_never_become_cash_price(
    trap_text: str, expected_monthly: float
) -> None:
    attrs = _attr_map(
        extract_attributes(
            make="ford",
            model="territory",
            trim="titanium",
            year=2024,
            title=trap_text,
            description_raw=trap_text,
        )
    )
    assert "cash_price" not in attrs
    assert "monthly_instalment" in attrs
    assert attrs["monthly_instalment"].numeric_value == expected_monthly


def test_down_payment_traps_never_become_cash_price() -> None:
    desc = "Down Payment AED 25,000\nZERO DOWN PAYEMENT= 760\n20% down payment"
    attrs = _attr_map(
        extract_attributes(
            make="hyundai",
            model="accent",
            trim="other",
            year=2023,
            title="Finance Offer",
            description_raw=desc,
        )
    )
    assert "cash_price" not in attrs
    assert "down_payment" in attrs


def test_salary_and_fee_traps_rejected() -> None:
    desc = (
        "Salary requirement: AED 3000 (WPS)\n"
        "Salary Certificate (AED 15000 and More)\n"
        "Upgrade to the advanced condition report for only AED 369\n"
        "Bank processing fee AED 12,500"
    )
    attrs = _attr_map(
        extract_attributes(
            make="nissan",
            model="x-trail",
            trim="s",
            year=2022,
            title="Finance Deal",
            description_raw=desc,
        )
    )
    assert "cash_price" not in attrs


def test_conflicting_cash_prices_flagged_inferred() -> None:
    desc = "Selling Price: AED 150,000\nCash Price: AED 185,000"
    attrs = _attr_map(
        extract_attributes(
            make="bmw",
            model="m4",
            trim="competition",
            year=2022,
            title="BMW M4",
            description_raw=desc,
        )
    )
    assert "cash_price" in attrs
    assert attrs["cash_price"].status == ExtractionStatus.INFERRED


def test_mileage_formats_and_zero_km() -> None:
    cases = [
        ("2026 Mercedes GLC 200 Coupe 0KM", 0.0),
        ("77820 kms", 77820.0),
        ("83.000KM", 83000.0),
        ("العداد : 40000km", 40000.0),
        ("Odometer : 1,194 Kms", 1194.0),
        ("KM - 78000 KM", 78000.0),
    ]
    for text, expected in cases:
        attrs = _attr_map(
            extract_attributes(
                make="mercedes-benz",
                model="c-class",
                trim="c200",
                year=2021,
                title=text,
                description_raw=text,
            )
        )
        assert "mileage_km" in attrs, f"Missing mileage for {text!r}"
        assert attrs["mileage_km"].numeric_value == expected
        assert attrs["mileage_km"].status == ExtractionStatus.CONFIRMED


def test_mileage_miles_to_kilometres_conversion() -> None:
    attrs = _attr_map(
        extract_attributes(
            make="ford",
            model="mustang",
            trim="gt",
            year=2021,
            title="US Import Mustang",
            description_raw="Mileage: 50,000 miles",
        )
    )
    assert "mileage_km" in attrs
    assert attrs["mileage_km"].numeric_value == pytest.approx(80467.0, abs=1.0)
    assert "converted from miles" in (attrs["mileage_km"].attribute_value or "")
    assert attrs["mileage_km"].status == ExtractionStatus.CONFIRMED


def test_mileage_traps_rejected() -> None:
    desc = (
        "Ford warranty until 6/2028 or 100,000 kms<br>"
        "701KM range on a single charge<br>"
        "0-100 km/h in 3.8 sec | Top Speed 250 km/h<br>"
        "Last Service: 17,611 Km<br>Next Service: 32,611 Km"
    )
    attrs = _attr_map(
        extract_attributes(
            make="byd",
            model="han",
            trim="premium",
            year=2025,
            title="2025 BYD Han L EV 701KM",
            description_raw=desc,
        )
    )
    assert "mileage_km" not in attrs


def test_conflicting_mileage_marked_inferred() -> None:
    desc = "Mileage: 25,000 km\nOdometer: 95,000 km"
    attrs = _attr_map(
        extract_attributes(
            make="audi",
            model="a3",
            trim="tfsi",
            year=2019,
            title="Audi A3",
            description_raw=desc,
        )
    )
    assert "mileage_km" in attrs
    assert attrs["mileage_km"].status == ExtractionStatus.INFERRED


def test_warranty_confirmed_vs_inferred_distinction() -> None:
    confirmed = _attr_map(
        extract_attributes(
            make="mercedes-benz",
            model="glc-class",
            trim="glc 200",
            year=2026,
            title="GLC 200 | 2 Years Warranty",
            description_raw="2 Years Warranty Included",
        )
    )
    assert confirmed["warranty"].status == ExtractionStatus.CONFIRMED
    assert confirmed["warranty"].numeric_value == 1.0

    optional = _attr_map(
        extract_attributes(
            make="bmw",
            model="m4",
            trim="competition",
            year=2022,
            title="BMW M4",
            description_raw="Warranty And Service Contract Available",
        )
    )
    assert optional["warranty"].status == ExtractionStatus.INFERRED

    no_warranty = _attr_map(
        extract_attributes(
            make="toyota",
            model="corolla",
            trim="gli",
            year=2018,
            title="Corolla",
            description_raw="Sold as-is, no warranty.",
        )
    )
    assert no_warranty["warranty"].status == ExtractionStatus.CONFIRMED
    assert no_warranty["warranty"].attribute_value == "No"


def test_spec_region_extraction() -> None:
    assert (
        _attr_map(
            extract_attributes(
                make="nissan",
                model="altima",
                trim="sv",
                year=2023,
                title="Altima American Specs",
                description_raw="Spec: American",
            )
        )["spec_region"].attribute_value
        == "US / American"
    )
    # Boilerplate mentioning Japan without Japanese spec/import must not trigger Japanese spec
    no_spec = _attr_map(
        extract_attributes(
            make="bmw",
            model="m4",
            trim="base",
            year=2022,
            title="BMW M4",
            description_raw="Our founder worked 30 years in Japan before moving to Dubai.",
        )
    )
    assert "spec_region" not in no_spec


def test_transmission_ignores_automatic_windows() -> None:
    attrs = _attr_map(
        extract_attributes(
            make="mercedes-benz",
            model="sprinter",
            trim="313 cdi",
            year=2016,
            title="Sprinter",
            description_raw="Diesel, Caravan, Manual Transmission, Automatic windows",
        )
    )
    assert attrs["transmission"].attribute_value == "Manual"


def test_colour_ignores_interior_and_trim_names() -> None:
    attrs = _attr_map(
        extract_attributes(
            make="volkswagen",
            model="t-roc",
            trim="style",
            year=2024,
            title="VW T-Roc Black Edition",
            description_raw="Interior Color: Red\nBlack Edition package",
        )
    )
    assert "colour" not in attrs

    ext_attrs = _attr_map(
        extract_attributes(
            make="bmw",
            model="m4",
            trim="competition",
            year=2022,
            title="BMW M4",
            description_raw="EXTERIOR COLOR : Metallic Blue\nINTERIOR COLOR : Black",
        )
    )
    assert ext_attrs["colour"].attribute_value == "Metallic Blue"


def test_body_type_confirmed_vs_inferred_and_cullinan_chassis() -> None:
    cullinan = _attr_map(
        extract_attributes(
            make="rolls-royce",
            model="cullinan",
            trim="series ii",
            year=2026,
            title="Cullinan",
            description_raw="All-aluminum spaceframe chassis for magic carpet ride.",
        )
    )
    assert cullinan["body_type"].attribute_value == "SUV"
    assert cullinan["body_type"].status == ExtractionStatus.INFERRED

    tova = _attr_map(
        extract_attributes(
            make="tova",
            model="j14",
            trim="other",
            year=2025,
            title="TOVA J14 3.0 TON 2.8L Chassis 2025 Model available only for export",
            description_raw="Export only",
        )
    )
    assert tova["body_type"].attribute_value == "Commercial"
    assert tova["body_type"].status == ExtractionStatus.CONFIRMED


def test_evidence_validation_rejects_hallucinated_llm_attributes() -> None:
    def fake_gemini(_title: str, _desc: str, _missing: list[str]) -> list[ListingAttribute]:
        return [
            # Fake evidence not present in source -> MUST BE REJECTED
            ListingAttribute(
                attribute_name="cash_price",
                attribute_value="150,000 AED",
                numeric_value=150000.0,
                evidence="Selling for 150,000 AED",
                extraction_method=ExtractionMethod.GEMINI,
                status=ExtractionStatus.CONFIRMED,
            ),
            # Valid verbatim evidence -> accepted, and cash_price must be forced INFERRED
            ListingAttribute(
                attribute_name="spec_region",
                attribute_value="GCC",
                evidence="Dubai agency car",
                extraction_method=ExtractionMethod.GEMINI,
                status=ExtractionStatus.CONFIRMED,
            ),
        ]

    attrs = _attr_map(
        extract_attributes(
            make="toyota",
            model="corolla",
            trim="gli",
            year=2020,
            title="Toyota Corolla",
            description_raw="Well maintained Dubai agency car.",
            llm_extractor=fake_gemini,
        )
    )
    assert "cash_price" not in attrs
    assert "spec_region" in attrs
    assert attrs["spec_region"].extraction_method == ExtractionMethod.GEMINI


def test_gemini_cash_price_never_promoted_to_confirmed() -> None:
    def fake_gemini(_title: str, _desc: str, _missing: list[str]) -> list[ListingAttribute]:
        return [
            ListingAttribute(
                attribute_name="cash_price",
                attribute_value="95,000 AED",
                numeric_value=95000.0,
                evidence="around 95k",
                extraction_method=ExtractionMethod.GEMINI,
                status=ExtractionStatus.CONFIRMED,
            )
        ]

    attrs = _attr_map(
        extract_attributes(
            make="toyota",
            model="corolla",
            trim="gli",
            year=2022,
            title="Corolla",
            description_raw="Looking for around 95k negotiable.",
            llm_extractor=fake_gemini,
        )
    )
    assert attrs["cash_price"].status == ExtractionStatus.INFERRED


def test_display_sanitization_strips_phones_urls_and_html() -> None:
    raw = (
        "Clean car &amp; full options.<br><br>"
        "Call Office: +971 4394 0068 or 052 996 5849<br>"
        "WhatsApp: https://wa.me/97144501601<br>"
        "Website: www.cvauto.ae<br>"
        "Mileage: 35,711 Kms"
    )
    clean = sanitize_display_description(raw)
    assert "<br" not in clean
    assert "&amp;" not in clean
    assert "+971" not in clean
    assert "052 996 5849" not in clean
    assert "wa.me" not in clean
    assert "www.cvauto.ae" not in clean
    assert "Clean car & full options." in clean
    assert "Mileage: 35,711 Kms" in clean


def test_language_detection() -> None:
    assert detect_language("2021 Honda CR-V", "Clean white SUV") == ListingLanguage.EN
    assert (
        detect_language("شفروليه ماليبو 2024", "ماليبو 2024 بحاله ممتازه")
        == ListingLanguage.AR
    )
    assert (
        detect_language("2023 Audi RSQ3", "ضمان الوكيل الرسمي full options")
        == ListingLanguage.MIXED
    )


def test_verify_evidence_utility() -> None:
    assert verify_evidence("Selling Price: AED 204,999", "Offer\nSelling   price: aed 204,999")
    assert not verify_evidence("", "Some text")
    assert not verify_evidence("AED 99,000", "AED 120,000")


def test_eval_gold_csv_against_dataset() -> None:
    """Validate deterministic extraction on all 20 hand-labelled rows in eval/gold.csv."""
    gold_path = Path(settings.PROJECT_ROOT) / "eval" / "gold.csv"
    df = pd.read_excel(settings.DATASET_PATH, sheet_name=settings.DATASET_SHEET)

    with gold_path.open(newline="", encoding="utf-8") as fh:
        gold_rows = list(csv.DictReader(fh))

    assert len(gold_rows) == 20

    for gold in gold_rows:
        lid = int(gold["listing_id"])
        row = df.iloc[lid - 1]
        assert str(row["make"]).strip().lower() == gold["make"]
        assert str(row["model"]).strip().lower() == gold["model"]
        assert int(row["year"]) == int(gold["year"])

        lang = detect_language(str(row["title"]), str(row["description"]))
        assert lang.value == gold["language"], f"Language mismatch on ID={lid}"

        attrs = _attr_map(
            extract_attributes(
                make=str(row["make"]).strip().lower(),
                model=str(row["model"]).strip().lower(),
                trim=str(row["trim"]).strip().lower(),
                year=int(row["year"]),
                title=str(row["title"]),
                description_raw=str(row["description"]),
            )
        )

        if gold["cash_price"]:
            assert "cash_price" in attrs, f"Missing cash_price on ID={lid}"
            assert attrs["cash_price"].numeric_value == float(gold["cash_price"])
            assert attrs["cash_price"].status.value == gold["cash_price_status"]
        else:
            assert "cash_price" not in attrs or attrs["cash_price"].status != ExtractionStatus.CONFIRMED, (
                f"False cash_price on ID={lid}: {attrs.get('cash_price')}"
            )

        if gold["monthly_instalment"]:
            assert "monthly_instalment" in attrs, f"Missing monthly on ID={lid}"
            assert attrs["monthly_instalment"].numeric_value == float(gold["monthly_instalment"])

        if gold["mileage_km"]:
            assert "mileage_km" in attrs, f"Missing mileage_km on ID={lid}"
            assert attrs["mileage_km"].numeric_value == float(gold["mileage_km"])
        else:
            assert "mileage_km" not in attrs, f"False mileage_km on ID={lid}: {attrs.get('mileage_km')}"

        if gold["spec_region"]:
            assert attrs["spec_region"].attribute_value == gold["spec_region"], f"Spec mismatch on ID={lid}"

        if gold["warranty_status"]:
            assert "warranty" in attrs, f"Missing warranty on ID={lid}"
            assert attrs["warranty"].status.value == gold["warranty_status"], f"Warranty status mismatch on ID={lid}"

        if gold["transmission"]:
            assert attrs["transmission"].attribute_value == gold["transmission"], f"Transmission mismatch on ID={lid}"

        if gold["colour"]:
            assert attrs["colour"].attribute_value == gold["colour"], f"Colour mismatch on ID={lid}"

        if gold["body_type"]:
            assert attrs["body_type"].attribute_value == gold["body_type"], f"Body type mismatch on ID={lid}"
            assert attrs["body_type"].status.value == gold["body_type_status"], f"Body type status mismatch on ID={lid}"
