"""Post-generation grounding verification and DB-backed vehicle card builder."""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.inventory.aliases import BODY_TYPE_ALIASES
from backend.inventory.extraction import _ALLOWED_COLOURS
from backend.inventory.search import InventorySearchService
from backend.models.entities import ListingSummary

NO_TOOL_GROUNDING_REFUSAL = (
    "I can only share verified prices and mileage directly from our dubizzle listings. "
    "Let me search the inventory or inspect a specific listing for you—which make, model, or budget would you like me to check?"
)

_AED_MENTION_RE = re.compile(
    r"(?:(?:aed|dhs)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:aed|dhs))",
    re.IGNORECASE,
)
_KM_MENTION_RE = re.compile(
    r"\b([\d,]+)\s*(?:km|kms|kilometers|kilometres)\b",
    re.IGNORECASE,
)
_POSITIVE_WARRANTY_RE = re.compile(
    r"\b(?:(?:has|includes?|comes\s+with|carries|offers?)\s+(?:[a-z0-9\-]+\s+){0,3}"
    r"|(?:under|covered\s+(?:by|under)|with)\s+(?:[a-z0-9\-]+\s+){0,3})"
    r"warranty\b",
    re.IGNORECASE,
)
_NEGATION_WARRANTY_RE = re.compile(
    r"\b(?:does\s+not|doesn't|no\s+warranty|not\s+mention|not\s+specify|unknown|unconfirmed|optional|available)\b",
    re.IGNORECASE,
)

_LISTING_CITE_RE = re.compile(r"(?:\blisting\s*#?|#)(\d+)\b", re.IGNORECASE)

_LABELED_TRANS_RE = re.compile(
    r"^(\s*(?:[-*•]\s*)?(?:\*\*)?transmission(?:\s+type)?(?:\*\*)?\s*:\s*(?:\*\*)?)(.+)$",
    re.IGNORECASE,
)
_LABELED_SPEC_RE = re.compile(
    r"^(\s*(?:[-*•]\s*)?(?:\*\*)?spec(?:ification)?(?:\s+region)?s?(?:\*\*)?\s*:\s*(?:\*\*)?)(.+)$",
    re.IGNORECASE,
)
_LABELED_COLOUR_RE = re.compile(
    r"^(\s*(?:[-*•]\s*)?(?:\*\*)?(?:exterior\s+)?colou?r(?:\*\*)?\s*:\s*(?:\*\*)?)(.+)$",
    re.IGNORECASE,
)

_NEGATED_CLAIM_PREFIX_RE = re.compile(
    r"(?:not\s+(?:specify|specified|mention|mentioned|listed|stated|confirm|confirmed)|"
    r"doesn't\s+(?:specify|mention)|does\s+not\s+(?:specify|mention)|"
    r"no\s+mention\s+of|unknown\s+whether|whether\s+(?:it|the\s+\w+)\s+(?:is|has)|"
    r"\bor\b\s+(?:manual|automatic))\s*$",
    re.IGNORECASE,
)
_VALUE_STARTS_NEGATED_RE = re.compile(
    r"^(?:\*|_)?(?:not\s+(?:specified|mentioned|listed|stated|available|provided)|unknown|n/?a|none)\b",
    re.IGNORECASE,
)

_NON_DRIVETRAIN_AUTO_FOLLOWING = (
    r"(?!\s+(?:windows|tailgate|climate|ac|air\s+conditioning|headlights|headlamps|"
    r"lights|wipers|doors|mirrors|seats|folding|soft[\s\-]?top|roof|parking|braking|"
    r"emergency|trunk|boot|hold|high\s+beam))"
)
_PROSE_TRANS_RE = re.compile(
    rf"\b(?:(automatic){_NON_DRIVETRAIN_AUTO_FOLLOWING}|(manual)|(tiptronic|cvt|dct))"
    r"(?:\s+(?:transmission|gearbox))?\b",
    re.IGNORECASE,
)

_SPEC_TOKEN_MAP: dict[str, str] = {
    "gcc": "gcc",
    "gulf": "gcc",
    "us": "us / american",
    "usa": "us / american",
    "american": "us / american",
    "japanese": "japanese",
    "japan": "japanese",
    "korean": "korean",
    "european": "european",
    "euro": "european",
    "canadian": "canadian",
    "chinese": "chinese",
}
_PROSE_SPEC_RE = re.compile(
    r"\b(?:(gcc|gulf|us|usa|american|japanese|japan|korean|european|euro|canadian|chinese)"
    r"[\s\-]*(?:spec|specs|specifications?|import(?:ed)?)\b|(gcc)\b)",
    re.IGNORECASE,
)

_SORTED_COLOURS = sorted(_ALLOWED_COLOURS, key=len, reverse=True)
_COLOUR_ALT = "|".join(re.escape(c) for c in _SORTED_COLOURS)
_ANY_COLOUR_RE = re.compile(rf"\b({_COLOUR_ALT})\b", re.IGNORECASE)
_PROSE_COLOUR_RE = re.compile(
    rf"(?:\b(?:finished\s+in|painted\s+in)\s+({_COLOUR_ALT})\b|"
    rf"\b({_COLOUR_ALT})\s+(?:exterior(?:\s+colou?r)?|paint|colou?r)\b)",
    re.IGNORECASE,
)
_LABELED_BODY_RE = re.compile(
    r"^(\s*(?:[-*•]\s*)?(?:\*\*)?body\s*(?:type|style)?(?:\*\*)?\s*:\s*(?:\*\*)?)(.+)$",
    re.IGNORECASE,
)
_BODY_TOKEN_ALT = "|".join(re.escape(k) for k in sorted(BODY_TYPE_ALIASES.keys(), key=len, reverse=True))
_BODY_TOKEN_RE = re.compile(rf"\b({_BODY_TOKEN_ALT})\b", re.IGNORECASE)

_LABELED_ENGINE_RE = re.compile(
    r"^(\s*(?:[-*•]\s*)?(?:\*\*)?(?:engine|powertrain|motor)(?:\s+type)?(?:\*\*)?\s*:\s*(?:\*\*)?)(.+)$",
    re.IGNORECASE,
)
_LABELED_PRICE_RE = re.compile(
    r"^(\s*(?:[-*•]\s*)?(?:\*\*)?(?:cash\s+)?(?:asking\s+)?price(?:\*\*)?\s*:\s*(?:\*\*)?\s*)(.+)$",
    re.IGNORECASE,
)
_LABELED_MILEAGE_RE = re.compile(
    r"^(\s*(?:[-*•]\s*)?(?:\*\*)?(?:mileage|odometer|kilomet(?:er|re)s?)(?:\*\*)?\s*:\s*(?:\*\*)?\s*)(.+)$",
    re.IGNORECASE,
)
_LABELED_WARRANTY_RE = re.compile(
    r"^(\s*(?:[-*•]\s*)?(?:\*\*)?warranty(?:\s+status)?(?:\*\*)?\s*:\s*(?:\*\*)?\s*)(.+)$",
    re.IGNORECASE,
)
_NON_CASH_PRICE_CTX_RE = re.compile(
    r"(?:per\s+month|/\s*mo(?:nth)?|monthly|instal+ment|down\s*payment|under\b|below\b|above\b|over\b|between\b|budget\b|up\s+to\b)",
    re.IGNORECASE,
)
_ENGINE_TOKEN_RE = re.compile(
    r"\b(\d\.\d\s*l(?:iter|itre|tr)?|\d{3,4}\s*cc|\d{2,3}\s*kwh|v(?:6|8|10|12))\b",
    re.IGNORECASE,
)
_PROSE_ENGINE_RE = re.compile(
    r"(?:\b(?:powered\s+by|equipped\s+with|features?\s+an?|under\s+the\s+hood[^.\n]{0,20}|"
    r"\b\d\.\d\s*l\s+(?:engine|turbo|v\d)|v(?:6|8|10|12)\s+(?:engine|turbo|petrol|motor))\b)"
    r"[^.\n]{0,30}",
    re.IGNORECASE,
)


def build_vehicle_cards(
    card_ids: list[int],
    inventory: InventorySearchService,
) -> list[ListingSummary]:
    """Build vehicle cards strictly from SQLite records in preserved order."""
    seen: set[int] = set()
    ordered_unique: list[int] = []
    for lid in card_ids:
        if lid not in seen:
            seen.add(lid)
            ordered_unique.append(lid)
    if not ordered_unique:
        return []
    return inventory.get_vehicle_summaries(ordered_unique)


def _collect_allowed_numbers(obj: Any, bucket: set[int]) -> None:
    """Recursively collect integer-rounded numeric values from validated tool results."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        bucket.add(int(round(obj)))
    elif isinstance(obj, str):
        for m in re.findall(r"\b\d[\d,]*\b", obj):
            try:
                bucket.add(int(m.replace(",", "")))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_allowed_numbers(v, bucket)
    elif isinstance(obj, list):
        for item in obj:
            _collect_allowed_numbers(item, bucket)


def _extract_user_numbers(user_message: str) -> set[int]:
    nums: set[int] = set()
    for m in re.findall(r"\b(\d[\d,]*)(k)?\b", user_message, re.IGNORECASE):
        raw = int(m[0].replace(",", ""))
        if m[1]:
            nums.add(raw * 1000)
        else:
            nums.add(raw)
    return nums


def _extract_summary_specs(item: dict[str, Any]) -> Optional[tuple[int, dict[str, Any]]]:
    lid = item.get("listing_id")
    if not isinstance(lid, int):
        return None
    raw_price = item.get("confirmed_cash_price_aed")
    if raw_price is None and item.get("cash_price") is not None:
        status_val = item.get("cash_price_status")
        if status_val == "confirmed":
            raw_price = item.get("cash_price")
    return lid, {
        "transmission": str(item["transmission"]).strip() if item.get("transmission") else None,
        "spec_region": str(item["spec_region"]).strip() if item.get("spec_region") else None,
        "colour": str(item["colour"]).strip() if item.get("colour") else None,
        "body_type": str(item["body_type"]).strip() if item.get("body_type") else None,
        "engine": str(item["engine"]).strip() if item.get("engine") else None,
        "warranty": str(item["warranty"]).strip() if item.get("warranty") else None,
        "warranty_status": str(item["warranty_status"]).strip() if item.get("warranty_status") else None,
        "cash_price": int(round(float(raw_price))) if raw_price is not None else None,
        "mileage_km": int(round(float(item["mileage_km"]))) if item.get("mileage_km") is not None else None,
    }


def _merge_listing_spec(
    specs_by_id: dict[int, dict[str, Any]],
    lid: int,
    new_spec: dict[str, Any],
) -> None:
    existing = specs_by_id.setdefault(lid, {})
    for key, val in new_spec.items():
        if val is not None or key not in existing:
            existing[key] = val


def _collect_tool_listing_specs(tool_records: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Build per-listing verified specs from tool outputs without losing richer detail attributes."""
    specs_by_id: dict[int, dict[str, Any]] = {}
    for rec in tool_records:
        name = rec.get("tool_name")
        res = rec.get("result") or {}
        if not isinstance(res, dict):
            continue

        if name == "search_inventory":
            for item in res.get("listings") or []:
                if isinstance(item, dict):
                    parsed = _extract_summary_specs(item)
                    if parsed:
                        _merge_listing_spec(specs_by_id, parsed[0], parsed[1])
        elif name == "get_listing_details" and res.get("found") is True:
            lid = res.get("listing_id")
            if isinstance(lid, int):
                attrs = res.get("attributes") or {}

                def _attr_val(attr_key: str) -> Optional[str]:
                    entry = attrs.get(attr_key)
                    if isinstance(entry, dict) and entry.get("value") is not None:
                        return str(entry["value"]).strip()
                    return None

                cp_entry = attrs.get("cash_price")
                cash_price_val: Optional[int] = None
                if (
                    isinstance(cp_entry, dict)
                    and cp_entry.get("status") == "confirmed"
                    and cp_entry.get("numeric_value") is not None
                ):
                    cash_price_val = int(round(float(cp_entry["numeric_value"])))
                km_entry = attrs.get("mileage_km")
                mileage_val: Optional[int] = None
                if isinstance(km_entry, dict) and km_entry.get("numeric_value") is not None:
                    mileage_val = int(round(float(km_entry["numeric_value"])))
                w_entry = attrs.get("warranty")
                w_status = w_entry.get("status") if isinstance(w_entry, dict) else None

                _merge_listing_spec(
                    specs_by_id,
                    lid,
                    {
                        "transmission": _attr_val("transmission"),
                        "spec_region": _attr_val("spec_region"),
                        "colour": _attr_val("colour"),
                        "body_type": _attr_val("body_type"),
                        "engine": _attr_val("engine"),
                        "warranty": _attr_val("warranty"),
                        "warranty_status": str(w_status).strip() if w_status else None,
                        "cash_price": cash_price_val,
                        "mileage_km": mileage_val,
                    },
                )
        elif name == "resolve_listing_reference" and res.get("status") == "resolved":
            listing = res.get("listing")
            if isinstance(listing, dict):
                parsed = _extract_summary_specs(listing)
                if parsed:
                    _merge_listing_spec(specs_by_id, parsed[0], parsed[1])
            for item in res.get("resolved_listings") or []:
                if isinstance(item, dict):
                    parsed = _extract_summary_specs(item)
                    if parsed:
                        _merge_listing_spec(specs_by_id, parsed[0], parsed[1])
        elif name in {"save_favourite", "get_booking_details"}:
            veh = res.get("vehicle")
            if isinstance(veh, dict):
                parsed = _extract_summary_specs(veh)
                if parsed:
                    _merge_listing_spec(specs_by_id, parsed[0], parsed[1])
        elif name == "get_saved_vehicles":
            for item in res.get("saved_vehicles") or []:
                if isinstance(item, dict):
                    parsed = _extract_summary_specs(item)
                    if parsed:
                        _merge_listing_spec(specs_by_id, parsed[0], parsed[1])
        elif name == "get_user_context":
            for item in res.get("favourites") or []:
                if isinstance(item, dict):
                    parsed = _extract_summary_specs(item)
                    if parsed:
                        _merge_listing_spec(specs_by_id, parsed[0], parsed[1])
    return specs_by_id


def _allowed_values_for_lids(
    lids: list[int],
    specs_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    allowed: dict[str, Any] = {
        "transmission": set(),
        "spec_region": set(),
        "colour": set(),
        "body_type": set(),
        "engine": set(),
        "cash_price": set(),
        "mileage_km": set(),
        "warranties": [],
    }
    for lid in lids:
        spec = specs_by_id.get(lid) or {}
        trans = (spec.get("transmission") or "").lower()
        if trans:
            allowed["transmission"].add(trans)
            if trans == "automatic":
                allowed["transmission"].update({"tiptronic", "cvt", "dct"})
        reg = (spec.get("spec_region") or "").lower()
        if reg:
            allowed["spec_region"].add(reg)
        col = (spec.get("colour") or "").lower()
        if col:
            allowed["colour"].add(col)
            allowed["colour"].update(col.split())
        body = (spec.get("body_type") or "").lower()
        if body:
            allowed["body_type"].add(body)
        eng = (spec.get("engine") or "").lower()
        if eng:
            allowed["engine"].add(_norm_engine_text(eng))
        if spec.get("cash_price") is not None:
            allowed["cash_price"].add(int(spec["cash_price"]))
        if spec.get("mileage_km") is not None:
            allowed["mileage_km"].add(int(spec["mileage_km"]))
        allowed["warranties"].append((spec.get("warranty"), spec.get("warranty_status")))
    return allowed


def _norm_engine_text(text: str) -> str:
    low = " ".join(text.lower().split())
    return re.sub(r"(\d\.\d)\s*(?:liters?|litres?|ltr|l)\b", r"\1l", low)


def _is_transmission_allowed(tok: str, allowed_trans: set[str]) -> bool:
    low = tok.lower()
    if low in allowed_trans:
        return True
    if low in {"tiptronic", "cvt", "dct"} and "automatic" in allowed_trans:
        return True
    return False


def _is_spec_allowed(tok: str, allowed_specs: set[str]) -> bool:
    canon = _SPEC_TOKEN_MAP.get(tok.lower())
    return bool(canon and canon in allowed_specs)


def _is_colour_allowed(tok: str, allowed_colours: set[str]) -> bool:
    low = " ".join(tok.lower().split())
    if low in allowed_colours:
        return True
    return any(low in ac or ac in low for ac in allowed_colours)


def _is_body_type_allowed(tok: str, allowed_bodies: set[str]) -> bool:
    canon = BODY_TYPE_ALIASES.get(tok.lower(), tok.lower()).lower()
    return canon in allowed_bodies


def _is_engine_allowed(tok: str, allowed_engines: set[str]) -> bool:
    norm = _norm_engine_text(tok).replace(" ", "")
    return any(norm in ae.replace(" ", "") or ae.replace(" ", "") in norm for ae in allowed_engines)


def _sanitize_spec_segment(
    segment: str,
    allowed: dict[str, Any],
    *,
    check_prose_numbers: bool = False,
) -> str:
    """Ensure a listing-scoped text segment never asserts unverified specs, prices, mileage, or warranty."""
    out_lines: list[str] = []
    for line in segment.splitlines():
        if _LABELED_TRANS_RE.match(line):
            m_lbl = _LABELED_TRANS_RE.match(line)
            assert m_lbl is not None
            prefix, val_part = m_lbl.group(1), m_lbl.group(2)
            if not _VALUE_STARTS_NEGATED_RE.match(val_part.strip()):
                m_tr = _PROSE_TRANS_RE.search(val_part)
                if m_tr:
                    tok = next(g for g in m_tr.groups() if g is not None)
                    if not _is_transmission_allowed(tok, allowed["transmission"]):
                        out_lines.append(f"{prefix}Not specified in the listing.")
                        continue

        if _LABELED_SPEC_RE.match(line):
            m_lbl = _LABELED_SPEC_RE.match(line)
            assert m_lbl is not None
            prefix, val_part = m_lbl.group(1), m_lbl.group(2)
            if not _VALUE_STARTS_NEGATED_RE.match(val_part.strip()):
                found_specs = [
                    next(g for g in m.groups() if g is not None)
                    for m in _PROSE_SPEC_RE.finditer(val_part)
                ]
                if not found_specs:
                    for raw_tok in re.findall(r"[a-z]+", val_part.lower()):
                        if raw_tok in _SPEC_TOKEN_MAP:
                            found_specs.append(raw_tok)
                if any(not _is_spec_allowed(tok, allowed["spec_region"]) for tok in found_specs):
                    out_lines.append(f"{prefix}Not specified in the listing.")
                    continue

        if _LABELED_COLOUR_RE.match(line) and "interior" not in line.lower():
            m_lbl = _LABELED_COLOUR_RE.match(line)
            assert m_lbl is not None
            prefix, val_part = m_lbl.group(1), m_lbl.group(2)
            if not _VALUE_STARTS_NEGATED_RE.match(val_part.strip()):
                found_cols = [m.group(1) for m in _ANY_COLOUR_RE.finditer(val_part)]
                if any(not _is_colour_allowed(c, allowed["colour"]) for c in found_cols):
                    out_lines.append(f"{prefix}Not specified in the listing.")
                    continue

        if _LABELED_BODY_RE.match(line):
            m_lbl = _LABELED_BODY_RE.match(line)
            assert m_lbl is not None
            prefix, val_part = m_lbl.group(1), m_lbl.group(2)
            if not _VALUE_STARTS_NEGATED_RE.match(val_part.strip()):
                found_bodies = [m.group(1) for m in _BODY_TOKEN_RE.finditer(val_part)]
                if any(not _is_body_type_allowed(b, allowed["body_type"]) for b in found_bodies):
                    out_lines.append(f"{prefix}Not specified in the listing.")
                    continue

        if _LABELED_ENGINE_RE.match(line):
            m_lbl = _LABELED_ENGINE_RE.match(line)
            assert m_lbl is not None
            prefix, val_part = m_lbl.group(1), m_lbl.group(2)
            if not _VALUE_STARTS_NEGATED_RE.match(val_part.strip()):
                found_engs = [m.group(1) for m in _ENGINE_TOKEN_RE.finditer(val_part)]
                if any(not _is_engine_allowed(e, allowed["engine"]) for e in found_engs):
                    out_lines.append(f"{prefix}Not specified in the listing.")
                    continue

        if (
            _LABELED_PRICE_RE.match(line)
            and "monthly" not in line.lower()
            and "instalment" not in line.lower()
            and "installment" not in line.lower()
            and "down" not in line.lower()
        ):
            m_lbl = _LABELED_PRICE_RE.match(line)
            assert m_lbl is not None
            prefix, val_part = m_lbl.group(1), m_lbl.group(2)
            if not _VALUE_STARTS_NEGATED_RE.match(val_part.strip()):
                nums = [int(m.group(1).replace(",", "")) for m in re.finditer(r"\b(\d[\d,]*)\b", val_part)]
                if any(v >= 500 and v not in allowed["cash_price"] for v in nums):
                    out_lines.append(f"{prefix}Cash price not specified in the listing.")
                    continue

        if _LABELED_MILEAGE_RE.match(line):
            m_lbl = _LABELED_MILEAGE_RE.match(line)
            assert m_lbl is not None
            prefix, val_part = m_lbl.group(1), m_lbl.group(2)
            if not _VALUE_STARTS_NEGATED_RE.match(val_part.strip()):
                nums = [int(m.group(1).replace(",", "")) for m in re.finditer(r"\b(\d[\d,]*)\b", val_part)]
                if any(v >= 100 and v not in allowed["mileage_km"] for v in nums):
                    out_lines.append(f"{prefix}Not specified in the listing.")
                    continue

        if _LABELED_WARRANTY_RE.match(line):
            m_lbl = _LABELED_WARRANTY_RE.match(line)
            assert m_lbl is not None
            prefix, val_part = m_lbl.group(1), m_lbl.group(2)
            warranties = allowed.get("warranties") or []
            if warranties and not _VALUE_STARTS_NEGATED_RE.match(val_part.strip()):
                all_none_or_no = all(w is None or str(w).lower() == "no" for w, _ in warranties)
                if all_none_or_no and _POSITIVE_WARRANTY_RE.search(val_part) and not _NEGATION_WARRANTY_RE.search(val_part):
                    out_lines.append(f"{prefix}Not specified in the listing.")
                    continue
                has_confirmed_w = any(
                    w and str(w).lower() != "no" and st == "confirmed" for w, st in warranties
                )
                has_inferred_w = any(
                    w and str(w).lower() != "no" and st == "inferred" for w, st in warranties
                )
                if (
                    not has_confirmed_w
                    and has_inferred_w
                    and _POSITIVE_WARRANTY_RE.search(val_part)
                    and not any(tok in val_part.lower() for tok in ("inferred", "optional", "available", "mentioned"))
                ):
                    out_lines.append(
                        f"{prefix}{val_part.strip()} (mentioned as optional/available, not confirmed as included)"
                    )
                    continue

        # Check prose mentions within non-labeled lines (skip explicit negations)
        updated = line
        for m in reversed(list(_PROSE_TRANS_RE.finditer(updated))):
            tok = next(g for g in m.groups() if g is not None)
            if _is_transmission_allowed(tok, allowed["transmission"]):
                continue
            prefix_ctx = updated[max(0, m.start() - 55) : m.start()]
            if _NEGATED_CLAIM_PREFIX_RE.search(prefix_ctx):
                continue
            # Also skip if whole sentence explicitly states transmission is not mentioned/specified
            if re.search(
                r"\b(?:does\s+not|doesn't|not)\s+(?:mention|specify|list)\b",
                updated[max(0, m.start() - 60) : min(len(updated), m.end() + 45)],
                re.IGNORECASE,
            ):
                continue
            updated = (
                updated[: m.start()]
                + "transmission (not specified in the listing)"
                + updated[m.end() :]
            )

        for m in reversed(list(_PROSE_SPEC_RE.finditer(updated))):
            tok = next(g for g in m.groups() if g is not None)
            if _is_spec_allowed(tok, allowed["spec_region"]):
                continue
            prefix_ctx = updated[max(0, m.start() - 55) : m.start()]
            if _NEGATED_CLAIM_PREFIX_RE.search(prefix_ctx):
                continue
            if re.search(
                r"\b(?:does\s+not|doesn't|not)\s+(?:mention|specify|list)\b",
                updated[max(0, m.start() - 60) : min(len(updated), m.end() + 45)],
                re.IGNORECASE,
            ):
                continue
            updated = (
                updated[: m.start()]
                + "specification region (not specified in the listing)"
                + updated[m.end() :]
            )

        for m in reversed(list(_PROSE_COLOUR_RE.finditer(updated))):
            tok = next(g for g in m.groups() if g is not None)
            if _is_colour_allowed(tok, allowed["colour"]):
                continue
            prefix_ctx = updated[max(0, m.start() - 55) : m.start()]
            if _NEGATED_CLAIM_PREFIX_RE.search(prefix_ctx):
                continue
            updated = (
                updated[: m.start()]
                + "exterior colour (not specified in the listing)"
                + updated[m.end() :]
            )

        for m in reversed(list(_PROSE_ENGINE_RE.finditer(updated))):
            chunk = m.group(0)
            toks = [em.group(1) for em in _ENGINE_TOKEN_RE.finditer(chunk)]
            if not toks or all(_is_engine_allowed(t, allowed["engine"]) for t in toks):
                continue
            prefix_ctx = updated[max(0, m.start() - 55) : m.start()]
            if _NEGATED_CLAIM_PREFIX_RE.search(prefix_ctx):
                continue
            updated = (
                updated[: m.start()]
                + "engine specification (not specified in the listing)"
                + updated[m.end() :]
            )

        if check_prose_numbers:
            for m in reversed(list(_AED_MENTION_RE.finditer(updated))):
                raw_num = (m.group(1) or m.group(2) or "").replace(",", "")
                try:
                    val = int(round(float(raw_num)))
                except ValueError:
                    continue
                if val < 500 or val in allowed["cash_price"]:
                    continue
                ctx_win = updated[max(0, m.start() - 35) : min(len(updated), m.end() + 35)]
                if _NON_CASH_PRICE_CTX_RE.search(ctx_win) or _NEGATED_CLAIM_PREFIX_RE.search(
                    updated[max(0, m.start() - 45) : m.start()]
                ):
                    continue
                updated = (
                    updated[: m.start()]
                    + "cash price (not specified in the listing)"
                    + updated[m.end() :]
                )

            for m in reversed(list(_KM_MENTION_RE.finditer(updated))):
                raw_num = m.group(1).replace(",", "")
                try:
                    val = int(raw_num)
                except ValueError:
                    continue
                if val < 100 or val in allowed["mileage_km"]:
                    continue
                if _NEGATED_CLAIM_PREFIX_RE.search(updated[max(0, m.start() - 45) : m.start()]):
                    continue
                updated = (
                    updated[: m.start()]
                    + "mileage (not specified in the listing)"
                    + updated[m.end() :]
                )

        out_lines.append(updated)

    return "\n".join(out_lines)


_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _split_md_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [c.strip() for c in stripped.split("|")]


def _format_md_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _validate_table_cell(
    attr_label: str,
    cell: str,
    allowed: dict[str, Any],
) -> str:
    """Validate one table cell against a specific attribute label and listing-scoped allowed values."""
    clean = cell.strip()
    if not clean or clean in {"-", "—", "N/A", "n/a"} or _VALUE_STARTS_NEGATED_RE.match(clean):
        return cell

    low_lbl = attr_label.lower()

    if "trans" in low_lbl or "gearbox" in low_lbl:
        m_tr = _PROSE_TRANS_RE.search(clean)
        if m_tr:
            tok = next(g for g in m_tr.groups() if g is not None)
            if not _is_transmission_allowed(tok, allowed["transmission"]):
                return "Not specified in the listing."

    if "spec" in low_lbl or "region" in low_lbl or "origin" in low_lbl:
        found_specs = [
            next(g for g in m.groups() if g is not None)
            for m in _PROSE_SPEC_RE.finditer(clean)
        ]
        if not found_specs:
            for raw_tok in re.findall(r"[a-z]+", clean.lower()):
                if raw_tok in _SPEC_TOKEN_MAP:
                    found_specs.append(raw_tok)
        if found_specs and any(not _is_spec_allowed(tok, allowed["spec_region"]) for tok in found_specs):
            return "Not specified in the listing."

    if ("colou" in low_lbl or "color" in low_lbl) and "interior" not in low_lbl:
        found_cols = [m.group(1) for m in _ANY_COLOUR_RE.finditer(clean)]
        if found_cols and any(not _is_colour_allowed(c, allowed["colour"]) for c in found_cols):
            return "Not specified in the listing."

    if "body" in low_lbl:
        found_bodies = [m.group(1) for m in _BODY_TOKEN_RE.finditer(clean)]
        if found_bodies and any(not _is_body_type_allowed(b, allowed["body_type"]) for b in found_bodies):
            return "Not specified in the listing."

    if "engine" in low_lbl or "powertrain" in low_lbl or "motor" in low_lbl:
        found_engs = [m.group(1) for m in _ENGINE_TOKEN_RE.finditer(clean)]
        if found_engs and any(not _is_engine_allowed(e, allowed["engine"]) for e in found_engs):
            return "Not specified in the listing."

    if (
        ("price" in low_lbl or "cash" in low_lbl)
        and "monthly" not in low_lbl
        and "instalment" not in low_lbl
        and "installment" not in low_lbl
        and "down" not in low_lbl
    ):
        for m in re.finditer(r"\b(\d[\d,]*)\b", clean):
            try:
                val = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if val >= 500 and val not in allowed["cash_price"]:
                return "Cash price not specified in the listing."

    if "mileage" in low_lbl or "odometer" in low_lbl or low_lbl.strip() in {"km", "kms"}:
        for m in re.finditer(r"\b(\d[\d,]*)\b", clean):
            try:
                val = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if val >= 100 and val not in allowed["mileage_km"]:
                return "Not specified in the listing."

    if "warranty" in low_lbl:
        warranties = allowed["warranties"]
        all_none_or_no = bool(warranties) and all(
            w is None or str(w).lower() == "no" for w, _ in warranties
        )
        if all_none_or_no and not _NEGATION_WARRANTY_RE.search(clean):
            return "Not specified in the listing."

    # Also enforce generic segment sanitization within the cell (skipping row-label logic)
    return _sanitize_spec_segment(clean, allowed)


def _sanitize_markdown_table(
    table_lines: list[str],
    specs_by_id: dict[int, dict[str, Any]],
) -> Optional[list[str]]:
    """Sanitize a markdown comparison table cell-by-cell when columns or rows map to listings."""
    if len(table_lines) < 3 or not _TABLE_SEP_RE.match(table_lines[1]):
        return None

    headers = _split_md_row(table_lines[0])
    if len(headers) < 2:
        return None

    # Mode A: Vehicle IDs in header columns ("| Spec | [Listing #86] | [Listing #47] |")
    col_lids: dict[int, list[int]] = {}
    for col_idx, h_text in enumerate(headers):
        ids = [
            int(m.group(1))
            for m in _LISTING_CITE_RE.finditer(h_text)
            if int(m.group(1)) in specs_by_id
        ]
        if ids:
            col_lids[col_idx] = ids

    if col_lids:
        out = [table_lines[0], table_lines[1]]
        for row_line in table_lines[2:]:
            cells = _split_md_row(row_line)
            if len(cells) < 2:
                out.append(row_line)
                continue
            attr_label = cells[0]
            new_cells = list(cells)
            for col_idx, lids in col_lids.items():
                if col_idx < len(new_cells):
                    col_allowed = _allowed_values_for_lids(lids, specs_by_id)
                    new_cells[col_idx] = _validate_table_cell(
                        attr_label, new_cells[col_idx], col_allowed
                    )
            out.append(_format_md_row(new_cells))
        return out

    # Mode B: Vehicle IDs per row ("| Listing #86 | Automatic | Red |")
    row_has_cites = any(
        any(int(m.group(1)) in specs_by_id for m in _LISTING_CITE_RE.finditer(line))
        for line in table_lines[2:]
    )
    if row_has_cites:
        out = [table_lines[0], table_lines[1]]
        for row_line in table_lines[2:]:
            cells = _split_md_row(row_line)
            row_ids = [
                int(m.group(1))
                for m in _LISTING_CITE_RE.finditer(row_line)
                if int(m.group(1)) in specs_by_id
            ]
            if not row_ids:
                out.append(row_line)
                continue
            row_allowed = _allowed_values_for_lids(row_ids, specs_by_id)
            new_cells = list(cells)
            for col_idx in range(len(new_cells)):
                if col_idx < len(headers) and _LISTING_CITE_RE.search(new_cells[col_idx]) and col_idx == 0:
                    continue
                col_lbl = headers[col_idx] if col_idx < len(headers) else ""
                new_cells[col_idx] = _validate_table_cell(
                    col_lbl, new_cells[col_idx], row_allowed
                )
            out.append(_format_md_row(new_cells))
        return out

    return None


def _validate_text_block(
    text: str,
    specs_by_id: dict[int, dict[str, Any]],
) -> str:
    """Validate prose/bullet text segmented by [Listing #ID] citations."""
    cites = [
        (m.start(), int(m.group(1)))
        for m in _LISTING_CITE_RE.finditer(text)
        if int(m.group(1)) in specs_by_id
    ]
    if not cites:
        allowed = _allowed_values_for_lids(list(specs_by_id.keys()), specs_by_id)
        return _sanitize_spec_segment(text, allowed)

    all_lids = list(specs_by_id.keys())
    chunks: list[str] = []
    first_pos = cites[0][0]
    if first_pos > 0:
        intro = text[:first_pos]
        intro_lids = [cites[0][1]] if len(specs_by_id) == 1 else all_lids
        chunks.append(_sanitize_spec_segment(intro, _allowed_values_for_lids(intro_lids, specs_by_id)))

    for idx, (pos, lid) in enumerate(cites):
        end_pos = cites[idx + 1][0] if idx + 1 < len(cites) else len(text)
        seg = text[pos:end_pos]
        chunks.append(
            _sanitize_spec_segment(
                seg,
                _allowed_values_for_lids([lid], specs_by_id),
                check_prose_numbers=True,
            )
        )

    return "".join(chunks)


def _validate_vehicle_specs(
    reply: str,
    specs_by_id: dict[int, dict[str, Any]],
) -> str:
    """Validate transmission, spec_region, colour, body_type, engine, and comparison tables."""
    if not specs_by_id:
        return reply

    lines = reply.splitlines()
    out_blocks: list[str] = []
    text_buf: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if (
            "|" in line
            and idx + 1 < len(lines)
            and _TABLE_SEP_RE.match(lines[idx + 1])
        ):
            end = idx + 2
            while end < len(lines) and "|" in lines[end] and lines[end].strip():
                end += 1
            table_lines = lines[idx:end]
            sanitized_table = _sanitize_markdown_table(table_lines, specs_by_id)
            if sanitized_table is not None:
                if text_buf:
                    out_blocks.append(_validate_text_block("\n".join(text_buf), specs_by_id))
                    text_buf = []
                out_blocks.extend(sanitized_table)
                idx = end
                continue
        text_buf.append(line)
        idx += 1

    if text_buf:
        out_blocks.append(_validate_text_block("\n".join(text_buf), specs_by_id))

    return "\n".join(out_blocks)


def verify_grounding(
    reply: str,
    *,
    user_message: str,
    tool_records: list[dict[str, Any]],
    trusted_context_text: Optional[str] = None,
) -> str:
    """Verify numerical, warranty, and vehicle-spec facts against current-turn tool results."""
    allowed_numbers: set[int] = _extract_user_numbers(user_message)
    if trusted_context_text:
        _collect_allowed_numbers(trusted_context_text, allowed_numbers)
    for rec in tool_records:
        _collect_allowed_numbers(rec.get("result"), allowed_numbers)

    # Check if reply cites AED prices or km mileages absent from tool results & trusted context
    ungrounded_claims: list[str] = []
    for m in _AED_MENTION_RE.finditer(reply):
        raw_num = (m.group(1) or m.group(2) or "").replace(",", "")
        try:
            val = int(round(float(raw_num)))
        except ValueError:
            continue
        if val >= 500 and val not in allowed_numbers:
            ungrounded_claims.append(f"AED {val:,}")

    for m in _KM_MENTION_RE.finditer(reply):
        raw_num = m.group(1).replace(",", "")
        try:
            val = int(raw_num)
        except ValueError:
            continue
        if val >= 100 and val not in allowed_numbers:
            ungrounded_claims.append(f"{val:,} km")

    # Check warranty claims against single-listing detail/reference tool results
    checked_warranties: list[tuple[bool, bool]] = []  # (is_unknown_or_no, is_inferred)
    for rec in tool_records:
        name = rec.get("tool_name")
        res = rec.get("result") or {}
        if name == "get_listing_details" and res.get("found") is True:
            attrs = res.get("attributes") or {}
            w = attrs.get("warranty")
            if w is None or (w.get("value") or "").lower() == "no":
                checked_warranties.append((True, False))
            elif w.get("status") == "inferred":
                checked_warranties.append((False, True))
            else:
                checked_warranties.append((False, False))
        elif name == "resolve_listing_reference" and res.get("status") == "resolved":
            listing = res.get("listing") or {}
            if listing.get("warranty") is None or (listing.get("warranty") or "").lower() == "no":
                checked_warranties.append((True, False))
            elif listing.get("warranty_status") == "inferred":
                checked_warranties.append((False, True))
            else:
                checked_warranties.append((False, False))

    specs_by_id = _collect_tool_listing_specs(tool_records)
    if not checked_warranties and specs_by_id:
        for spec in specs_by_id.values():
            w_val = spec.get("warranty")
            w_st = spec.get("warranty_status")
            if w_val is None or str(w_val).lower() == "no":
                checked_warranties.append((True, False))
            elif w_st == "inferred":
                checked_warranties.append((False, True))
            else:
                checked_warranties.append((False, False))

    has_confirmed_warranty = any(not unk and not inf for unk, inf in checked_warranties)
    detail_warranty_unknown = bool(checked_warranties) and not has_confirmed_warranty and all(
        unk for unk, _ in checked_warranties
    )
    detail_warranty_inferred = bool(checked_warranties) and not has_confirmed_warranty and any(
        inf for _, inf in checked_warranties
    )

    if detail_warranty_unknown and _POSITIVE_WARRANTY_RE.search(reply) and not _NEGATION_WARRANTY_RE.search(reply):
        return (
            "The advertisement does not mention confirmed warranty coverage for this vehicle. "
            "I can note that as a question for a viewing if you'd like."
        )

    notes: list[str] = []
    if detail_warranty_inferred and _POSITIVE_WARRANTY_RE.search(reply) and "inferred" not in reply.lower() and "optional" not in reply.lower() and "available" not in reply.lower():
        notes.append(
            "Note: warranty in this listing is only mentioned as optional/available (inferred), not confirmed as included."
        )

    if ungrounded_claims:
        if not tool_records:
            return NO_TOOL_GROUNDING_REFUSAL
        notes.append(
            "Verification note: please rely on the verified figures shown on the vehicle cards below, as unverified figures ("
            + ", ".join(dict.fromkeys(ungrounded_claims))
            + ") are not confirmed in the listing records."
        )

    verified_reply = _validate_vehicle_specs(reply, specs_by_id)

    if notes:
        return f"{verified_reply.rstrip()}\n\n" + " ".join(notes)
    return verified_reply
