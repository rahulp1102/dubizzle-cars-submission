"""Deterministic attribute extraction, display sanitisation, and evidence validation.

Rule: Every extracted attribute must carry an evidence snippet verifiable against
the original listing text. Unknown attributes are never invented. Confirmed cash
prices are strictly separated from monthly instalments, down payments, fees, and
salary figures.
"""

from __future__ import annotations

import hashlib
import html
import re
from typing import Callable, Optional

from backend.models.entities import (
    ExtractionMethod,
    ExtractionStatus,
    ListingAttribute,
    ListingLanguage,
)

# Optional callback signature for Gemini fallback:
# fn(title: str, description: str, missing_attrs: list[str]) -> list[ListingAttribute]
LLMExtractor = Callable[[str, str, list[str]], list[ListingAttribute]]

_BIDI_RE = re.compile(r"[\u200e\u200f\u202a-\u202e\ufeff]")
_BR_RE = re.compile(r"<br\s*/?>|<br\b[^>\n]*$", re.IGNORECASE)
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>)\]]+", re.IGNORECASE)
_PHONE_RE = re.compile(
    r"(?:"
    r"(?:\+|00)?971[\s\-]?(?:\d[\s\-]?){7,10}\d"
    r"|"
    r"\(\s*\+?971\s*\)"
    r"|"
    r"(?<!\d)0(?:5[024568]|[234679])(?:[\s\-]?\d){7}(?!\d)"
    r"|"
    r"(?:contact|call|whatsapp|viewing)\s+05\d{3,8}$"
    r")",
    re.IGNORECASE,
)

_ALLOWED_COLOURS = {
    "white",
    "black",
    "silver",
    "grey",
    "gray",
    "blue",
    "red",
    "green",
    "gold",
    "beige",
    "brown",
    "orange",
    "yellow",
    "bronze",
    "burgundy",
    "maroon",
    "champagne",
    "pearl white",
    "metallic blue",
    "metallic black",
    "metallic grey",
    "metallic gray",
    "metallic silver",
    "dark grey",
    "dark gray",
    "dark blue",
    "light blue",
    "midnight blue",
    "navy",
    "charcoal",
}

_MODEL_BODY_TYPE_MAP: dict[str, str] = {
    "glc-class": "SUV",
    "gla-class": "SUV",
    "gle-class": "SUV",
    "q8": "SUV",
    "rs q3": "SUV",
    "territory": "SUV",
    "edge": "SUV",
    "explorer": "SUV",
    "h9": "SUV",
    "t99": "SUV",
    "cullinan": "SUV",
    "tahoe": "SUV",
    "patrol": "SUV",
    "patrol safari": "SUV",
    "kicks": "SUV",
    "x-trail": "SUV",
    "model x": "SUV",
    "js4": "SUV",
    "discovery": "SUV",
    "range rover": "SUV",
    "range rover sport": "SUV",
    "range rover evoque": "SUV",
    "countryman": "SUV",
    "ix3": "SUV",
    "prado": "SUV",
    "bentayga": "SUV",
    "yu7": "SUV",
    "xc60": "SUV",
    "urban cruiser": "SUV",
    "cr-v": "SUV",
    "t-roc": "SUV",
    "captiva": "SUV",
    "cx-5": "SUV",
    "bz4x": "SUV",
    "yaris": "Sedan",
    "malibu": "Sedan",
    "c-class": "Sedan",
    "e-class": "Sedan",
    "cls-class": "Sedan",
    "cla-class": "Sedan",
    "maxima": "Sedan",
    "han": "Sedan",
    "sunny": "Sedan",
    "altima": "Sedan",
    "accent": "Sedan",
    "ghost": "Sedan",
    "3-series": "Sedan",
    "corolla": "Sedan",
    "i4": "Sedan",
    "es-series": "Sedan",
    "a3": "Sedan",
    "arrizo 5": "Sedan",
    "rs7": "Sedan",
    "m5": "Sedan",
    "charger": "Sedan",
    "f430": "Coupe",
    "sf90 stradale": "Coupe",
    "chiron": "Coupe",
    "m4": "Coupe",
    "wraith": "Coupe",
    "mustang": "Coupe",
    "challenger": "Coupe",
    "a-class": "Hatchback",
    "2-series": "Coupe",
    "wingle 5": "Pickup",
    "hilux": "Pickup",
    "land cruiser 70 series": "Pickup",
    "tundra": "Pickup",
    "innova": "MPV",
    "v-class": "Van",
    "sprinter": "Van",
    "j14": "Commercial",
}

_PRICE_TRAP_RE = re.compile(
    r"(?:/\s*month|per\s+month|\bmonthly\b|\bp\.?\s*m\b|\bp/m\b|\bemi\b|"
    r"instal+ment|down\s*payment|downpayment|\bdp\b|\bsalary\b|\bwps\b|"
    r"deposit|condition\s+report|\bupgrade\b|\bfee\b|\bfees\b|"
    r"starting\s+from|payable\s+in|\bfor\s+\d+\s+years?\b)",
    re.IGNORECASE,
)

_MILEAGE_TRAP_RE = re.compile(
    r"(?:warranty|service\s+contract|service\s+package|service\s+plan|"
    r"last\s+service|next\s+service|valid\s+till|valid\s+until|"
    r"\buntil\b|\btill\b|driving\s+range|\brange\b|\bwltp\b|"
    r"single\s+charge|km/h|kmph|top\s+speed|0[\s\-–]+100|acceleration)",
    re.IGNORECASE,
)


def compute_content_hash(title: str, description_raw: str) -> str:
    """Compute a deterministic SHA-256 hash of title + raw description."""
    payload = f"{title.strip()}\n{description_raw.strip()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_for_matching(text: str) -> str:
    """Convert HTML breaks and entities into plain text for regex extraction."""
    cleaned = _BR_RE.sub("\n", text)
    cleaned = html.unescape(cleaned)
    cleaned = _BIDI_RE.sub("", cleaned)
    return cleaned


def sanitize_display_description(description_raw: str) -> str:
    """Produce a display-safe description with <br> cleaned and phone/URLs stripped.

    The original description_raw must remain untouched in the database.
    """
    text = normalize_for_matching(description_raw)
    text = _URL_RE.sub("", text)
    # Strip trailing truncated UAE mobile numbers when preceded by viewing/contact phrase
    text = re.sub(
        r"((?:schedule\s+a\s+viewing|contact|call|whatsapp)[^\n]*?)\b05\d{3,8}\s*$",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    text = _PHONE_RE.sub("", text)

    cleaned_lines: list[str] = []
    for line in text.splitlines():
        # Collapse extra internal spaces left after stripping tokens
        compact = re.sub(r"[ \t]{2,}", " ", line).strip()
        # Drop lines that became dangling contact stubs like "Office:" or "WHATSAPP -"
        if re.fullmatch(
            r"(?:call|whatsapp|call/whatsapp|office|sales|phone(?:\s*no\.?)?|tel|mobile|website|instagram|facebook|tiktok|youtube|contact(?:\s+us)?)\s*[:#\-–—]*",
            compact,
            re.IGNORECASE,
        ):
            continue
        cleaned_lines.append(compact)

    joined = "\n".join(cleaned_lines)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()


def detect_language(title: str, description_raw: str) -> ListingLanguage:
    """Detect whether listing text is English, Arabic, or mixed."""
    combined = f"{title} {normalize_for_matching(description_raw)}"
    arabic_chars = len(re.findall(r"[\u0600-\u06FF]", combined))
    latin_chars = len(re.findall(r"[A-Za-z]", combined))
    total = arabic_chars + latin_chars
    if arabic_chars == 0 or total == 0:
        return ListingLanguage.EN
    ratio = arabic_chars / total
    if ratio > 0.5:
        return ListingLanguage.AR
    if arabic_chars >= 5:
        return ListingLanguage.MIXED
    return ListingLanguage.EN


def _parse_number(raw: str) -> Optional[float]:
    """Parse number strings including commas, space separators, and dot thousands."""
    s = raw.strip()
    if not s:
        return None
    # Space thousands separator e.g. "89 900"
    if re.fullmatch(r"\d{1,3}(?:\s\d{3})+", s):
        s = s.replace(" ", "")
    else:
        s = s.replace(" ", "")
    # Dot thousands separator e.g. "25.000" or "83.000" (no commas, dot + exact 3 digits)
    if "," not in s and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _norm_for_evidence(text: str) -> str:
    s = normalize_for_matching(text)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def verify_evidence(evidence: Optional[str], source_text: str) -> bool:
    """Verify that evidence exists verbatim (whitespace/case normalized) in source_text."""
    if not evidence or not evidence.strip():
        return False
    ev_norm = _norm_for_evidence(evidence)
    src_norm = _norm_for_evidence(source_text)
    return bool(ev_norm) and ev_norm in src_norm


def _split_segments(text: str) -> list[str]:
    """Split text into lines and pipe/bullet-delimited segments for clean context checks."""
    segments: list[str] = []
    for line in text.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        segments.append(line_clean)
        if "|" in line_clean:
            for part in line_clean.split("|"):
                part_clean = part.strip()
                if part_clean and part_clean != line_clean:
                    segments.append(part_clean)
    return segments


def _extract_cash_price(text: str) -> Optional[ListingAttribute]:
    """Extract confirmed cash price in AED while strictly rejecting instalment/fee traps."""
    candidates: list[tuple[float, str]] = []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    explicit_patterns = [
        # "Selling Price: AED 204,999" / "Summer Offer: AED 225,000/-" / "Price: 329,000 AED"
        re.compile(
            r"(?:selling\s+price|cash\s+price|asking\s+price|offer\s+price|summer\s+offer)\s*[:\-]\s*(?:aed|dhs)?\s*(\d[\d,\s.]*\d|\d{5,})",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bprice\s*[:\-]\s*(?:aed|dhs)?\s*(\d[\d,\s.]*\d)",
            re.IGNORECASE,
        ),
        # "AED 115,750.00 in cash" / "AED 165,000 cash"
        re.compile(
            r"(?:aed|dhs)\s*(\d[\d,\s.]*\d)\s*(?:in\s+cash|\bcash\b)",
            re.IGNORECASE,
        ),
        # "234,999 AED or 3,834 AED / Month" / "AED 89 900 or AED 1380/ Month"
        re.compile(
            r"(?:aed|dhs)\s*(\d[\d,\s.]*\d)\s+or\s+(?:aed|dhs)?\s*\d[\d,\s.]*\s*/\s*month",
            re.IGNORECASE,
        ),
        re.compile(
            r"(\d[\d,\s.]*\d)\s*(?:aed|dhs)\s+(?:or\s+[\d,\s.]+\s*(?:aed|dhs)?\s*/\s*month|in\s+cash|\bcash\b)",
            re.IGNORECASE,
        ),
        # "for 85,000.00 AED"
        re.compile(
            r"\bfor\s+(\d[\d,\.]*\d)\s*(?:aed|dhs)\b",
            re.IGNORECASE,
        ),
    ]

    for line in lines:
        matched_in_line = False
        for pat in explicit_patterns:
            m = pat.search(line)
            if not m:
                continue
            # Check only the prefix/immediate context of the match for traps
            snippet = m.group(0)
            prefix_ctx = line[max(0, m.start() - 25) : m.start()]
            if _PRICE_TRAP_RE.search(prefix_ctx) or _PRICE_TRAP_RE.search(snippet.split(" or ")[0]):
                continue
            val = _parse_number(m.group(1))
            if val is not None and 10_000 <= val <= 50_000_000:
                candidates.append((val, snippet.strip()))
                matched_in_line = True
                break

        if matched_in_line:
            continue

        # Pipe-separated cash segments like "... | GCC | 249,000 AED" or "| 25.000AED |"
        if "|" in line:
            for seg in line.split("|"):
                seg = seg.strip()
                if not seg or _PRICE_TRAP_RE.search(seg):
                    continue
                m = re.fullmatch(
                    r"(?:(?:aed|dhs)\s*(\d[\d,\.]*\d)|(\d[\d,\.]*\d)\s*(?:aed|dhs))",
                    seg,
                    re.IGNORECASE,
                )
                if m:
                    num_str = m.group(1) or m.group(2)
                    val = _parse_number(num_str)
                    if val is not None and 10_000 <= val <= 50_000_000:
                        candidates.append((val, seg))

        # Standalone line: "AED 106000" / "25.000AED"
        if not _PRICE_TRAP_RE.search(line):
            m = re.fullmatch(
                r"[•\-\s]*(?:(?:aed|dhs)\s*(\d[\d,\.]*\d)|(\d[\d,\.]*\d)\s*(?:aed|dhs))[/-]*",
                line,
                re.IGNORECASE,
            )
            if m:
                num_str = m.group(1) or m.group(2)
                val = _parse_number(num_str)
                if val is not None and 10_000 <= val <= 50_000_000:
                    candidates.append((val, line))

    if not candidates:
        return None

    # Deduplicate same numeric values (within 1%)
    unique_vals: list[tuple[float, str]] = []
    for val, ev in candidates:
        if not any(abs(val - existing_v) / max(existing_v, 1.0) < 0.01 for existing_v, _ in unique_vals):
            unique_vals.append((val, ev))

    if len(unique_vals) > 1:
        # Conflicting cash prices -> flag as inferred rather than confirmed
        primary_val, primary_ev = unique_vals[0]
        return ListingAttribute(
            attribute_name="cash_price",
            attribute_value=f"{int(primary_val):,} AED (conflict detected)",
            numeric_value=primary_val,
            evidence=primary_ev,
            extraction_method=ExtractionMethod.REGEX,
            status=ExtractionStatus.INFERRED,
            confidence=0.4,
        )

    val, ev = unique_vals[0]
    return ListingAttribute(
        attribute_name="cash_price",
        attribute_value=f"{int(val):,} AED",
        numeric_value=val,
        evidence=ev,
        extraction_method=ExtractionMethod.REGEX,
        status=ExtractionStatus.CONFIRMED,
        confidence=0.98,
    )


def _extract_monthly_instalment(text: str) -> Optional[ListingAttribute]:
    """Extract monthly instalment amount in AED (never confused with cash_price)."""
    patterns = [
        re.compile(
            r"(?:aed|dhs)\s*([\d,]+(?:\.\d+)?)\s*(?:/\s*month|per\s+month|\bmonthly\b|\bp\.?\s*m\b|\bp/m\b)",
            re.IGNORECASE,
        ),
        re.compile(
            r"([\d,]+(?:\.\d+)?)\s*(?:aed|dhs)?\s*(?:/\s*month|per\s+month|\bp\.?\s*m\b|\bp/m\b)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:starting\s+from|monthly\s+install?ment|monthly\s+starting\s+from|\bemi\b)\s*[:=]?\s*(?:aed|dhs)?\s*([\d,]+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
    ]
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for pat in patterns:
            m = pat.search(line)
            if not m:
                continue
            val = _parse_number(m.group(1))
            if val is not None and 200 <= val < 50_000:
                return ListingAttribute(
                    attribute_name="monthly_instalment",
                    attribute_value=f"{int(round(val)):,} AED/month",
                    numeric_value=val,
                    evidence=m.group(0).strip(),
                    extraction_method=ExtractionMethod.REGEX,
                    status=ExtractionStatus.CONFIRMED,
                    confidence=0.95,
                )
    return None


def _extract_down_payment(text: str) -> Optional[ListingAttribute]:
    """Extract down payment terms (percentage or AED)."""
    patterns = [
        re.compile(
            r"\b(zero|\d{1,2}\s*%)\s*(?:down[\s\-]*payment|downpayment|dp\b)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:down[\s\-]*payment|downpayment)\s*[:\-]?\s*(zero|\d{1,2}\s*%|aed\s*[\d,]+)",
            re.IGNORECASE,
        ),
    ]
    for line in text.splitlines():
        line = line.strip()
        for pat in patterns:
            m = pat.search(line)
            if m:
                raw_val = m.group(1).strip()
                norm_val = "0%" if raw_val.lower() == "zero" else raw_val.replace(" ", "")
                num_val: Optional[float] = None
                if norm_val.endswith("%"):
                    num_val = _parse_number(norm_val[:-1])
                else:
                    num_val = _parse_number(re.sub(r"[^\d,.]", "", norm_val))
                return ListingAttribute(
                    attribute_name="down_payment",
                    attribute_value=norm_val,
                    numeric_value=num_val,
                    evidence=m.group(0).strip(),
                    extraction_method=ExtractionMethod.REGEX,
                    status=ExtractionStatus.CONFIRMED,
                    confidence=0.95,
                )
    return None


def _extract_mileage_km(text: str) -> Optional[ListingAttribute]:
    """Extract odometer mileage in km, converting miles and rejecting warranty/range/speed traps."""
    candidates: list[tuple[float, str, bool]] = []  # (km_value, evidence, converted_from_miles)

    segments = _split_segments(text)
    for seg in segments:
        if _MILEAGE_TRAP_RE.search(seg):
            continue

        # 1. Explicit label: Mileage: 35,711 Kms / Odometer : 1,194 Kms / العداد : 40000km
        m = re.search(
            r"(?:mileage|odometer|العداد)\s*[:\-]?\s*(\d[\d,\.]*)\s*(km|kms|kilometers|kilometres|miles|mile|mi)?\b",
            seg,
            re.IGNORECASE,
        )
        if m:
            val = _parse_number(m.group(1))
            unit = (m.group(2) or "km").lower()
            if val is not None and 0 <= val <= 1_500_000:
                if unit in {"mi", "mile", "miles"}:
                    km = round(val * 1.60934)
                    candidates.append((float(km), m.group(0).strip(), True))
                else:
                    candidates.append((val, m.group(0).strip(), False))
                continue

        # 2. Miles explicitly without label: "45,000 miles"
        m_miles = re.search(r"\b(\d[\d,\.]*)\s*(miles|mile)\b", seg, re.IGNORECASE)
        if m_miles:
            val = _parse_number(m_miles.group(1))
            if val is not None and 0 <= val <= 1_000_000:
                km = round(val * 1.60934)
                candidates.append((float(km), m_miles.group(0).strip(), True))
                continue

        # 3. "KM - 78000 KM"
        m_km_prefix = re.search(r"\bkm\s*[:\-]\s*(\d[\d,\.]*)\s*(?:km|kms)?\b", seg, re.IGNORECASE)
        if m_km_prefix:
            val = _parse_number(m_km_prefix.group(1))
            if val is not None and 0 <= val <= 1_500_000:
                candidates.append((val, m_km_prefix.group(0).strip(), False))
                continue

        # 4. Standalone km line or contextual km ("0KM", "77820 kms", "1739Km ONLY", "driven 260,000 km", "only 18 km mileage")
        m_standalone = re.fullmatch(
            r"[•\-\s]*(\d[\d,\.]*)\s*(?:km|kms|kilometers|kilometres)\s*",
            seg,
            re.IGNORECASE,
        )
        if m_standalone:
            val = _parse_number(m_standalone.group(1))
            if val is not None and 0 <= val <= 1_500_000:
                candidates.append((val, seg.strip(), False))
                continue

        m_ctx = re.search(
            r"(?:(?:only|driven|boasting\s+only|with)\s+(\d[\d,\.]*)\s*(?:km|kms)\b|"
            r"\b(\d[\d,\.]*)\s*(?:km|kms)\s*(?:only|driven|mileage)\b|"
            r"\b0\s*(?:km|kms)\b)",
            seg,
            re.IGNORECASE,
        )
        if m_ctx:
            raw_num = m_ctx.group(1) or m_ctx.group(2)
            val = 0.0 if raw_num is None else _parse_number(raw_num)
            if val is not None and 0 <= val <= 1_500_000:
                candidates.append((val, m_ctx.group(0).strip(), False))

    if not candidates:
        return None

    # Deduplicate similar numeric readings (within 5% or <= 500 km tolerance)
    unique_vals: list[tuple[float, str, bool]] = []
    for km_val, ev, conv in candidates:
        is_dup = any(
            abs(km_val - existing) <= max(500.0, 0.05 * max(existing, 1.0))
            for existing, _, _ in unique_vals
        )
        if not is_dup:
            unique_vals.append((km_val, ev, conv))

    if len(unique_vals) > 1:
        primary_km, primary_ev, conv = unique_vals[0]
        return ListingAttribute(
            attribute_name="mileage_km",
            attribute_value=f"{int(round(primary_km)):,} km (conflict detected)",
            numeric_value=primary_km,
            evidence=primary_ev,
            extraction_method=ExtractionMethod.REGEX,
            status=ExtractionStatus.INFERRED,
            confidence=0.45,
        )

    km_val, ev, converted = unique_vals[0]
    val_text = (
        f"{int(round(km_val)):,} km (converted from miles)"
        if converted
        else f"{int(round(km_val)):,} km"
    )
    return ListingAttribute(
        attribute_name="mileage_km",
        attribute_value=val_text,
        numeric_value=km_val,
        evidence=ev,
        extraction_method=ExtractionMethod.REGEX,
        status=ExtractionStatus.CONFIRMED,
        confidence=0.95,
    )


def _extract_warranty(text: str) -> Optional[ListingAttribute]:
    """Extract warranty status, distinguishing confirmed coverage from optional availability."""
    confirmed_patterns = [
        re.compile(r"no\s+warranty", re.IGNORECASE),
        re.compile(
            r"(?:under\s+(?:[a-z]+\s+)?warranty|"
            r"free\s+warranty|"
            r"\d+\s*years?\s*(?:[a-z]+\s+)?warranty(?:\s+included)?|"
            r"warranty\s*/\s*service\s+contract\s+\d+\s*years?|"
            r"(?:agency|official\s+dealer(?:\s*\([^)]+\))?|manufacturer|factory|premium|third\s+party|"
            r"bmw|ford|mercedes(?:-benz)?|land\s+rover|rangrover|audi|gargash|al\s+tayer|al\s+nabodah)"
            r"\s*warranty[^\n|•]*|"
            r"warranty\s*(?:valid\s+(?:till|until)|until|till|included|package|from\s+al\s+nabodah|:)[^\n|•]*|"
            r"with\s+(?:one\s+year\s+)?warranty|"
            r"ضمان\s+الوكيل(?:\s+الرسمي)?)",
            re.IGNORECASE,
        ),
    ]
    inferred_patterns = [
        re.compile(
            r"(?:warranty[^\n|•]*\bavailable\b|"
            r"(?:can\s+provide|provide)\s+(?:a\s+)?warranty|"
            r"most\s+vehicles\s+include[^\n|•]*warranty|"
            r"\bwarranty\b)",
            re.IGNORECASE,
        )
    ]

    for seg in _split_segments(text):
        if not re.search(r"(?:warrant|ضمان)", seg, re.IGNORECASE):
            continue
        # Ignore unrelated dealer guarantees like "ضمان فحص المرور" (traffic inspection pass guarantee)
        if "ضمان فحص المرور" in seg and "الوكيل" not in seg:
            continue
        # Check uncertain / optional wording first
        if re.search(
            r"(?:warranty[^\n|•]*\bavailable\b|can\s+provide\s+(?:a\s+)?warranty|"
            r"most\s+vehicles\s+include|warranty\s+options|warranty\s+upon\s+request)",
            seg,
            re.IGNORECASE,
        ):
            return ListingAttribute(
                attribute_name="warranty",
                attribute_value="Mentioned (available/optional)",
                numeric_value=None,
                evidence=seg[:180],
                extraction_method=ExtractionMethod.RULE,
                status=ExtractionStatus.INFERRED,
                confidence=0.7,
            )

        if re.search(r"\bno\s+warranty\b", seg, re.IGNORECASE):
            return ListingAttribute(
                attribute_name="warranty",
                attribute_value="No",
                numeric_value=0.0,
                evidence=seg[:180],
                extraction_method=ExtractionMethod.REGEX,
                status=ExtractionStatus.CONFIRMED,
                confidence=0.95,
            )

        for pat in confirmed_patterns[1:]:
            m = pat.search(seg)
            if m:
                snippet = m.group(0).strip()
                return ListingAttribute(
                    attribute_name="warranty",
                    attribute_value=f"Yes ({snippet[:100]})",
                    numeric_value=1.0,
                    evidence=snippet[:180],
                    extraction_method=ExtractionMethod.REGEX,
                    status=ExtractionStatus.CONFIRMED,
                    confidence=0.92,
                )

    for seg in _split_segments(text):
        if "ضمان فحص المرور" in seg and "الوكيل" not in seg:
            continue
        for pat in inferred_patterns:
            m = pat.search(seg)
            if m:
                return ListingAttribute(
                    attribute_name="warranty",
                    attribute_value="Mentioned",
                    numeric_value=None,
                    evidence=seg[:180],
                    extraction_method=ExtractionMethod.RULE,
                    status=ExtractionStatus.INFERRED,
                    confidence=0.6,
                )
    return None


def _extract_spec_region(text: str) -> Optional[ListingAttribute]:
    """Extract regional specification (GCC, US/American, Japanese, Korean, European, etc.)."""
    rules: list[tuple[str, re.Pattern[str]]] = [
        (
            "GCC",
            re.compile(
                r"(?:(?:regional\s+)?specs?\s*[:\-]\s*gcc\b|gcc\s*(?:spec|specs|specifications?|example)\b|\bgulf\s+car\b|\bgcc\b)",
                re.IGNORECASE,
            ),
        ),
        (
            "US / American",
            re.compile(
                r"(?:(?:regional\s+)?specs?\s*[:\-]\s*(?:us|usa|american)\b|(?:us|usa|american)\s*(?:spec|specs|specifications?|import(?:ed)?)\b)",
                re.IGNORECASE,
            ),
        ),
        (
            "Japanese",
            re.compile(
                r"(?:(?:regional\s+)?specs?\s*[:\-]\s*japan(?:ese)?\b|japan(?:ese)?\s*(?:spec|specs|specifications?|import(?:ed)?)\b)",
                re.IGNORECASE,
            ),
        ),
        (
            "Korean",
            re.compile(
                r"(?:(?:regional\s+)?specs?\s*[:\-]\s*korean\b|korean\s*(?:spec|specs|specifications?|import(?:ed)?)\b)",
                re.IGNORECASE,
            ),
        ),
        (
            "European",
            re.compile(
                r"(?:(?:regional\s+)?specs?\s*[:\-]\s*(?:european|euro)\b|(?:european|euro)\s*(?:spec|specs|specifications?|import(?:ed)?)\b)",
                re.IGNORECASE,
            ),
        ),
        (
            "Canadian",
            re.compile(
                r"(?:(?:regional\s+)?specs?\s*[:\-]\s*canadian\b|canadian\s*(?:spec|specs|specifications?)\b)",
                re.IGNORECASE,
            ),
        ),
        (
            "Chinese",
            re.compile(
                r"(?:(?:regional\s+)?specs?\s*[:\-]\s*chinese\b|chinese\s*(?:spec|specs|specifications?)\b)",
                re.IGNORECASE,
            ),
        ),
    ]

    for seg in _split_segments(text):
        for region, pat in rules:
            m = pat.search(seg)
            if m:
                return ListingAttribute(
                    attribute_name="spec_region",
                    attribute_value=region,
                    evidence=m.group(0).strip(),
                    extraction_method=ExtractionMethod.REGEX,
                    status=ExtractionStatus.CONFIRMED,
                    confidence=0.95,
                )
    return None


def _extract_transmission(text: str) -> Optional[ListingAttribute]:
    """Extract transmission type while avoiding non-drivetrain 'automatic' features."""
    non_trans_following = (
        r"(?!\s+(?:windows|tailgate|climate|ac|air\s+conditioning|headlights|headlamps|"
        r"lights|wipers|doors|mirrors|seats|folding|soft[\s\-]?top|roof|parking|braking|"
        r"emergency|trunk|boot|hold|high\s+beam))"
    )
    manual_pat = re.compile(
        r"\b(?:manual\s+transmission|manual\s+gearbox|\d+\s*-?\s*speed\s+manual|\bstd\s*-\s*manual\b)\b",
        re.IGNORECASE,
    )
    auto_pat = re.compile(
        rf"\b(?:\d+\s*-?\s*speed\s+automatic(?:\s+transmission)?|automatic\s+transmission|"
        rf"tiptronic(?:\s+gears)?|steptronic|s[\s\-]?tronic|\b(?:dct|pdk|cvt)\b|"
        rf"1[\s\-]?speed\s+direct\s+drive|automatic{non_trans_following})\b",
        re.IGNORECASE,
    )

    for seg in _split_segments(text):
        m_man = manual_pat.search(seg)
        if m_man:
            return ListingAttribute(
                attribute_name="transmission",
                attribute_value="Manual",
                evidence=m_man.group(0).strip(),
                extraction_method=ExtractionMethod.REGEX,
                status=ExtractionStatus.CONFIRMED,
                confidence=0.95,
            )
    for seg in _split_segments(text):
        m_auto = auto_pat.search(seg)
        if m_auto:
            return ListingAttribute(
                attribute_name="transmission",
                attribute_value="Automatic",
                evidence=m_auto.group(0).strip(),
                extraction_method=ExtractionMethod.REGEX,
                status=ExtractionStatus.CONFIRMED,
                confidence=0.93,
            )
    return None


def _extract_colour(text: str) -> Optional[ListingAttribute]:
    """Extract exterior vehicle colour only when explicitly stated as vehicle paint/exterior colour."""
    patterns = [
        re.compile(
            r"(?:exterior(?:\s+colou?r)?|\bcolou?r)\s*[:\-]\s*([A-Za-z]+(?:\s+[A-Za-z]+)?)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^\s*1\.\s*([A-Za-z]+)\s+with\s+.*interior\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:finished\s+in|sleek|stunning|awesome)\s+((?:pearl\s+|metallic\s+|dark\s+|light\s+)?[a-z]+)\s+(?:exterior|colou?r|gcc)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b((?:pearl\s+|metallic\s+)?(?:white|black|silver|grey|gray|blue|red|green|gold|beige|brown|orange|yellow))\s+colou?r\b",
            re.IGNORECASE,
        ),
    ]
    for seg in _split_segments(text):
        if re.search(r"\binterior\s+colou?r\b", seg, re.IGNORECASE) and not re.search(
            r"\bexterior\b", seg, re.IGNORECASE
        ):
            continue
        for pat in patterns:
            m = pat.search(seg)
            if not m:
                continue
            cand = re.sub(r"\s+", " ", m.group(1).strip()).lower()
            # Trim trailing noise words if caught in greedy group
            words = cand.split()
            if len(words) == 2 and cand not in _ALLOWED_COLOURS and words[0] in _ALLOWED_COLOURS:
                cand = words[0]
            if cand in _ALLOWED_COLOURS:
                return ListingAttribute(
                    attribute_name="colour",
                    attribute_value=cand.title(),
                    evidence=m.group(0).strip(),
                    extraction_method=ExtractionMethod.REGEX,
                    status=ExtractionStatus.CONFIRMED,
                    confidence=0.92,
                )
    return None


def _extract_body_type(text: str, make: str, model: str, year: int) -> Optional[ListingAttribute]:
    """Extract confirmed body type from text or label model-based inference as inferred."""
    explicit_rules: list[tuple[str, re.Pattern[str]]] = [
        (
            "Commercial",
            re.compile(
                r"\b(?:3\.0\s*ton\b[^\n|]*\bchassis\b|commercial\s+vehicle|\btruck\b|\btova\s+j14\b)",
                re.IGNORECASE,
            ),
        ),
        ("Van", re.compile(r"\b(?:caravan|sprinter|cargo\s+van|passenger\s+van|\bvan\b)\b", re.IGNORECASE)),
        ("Convertible", re.compile(r"\b(?:convertible|convertable|cabriolet|roadster|spider|spyder)\b", re.IGNORECASE)),
        ("Pickup", re.compile(r"\b(?:pickup|pick-up|double\s+cab|crewmax)\b", re.IGNORECASE)),
        ("SUV", re.compile(r"\b(?:suv|sport\s+utility|4x4\s+suv)\b", re.IGNORECASE)),
        ("Sedan", re.compile(r"\bsedan\b", re.IGNORECASE)),
        ("Hatchback", re.compile(r"\bhatchback\b", re.IGNORECASE)),
        ("Wagon", re.compile(r"\b(?:wagon|estate|shooting\s+brake)\b", re.IGNORECASE)),
        ("MPV", re.compile(r"\b(?:mpv|minivan)\b", re.IGNORECASE)),
        ("Coupe", re.compile(r"\b(?:coupe|coupé)\b", re.IGNORECASE)),
    ]

    segments = _split_segments(text)
    for btype, pat in explicit_rules:
        for seg in segments:
            m = pat.search(seg)
            if m:
                return ListingAttribute(
                    attribute_name="body_type",
                    attribute_value=btype,
                    evidence=m.group(0).strip(),
                    extraction_method=ExtractionMethod.REGEX,
                    status=ExtractionStatus.CONFIRMED,
                    confidence=0.94,
                )

    # Rule-based inference from known model taxonomy (explicitly marked INFERRED)
    model_norm = model.strip().lower()
    if model_norm in _MODEL_BODY_TYPE_MAP:
        return ListingAttribute(
            attribute_name="body_type",
            attribute_value=_MODEL_BODY_TYPE_MAP[model_norm],
            evidence=f"{make} {model}",
            extraction_method=ExtractionMethod.RULE,
            status=ExtractionStatus.INFERRED,
            confidence=0.75,
        )
    return None


def _extract_engine(text: str) -> Optional[ListingAttribute]:
    """Extract engine specification (displacement/cylinders/EV)."""
    patterns = [
        re.compile(
            r"\b\d\.\d\s*l(?:tr|iter|itre)?(?:\s+(?:twin[\s\-]*turbo|turbo(?:charged)?|supercharged|biturbo|petrol|diesel|hybrid))?(?:\s+(?:v\d+|i\d|inline-\d|4-cylinder|cylinder|petrol|diesel))?",
            re.IGNORECASE,
        ),
        re.compile(r"\b\d{3,4}\s*-\s*\d{3,4}\s*cc\s*\d+\s*cylinders?\b", re.IGNORECASE),
        re.compile(r"\b\d+\s*kwh\b", re.IGNORECASE),
        re.compile(r"\b(?:twin[\s\-]*turbo\s+)?v(?:6|8|10|12)\b(?:\s+supercharged?)?", re.IGNORECASE),
        re.compile(r"\belectric\s+(?:sedan|suv|vehicle|powertrain)\b", re.IGNORECASE),
    ]
    for seg in _split_segments(text):
        for pat in patterns:
            m = pat.search(seg)
            if m:
                val = re.sub(r"\s+", " ", m.group(0).strip())
                return ListingAttribute(
                    attribute_name="engine",
                    attribute_value=val,
                    evidence=m.group(0).strip(),
                    extraction_method=ExtractionMethod.REGEX,
                    status=ExtractionStatus.CONFIRMED,
                    confidence=0.9,
                )
    return None


def _extract_service_history(text: str) -> Optional[ListingAttribute]:
    """Extract service history or agency maintenance statements."""
    pat = re.compile(
        r"(?:full\s+(?:[a-z]+\s+)?service\s+history|"
        r"agency\s+maintained|"
        r"completely\s+service(?:d)?|"
        r"just\s+serviced|"
        r"service\s+contract[^\n|•]*|"
        r"service\s+plan[^\n|•]*|"
        r"last\s+service\s*:\s*[^\n|•]+)",
        re.IGNORECASE,
    )
    for seg in _split_segments(text):
        m = pat.search(seg)
        if m:
            snippet = m.group(0).strip()
            return ListingAttribute(
                attribute_name="service_history",
                attribute_value=snippet[:120],
                evidence=snippet[:180],
                extraction_method=ExtractionMethod.REGEX,
                status=ExtractionStatus.CONFIRMED,
                confidence=0.9,
            )
    return None


def _extract_accident_free(text: str) -> Optional[ListingAttribute]:
    """Extract explicit accident-free confirmation."""
    pat = re.compile(
        r"\b(?:accident[\s\-]*free|accidents?\s+free|no\s+accidents?|zero\s+accidents?)\b",
        re.IGNORECASE,
    )
    for seg in _split_segments(text):
        m = pat.search(seg)
        if m:
            return ListingAttribute(
                attribute_name="accident_free",
                attribute_value="Yes",
                numeric_value=1.0,
                evidence=m.group(0).strip(),
                extraction_method=ExtractionMethod.REGEX,
                status=ExtractionStatus.CONFIRMED,
                confidence=0.95,
            )
    return None


def extract_attributes(
    *,
    make: str,
    model: str,
    trim: str,
    year: int,
    title: str,
    description_raw: str,
    llm_extractor: Optional[LLMExtractor] = None,
) -> list[ListingAttribute]:
    """Run deterministic extraction, optional LLM fallback, and strict evidence verification.

    Args:
        make: Listing make.
        model: Listing model.
        trim: Listing trim.
        year: Model year.
        title: Listing title.
        description_raw: Original raw description.
        llm_extractor: Optional Gemini fallback callable for unresolved attributes.

    Returns:
        List of verified ListingAttribute objects. Any attribute lacking verifiable
        evidence in the source text is dropped/rejected.
    """
    norm_title = normalize_for_matching(title)
    norm_desc = normalize_for_matching(description_raw)
    combined = f"{norm_title}\n{norm_desc}"
    full_source = f"{make} {model} {trim} {year}\n{combined}"

    extracted: dict[str, ListingAttribute] = {}
    candidates = [
        _extract_cash_price(combined),
        _extract_monthly_instalment(combined),
        _extract_down_payment(combined),
        _extract_mileage_km(combined),
        _extract_warranty(combined),
        _extract_spec_region(combined),
        _extract_transmission(combined),
        _extract_colour(combined),
        _extract_body_type(combined, make, model, year),
        _extract_engine(combined),
        _extract_service_history(combined),
        _extract_accident_free(combined),
    ]

    for attr in candidates:
        if attr is None:
            continue
        if verify_evidence(attr.evidence, full_source):
            extracted[attr.attribute_name] = attr

    # Optional Gemini fallback for unresolved key attributes on non-Arabic listings
    lang = detect_language(title, description_raw)
    key_attrs = ["cash_price", "mileage_km", "spec_region", "warranty", "transmission"]
    missing = [k for k in key_attrs if k not in extracted]
    if llm_extractor is not None and missing and lang != ListingLanguage.AR:
        try:
            fallback_attrs = llm_extractor(norm_title, norm_desc, missing)
            for attr in fallback_attrs:
                if attr.attribute_name in extracted:
                    continue
                # Evidence MUST exist verbatim in original title+description for LLM outputs
                if not verify_evidence(attr.evidence, combined):
                    continue
                # LLM-extracted cash prices must never be treated as confirmed
                if attr.attribute_name == "cash_price":
                    attr.status = ExtractionStatus.INFERRED
                attr.extraction_method = ExtractionMethod.GEMINI
                extracted[attr.attribute_name] = attr
        except Exception:
            # Graceful fallback: deterministic attributes still returned
            pass

    return list(extracted.values())
