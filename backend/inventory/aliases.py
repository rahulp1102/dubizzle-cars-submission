"""Deterministic make, model, spec-region, body-type, and transmission aliases.

Only unambiguous aliases are mapped. Also includes FTS5 query sanitisation so
hyphens, punctuation, and user shorthand never trigger FTS5 syntax errors.
"""

from __future__ import annotations

import re
from typing import Optional

from backend.models.schemas import SearchFilters

MAKE_ALIASES: dict[str, str] = {
    "merc": "mercedes-benz",
    "mercs": "mercedes-benz",
    "mercedes": "mercedes-benz",
    "mercedes benz": "mercedes-benz",
    "mercedes-benz": "mercedes-benz",
    "benz": "mercedes-benz",
    "mb": "mercedes-benz",
    "m-benz": "mercedes-benz",
    "rr": "rolls-royce",
    "rolls": "rolls-royce",
    "rolls royce": "rolls-royce",
    "rolls-royce": "rolls-royce",
    "beemer": "bmw",
    "bimmer": "bmw",
    "vw": "volkswagen",
    "volks": "volkswagen",
    "chevy": "chevrolet",
    "lr": "land rover",
    "landrover": "land rover",
    "land-rover": "land rover",
    "range rover": "land rover",
    "rangerover": "land rover",
    "mini cooper": "mini",
}

MODEL_ALIASES: dict[str, str] = {
    "crv": "cr-v",
    "cr v": "cr-v",
    "cx5": "cx-5",
    "cx 5": "cx-5",
    "troc": "t-roc",
    "t roc": "t-roc",
    "xtrail": "x-trail",
    "x trail": "x-trail",
    "c200": "c-class",
    "c300": "c-class",
    "c63": "c-class",
    "c63s": "c-class",
    "c class": "c-class",
    "e200": "e-class",
    "e240": "e-class",
    "e350": "e-class",
    "e400": "e-class",
    "e class": "e-class",
    "glc": "glc-class",
    "glc200": "glc-class",
    "glc 200": "glc-class",
    "glc300": "glc-class",
    "glc 300": "glc-class",
    "gla": "gla-class",
    "gla200": "gla-class",
    "gla 200": "gla-class",
    "gla35": "gla-class",
    "gla 35": "gla-class",
    "gle": "gle-class",
    "gle400": "gle-class",
    "gle 400": "gle-class",
    "cla": "cla-class",
    "cla250": "cla-class",
    "cla 250": "cla-class",
    "cls": "cls-class",
    "cls550": "cls-class",
    "cls 550": "cls-class",
    "a45": "a-class",
    "a 45": "a-class",
    "v220": "v-class",
    "v220d": "v-class",
    "325i": "3-series",
    "330i": "3-series",
    "3 series": "3-series",
    "2 series": "2-series",
    "m2": "2-series",
    "m235": "2-series",
    "es350": "es-series",
    "es 350": "es-series",
    "rsq3": "rs q3",
    "lc70": "land cruiser 70 series",
    "lc79": "land cruiser 70 series",
    "land cruiser 70": "land cruiser 70 series",
    "landcruiser 79": "land cruiser 70 series",
    "evoque": "range rover evoque",
    "velar": "range rover velar",
    "rr sport": "range rover sport",
    "super safari": "patrol safari",
    "vtc": "patrol safari",
    "sf90": "sf90 stradale",
}

SPEC_REGION_ALIASES: dict[str, str] = {
    "gcc": "GCC",
    "gcc spec": "GCC",
    "gcc specs": "GCC",
    "gulf": "GCC",
    "non-gcc": "Non-GCC",
    "non gcc": "Non-GCC",
    "non-gcc spec": "Non-GCC",
    "non-gcc specs": "Non-GCC",
    "imported": "Non-GCC",
    "import": "Non-GCC",
    "us": "US / American",
    "usa": "US / American",
    "american": "US / American",
    "us / american": "US / American",
    "japan": "Japanese",
    "japanese": "Japanese",
    "jdm": "Japanese",
    "korea": "Korean",
    "korean": "Korean",
    "euro": "European",
    "european": "European",
    "canadian": "Canadian",
    "canada": "Canadian",
    "chinese": "Chinese",
}

BODY_TYPE_ALIASES: dict[str, str] = {
    "suv": "SUV",
    "suvs": "SUV",
    "4x4": "SUV",
    "4wd": "SUV",
    "crossover": "SUV",
    "sedan": "Sedan",
    "sedans": "Sedan",
    "saloon": "Sedan",
    "coupe": "Coupe",
    "coupes": "Coupe",
    "coupé": "Coupe",
    "convertible": "Convertible",
    "convertable": "Convertible",
    "cabriolet": "Convertible",
    "roadster": "Convertible",
    "hatchback": "Hatchback",
    "hatch": "Hatchback",
    "pickup": "Pickup",
    "pick-up": "Pickup",
    "truck": "Pickup",
    "van": "Van",
    "mpv": "MPV",
    "minivan": "MPV",
    "wagon": "Wagon",
    "commercial": "Commercial",
    "chassis": "Commercial",
}

TRANSMISSION_ALIASES: dict[str, str] = {
    "auto": "Automatic",
    "automatic": "Automatic",
    "tiptronic": "Automatic",
    "cvt": "Automatic",
    "dct": "Automatic",
    "manual": "Manual",
    "stick": "Manual",
}

COLOUR_ALIASES: dict[str, str] = {
    "gray": "Grey",
    "grey": "Grey",
    "metallic gray": "Metallic Grey",
    "metallic grey": "Metallic Grey",
    "dark gray": "Dark Grey",
    "dark grey": "Dark Grey",
    "pearl white": "Pearl White",
    "metallic blue": "Metallic Blue",
    "metallic black": "Metallic Black",
    "metallic silver": "Metallic Silver",
    "white": "White",
    "black": "Black",
    "silver": "Silver",
    "blue": "Blue",
    "red": "Red",
    "green": "Green",
    "gold": "Gold",
    "beige": "Beige",
    "brown": "Brown",
    "orange": "Orange",
    "yellow": "Yellow",
    "bronze": "Bronze",
    "burgundy": "Burgundy",
    "maroon": "Maroon",
    "champagne": "Champagne",
    "dark blue": "Dark Blue",
    "light blue": "Light Blue",
    "midnight blue": "Midnight Blue",
    "navy": "Navy",
    "charcoal": "Charcoal",
}


def resolve_make(raw: Optional[str]) -> Optional[str]:
    """Resolve unambiguous make shorthand (e.g. 'merc' -> 'mercedes-benz')."""
    if raw is None or not raw.strip():
        return None
    cleaned = " ".join(raw.strip().lower().split())
    return MAKE_ALIASES.get(cleaned, cleaned)


def resolve_model(raw: Optional[str]) -> Optional[str]:
    """Resolve unambiguous model aliases (e.g. 'crv' -> 'cr-v', 'urban-cruiser' -> 'urban cruiser')."""
    if raw is None or not raw.strip():
        return None
    cleaned = " ".join(raw.strip().lower().split())
    if cleaned in MODEL_ALIASES:
        return MODEL_ALIASES[cleaned]
    dehyphen = " ".join(cleaned.replace("-", " ").split())
    if dehyphen in MODEL_ALIASES:
        return MODEL_ALIASES[dehyphen]
    return cleaned


def resolve_spec_region(raw: Optional[str]) -> Optional[str]:
    """Normalise specification region strings to canonical values."""
    if raw is None or not raw.strip():
        return None
    cleaned = " ".join(raw.strip().lower().split())
    return SPEC_REGION_ALIASES.get(cleaned, raw.strip())


def resolve_body_type(raw: Optional[str]) -> Optional[str]:
    """Normalise body type strings to canonical values."""
    if raw is None or not raw.strip():
        return None
    cleaned = " ".join(raw.strip().lower().split())
    return BODY_TYPE_ALIASES.get(cleaned, raw.strip().title())


def resolve_transmission(raw: Optional[str]) -> Optional[str]:
    """Normalise transmission strings to canonical values."""
    if raw is None or not raw.strip():
        return None
    cleaned = " ".join(raw.strip().lower().split())
    return TRANSMISSION_ALIASES.get(cleaned, raw.strip().title())


def resolve_colour(raw: Optional[str]) -> Optional[str]:
    """Normalise exterior colour strings to canonical values."""
    if raw is None or not raw.strip():
        return None
    cleaned = " ".join(raw.strip().lower().split())
    return COLOUR_ALIASES.get(cleaned, raw.strip().title())


def normalize_filters(filters: SearchFilters) -> SearchFilters:
    """Return a new SearchFilters instance with all aliases deterministically resolved."""
    data = filters.model_dump()
    # Special case: if user passed a Range Rover model in make="range rover", preserve model intent if needed
    raw_make = data.get("make")
    raw_model = data.get("model")

    resolved_make = resolve_make(raw_make)
    resolved_model = resolve_model(raw_model)

    if (
        raw_make
        and raw_make.strip().lower() in {"range rover", "rangerover"}
        and not resolved_model
    ):
        resolved_model = "range rover"

    return SearchFilters(
        make=resolved_make,
        model=resolved_model,
        min_year=data.get("min_year"),
        max_year=data.get("max_year"),
        min_price=data.get("min_price"),
        max_price=data.get("max_price"),
        min_mileage=data.get("min_mileage"),
        max_mileage=data.get("max_mileage"),
        spec_region=resolve_spec_region(data.get("spec_region")),
        body_type=resolve_body_type(data.get("body_type")),
        transmission=resolve_transmission(data.get("transmission")),
        colour=resolve_colour(data.get("colour")),
        warranty=data.get("warranty"),
        free_text=data.get("free_text").strip() if data.get("free_text") else None,
    )


def build_fts_query(free_text: str) -> Optional[str]:
    """Build a safe SQLite FTS5 MATCH expression with alias expansion.

    Strips special operators so punctuation/hyphens never trigger FTS5 syntax errors.
    """
    if not free_text or not free_text.strip():
        return None

    raw_tokens = re.findall(r"[A-Za-z0-9\u0600-\u06FF]+", free_text.lower())
    if not raw_tokens:
        return None

    clauses: list[str] = []
    for tok in raw_tokens:
        expanded: list[str] = [f'"{tok}"']
        # Expand known single-token make/model aliases cleanly inside FTS OR group
        alias_target = MAKE_ALIASES.get(tok) or MODEL_ALIASES.get(tok)
        if alias_target:
            parts = [f'"{p}"' for p in re.findall(r"[a-z0-9]+", alias_target)]
            if parts:
                expr = parts[0] if len(parts) == 1 else f"({' AND '.join(parts)})"
                if expr not in expanded:
                    expanded.append(expr)
        if len(expanded) > 1:
            clauses.append(f"({' OR '.join(expanded)})")
        else:
            clauses.append(expanded[0])

    return " AND ".join(clauses)
