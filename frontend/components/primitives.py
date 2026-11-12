"""Shared formatting, epistemic badge, and UI primitive helpers for the dubizzle AI frontend."""

from __future__ import annotations

import html
import re
import urllib.parse
from typing import Any, Optional

import streamlit as st

_SPECIAL_MAKES: dict[str, str] = {
    "mercedes-benz": "Mercedes-Benz",
    "bmw": "BMW",
    "gwm": "GWM",
    "byd": "BYD",
    "jac": "JAC",
    "mg": "MG",
    "gmc": "GMC",
    "vw": "Volkswagen",
    "land rover": "Land Rover",
    "land-rover": "Land Rover",
    "rolls-royce": "Rolls-Royce",
    "tova": "TOVA",
}

_SPECIAL_MODELS: dict[str, str] = {
    "glc-class": "GLC-Class",
    "gla-class": "GLA-Class",
    "gle-class": "GLE-Class",
    "s-class": "S-Class",
    "c-class": "C-Class",
    "e-class": "E-Class",
    "g-class": "G-Class",
    "a-class": "A-Class",
    "v-class": "V-Class",
    "cla-class": "CLA-Class",
    "cls-class": "CLS-Class",
    "2-series": "2 Series",
    "3-series": "3 Series",
    "4-series": "4 Series",
    "5-series": "5 Series",
    "es-series": "ES Series",
    "x5": "X5",
    "x6": "X6",
    "q8": "Q8",
    "rs q3": "RS Q3",
    "cr-v": "CR-V",
    "cx-5": "CX-5",
    "t-roc": "T-Roc",
    "x-trail": "X-Trail",
    "bz4x": "bZ4X",
    "j14": "J14",
    "h9": "H9",
    "t99": "T99",
    "sf90 stradale": "SF90 Stradale",
}

_UPPERCASE_TRIM_TOKENS: set[str] = {
    "2wd",
    "4wd",
    "4x4",
    "awd",
    "fwd",
    "rwd",
    "amg",
    "bmw",
    "cla",
    "cls",
    "dlx",
    "dx",
    "ex",
    "fsport",
    "gl",
    "gla",
    "glc",
    "gle",
    "gls",
    "glx",
    "gt",
    "gti",
    "gts",
    "gxr",
    "hs",
    "ii",
    "iii",
    "iv",
    "le",
    "ls",
    "lt",
    "ltz",
    "lx",
    "m",
    "m3",
    "m4",
    "m5",
    "rs",
    "rst",
    "se",
    "sel",
    "sl",
    "sline",
    "sr",
    "srv",
    "sv",
    "sx",
    "tx",
    "txl",
    "v6",
    "v8",
    "v10",
    "v12",
    "vx",
    "vxr",
    "xl",
    "xle",
    "xlt",
    "xse",
    "4matic",
    "xdrive",
    "sdrive",
    "quattro",
}

ATTRIBUTE_LABELS: dict[str, str] = {
    "cash_price": "Cash Price",
    "monthly_instalment": "Monthly Instalment",
    "down_payment": "Down Payment",
    "mileage_km": "Mileage",
    "spec_region": "Regional Spec",
    "body_type": "Body Type",
    "transmission": "Transmission",
    "colour": "Exterior Colour",
    "warranty": "Warranty",
    "engine": "Engine",
    "service_history": "Service History",
    "accident_free": "Accident-Free",
}


def format_make_name(raw_make: str) -> str:
    """Format canonical lowercase vehicle make for clean marketplace display."""
    low = str(raw_make or "").strip().lower()
    if not low:
        return ""
    if low in _SPECIAL_MAKES:
        return _SPECIAL_MAKES[low]
    return " ".join(part.capitalize() for part in low.replace("-", " ").split())


def format_model_name(raw_model: str) -> str:
    """Format canonical lowercase vehicle model cleanly."""
    low = str(raw_model or "").strip().lower()
    if not low:
        return ""
    if low in _SPECIAL_MODELS:
        return _SPECIAL_MODELS[low]
    parts: list[str] = []
    for token in low.replace("-", " ").split():
        if token in {"ii", "iii", "iv"}:
            parts.append(token.upper())
        elif re.fullmatch(r"[a-z]{1,2}\d+[a-z]*", token):
            parts.append(token.upper())
        else:
            parts.append(token.capitalize())
    return " ".join(parts)


def format_trim_name(raw_trim: Any) -> str:
    """Normalize raw lowercase trim strings into readable automotive trim names."""
    text = str(raw_trim or "").strip()
    if not text or text.lower() in {"none", "null", "n/a", "na", "other", "-"}:
        return ""

    def _fmt_subtoken(tok: str) -> str:
        low = tok.lower()
        if low in _UPPERCASE_TRIM_TOKENS:
            return low.upper()
        if re.fullmatch(r"[a-z]{1,3}\d+[a-z]*", low) or re.fullmatch(r"\d+[a-z]{1,2}", low):
            return low.upper()
        if low in {"and", "or", "with", "for", "edition", "line", "pack", "package"}:
            return low.capitalize()
        return low.capitalize()

    words: list[str] = []
    for word in text.split():
        if "-" in word:
            words.append("-".join(_fmt_subtoken(part) for part in word.split("-") if part))
        else:
            words.append(_fmt_subtoken(word))
    return " ".join(words)


def format_vehicle_title(item: dict[str, Any]) -> str:
    """Format Year + Make + Model for card headings and selectors."""
    year = str(item.get("year") or "").strip()
    make = format_make_name(str(item.get("make") or ""))
    model = format_model_name(str(item.get("model") or ""))
    parts = [p for p in (year, make, model) if p]
    if parts:
        return " ".join(parts)
    fallback = str(item.get("title") or "").strip()
    if fallback:
        return fallback
    lid = item.get("listing_id")
    return f"Listing #{lid}" if lid is not None else "Vehicle"


def format_vehicle_label(item: dict[str, Any]) -> str:
    """Human-readable DB-backed label for dropdowns, focus strip, and bookings."""
    base = format_vehicle_title(item)
    trim = format_trim_name(item.get("trim"))
    lid = item.get("listing_id")
    if trim:
        return f"{base} {trim} (#{lid})"
    return f"{base} (#{lid})"


def format_attribute_label(attr_name: str) -> str:
    """Convert internal snake_case attribute key into clean UI label."""
    return ATTRIBUTE_LABELS.get(attr_name, attr_name.replace("_", " ").title())


def build_placeholder_image_uri(theme: str = "light") -> str:
    """Generate a theme-matched vehicle photo placeholder SVG data URI."""
    is_dark = theme == "dark"
    bg = "#1D212A" if is_dark else "#EEF1F5"
    stroke = "#5D6677" if is_dark else "#8C95A4"
    text_fill = "#8E98A8" if is_dark else "#64748B"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" width="640" height="400">'
        f'<rect width="640" height="400" fill="{bg}"/>'
        f'<g fill="none" stroke="{stroke}" stroke-width="10" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M120 245 L165 170 Q180 150 215 150 L405 150 Q440 150 462 176 L515 210 '
        'Q535 220 535 245 L535 270 L105 270 L105 250 Q105 245 120 245 Z"/>'
        f'<circle cx="195" cy="272" r="34" fill="{bg}"/>'
        f'<circle cx="445" cy="272" r="34" fill="{bg}"/>'
        "</g>"
        f'<text x="320" y="340" text-anchor="middle" fill="{text_fill}" '
        'font-family="system-ui,-apple-system,sans-serif" font-size="20" font-weight="600">'
        "Vehicle photo unavailable</text>"
        "</svg>"
    )
    return "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(svg)


PLACEHOLDER_IMAGE_URI = build_placeholder_image_uri("light")


def safe_photo_src(raw_url: Any, theme: str = "light") -> str:
    """Validate HTTP(S) photo URL or fall back to theme-aware placeholder SVG."""
    text = str(raw_url or "").strip()
    if text.startswith("https://") or text.startswith("http://"):
        return text
    return build_placeholder_image_uri(theme)


def format_rtl_safe_html(raw_text: str) -> str:
    """Escape raw seller text cleanly and preserve line breaks for dir='auto' rendering."""
    escaped = html.escape(raw_text.strip())
    return escaped.replace("\r\n", "\n").replace("\n", "<br/>")


def render_empty_state(title: str, body: str) -> None:
    """Render a clean, restrained empty-state card without decorative clutter."""
    st.markdown(
        '<div class="dbz-empty-state">'
        f'<div class="dbz-empty-title">{html.escape(title)}</div>'
        f'<div class="dbz-empty-body">{html.escape(body)}</div>'
        "</div>",
        unsafe_allow_html=True,
    )


def format_aed(amount: Optional[float | int]) -> str:
    """Format integer AED currency cleanly."""
    if amount is None:
        return "Not specified"
    return f"AED {int(round(float(amount))):,}"


def is_masked_phone(phone: Optional[str]) -> bool:
    """Return True if a phone string already contains masking asterisks."""
    return bool(phone and "*" in phone)


def mask_phone_display(phone: Optional[str], *, empty_label: str = "—") -> str:
    """Consistently mask a phone number for buyer UI display (matching backend mask_phone)."""
    cleaned = (phone or "").strip()
    if not cleaned:
        return empty_label
    if len(cleaned) <= 6:
        return "*" * len(cleaned)
    prefix = cleaned[:4]
    suffix = cleaned[-3:]
    stars = "*" * max(4, len(cleaned) - 7)
    return f"{prefix}{stars}{suffix}"
