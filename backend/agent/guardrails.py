"""Deterministic input/output guardrails and PII/secret redaction."""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from pydantic import BaseModel

from backend.config import settings

_API_KEY_RE = re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b")
_CONTACT_URL_RE = re.compile(
    r"\bhttps?://(?:wa\.me|api\.whatsapp\.com/send\?phone=|t\.me/)[^\s<>\"')]+|"
    r"\b(?:wa\.me/[+\d\-]+|t\.me/[A-Za-z0-9_]{3,})\b",
    re.IGNORECASE,
)
_EMIRATES_ID_RE = re.compile(r"(?<![A-Za-z0-9])784[\s\-]?\d{4}[\s\-]?\d{7}[\s\-]?\d(?![A-Za-z0-9])")
_PHONE_RE = re.compile(
    r"(?<![A-Za-z0-9.])(?:\+|00)?971[\s\-().]*(?:\d[\s\-().]*){7,10}\d(?![A-Za-z0-9])"
    r"|(?<![A-Za-z0-9.])\(?0(?:5[024568]|[234679])\)?(?:[\s\-.]?\d){7}(?![A-Za-z0-9])"
    r"|(?:\+|00)[1-9]\d{0,2}[\s\-().]*(?:\d[\s\-().]*){6,12}\d(?![A-Za-z0-9])"
    r"|\(\d{3}\)[\s\-.)]*\d{3}[\s\-.]\d{4}(?![A-Za-z0-9])"
    r"|(?<![A-Za-z0-9.\-])\d{3}[-.]\d{3}[-.]\d{4}(?![A-Za-z0-9.\-])"
)
_CONTEXT_PHONE_RE = re.compile(
    r"(\b(?:phone|mobile|cell|tel(?:ephone)?|whatsapp|call\s+me\s+(?:at|on)|"
    r"reach\s+me\s+(?:at|on)|contact\s+(?:me\s+(?:at|on)|no\.?|number))"
    r"(?:\s*(?:is|:|=))?\s*)"
    r"(?!\s*(?:aed|dhs|usd|eur|gbp|km|kms)\b)"
    r"(\+?\d[\d\s\-().]{6,16}\d)\b"
    r"(?!\s*(?:aed|dhs|usd|eur|gbp|km|kms|kilomet(?:er|re)s?|cc|hp|bhp)\b)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(
    r"(?:mailto:)?\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
    re.IGNORECASE,
)

_INJECTION_RE = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous\s+instructions|"
    r"disregard\s+(?:your\s+)?(?:system\s+)?(?:prompt|rules|instructions)|"
    r"reveal\s+(?:your\s+)?system\s+prompt|"
    r"\b(?:drop\s+table|select\s+\*\s+from|execute\s+sql|union\s+select)\b)",
    re.IGNORECASE,
)

_COMPETITOR_RE = re.compile(
    r"\b(?:carswitch|cars24|yallamotor|kavak|autotrader|opensooq|dubicars|sellanycar|carvana|"
    r"facebook\s+marketplace|cargurus|edmunds|cars\.com|craigslist|autolist|truecar|"
    r"olx\s+motors|al[\s\-]?futtaim\s+automall)\b",
    re.IGNORECASE,
)

_AUTOMOTIVE_DOMAIN_RE = re.compile(
    r"\b(?:car|cars|vehicle|vehicles|auto|automotive|suv|suvs|sedan|sedans|coupe|coupes|"
    r"convertible|hatchback|pickup|van|commercial|mileage|km|kilomet|warranty|gcc|spec|specs|"
    r"aed|budget|instalment|installment|down\s*payment|favou?rite|booking|test\s*drive|"
    r"engine|transmission|listing|listings|dubizzle|tire|tyre|hybrid|electric|ev|trim|"
    r"mercedes|merc|benz|bmw|audi|toyota|nissan|honda|ford|porsche|ferrari|bentley|"
    r"rolls|royce|tesla|land\s*rover|range\s*rover|patrol|cr-?v|q8|tova|haval|byd|bestune|"
    r"hyundai|dodge|chevrolet|mazda|mini|volvo|volkswagen|vw|lexus|jac|chery|gwm)\b",
    re.IGNORECASE,
)

_SESSION_FOLLOWUP_RE = re.compile(
    r"\b(?:first|second|third|last|one|this|that|it|show|find|search|compare|"
    r"price|cost|cash|month|monthly|finance|save|saved|book|viewing|"
    r"automatic|manual|colou?r|family|drive|owner)\b",
    re.IGNORECASE,
)

_OFF_TOPIC_RE = re.compile(
    r"\b(?:python|javascript|typescript|java|c\+\+|rust|html|css|sql|react|algorithm|merge\s+sort|"
    r"homework|calculus|algebra|integral|derivative|physics\s+problem|quantum|essay|"
    r"photosynthesis|biology|chemistry|mitosis|boiling\s+point|periodic\s+table|"
    r"ww2|wwii|world\s+war(?:\s+(?:i|ii|1|2))?|history\s+of|roman\s+empire|napoleon|"
    r"french\s+revolution|civil\s+war|cold\s+war|politics|political|election|elections|"
    r"president\s+of|prime\s+minister|parliament|senate|congress|"
    r"capital\s+of|population\s+of|solar\s+system|"
    r"recipe|pasta|bake\s+a\s+cake|bitcoin|crypto|stock\s+market|weather\s+forecast|"
    r"tell\s+(?:me\s+)?a\s+(?:joke|riddle)|"
    r"write\s+(?:me\s+)?a\s+(?:poem|haiku|limerick|sonnet|song|story|code|script))\b",
    re.IGNORECASE,
)

_PROMPT_LEAK_RE = re.compile(
    r"(?:MANDATORY\s+GROUNDING\s+RULES|"
    r"YOU\s+ARE\s+A\s+TRANSLATOR\s+AND\s+CONVERSATIONAL\s+INTERFACE|"
    r"SCOPE\s*&\s*SECURITY\s+GUARDRAILS|"
    r"returning_user_greeting_suggestion\s*=)",
    re.IGNORECASE,
)

_FALSE_SAVE_CLAIM_RE = re.compile(
    r"\b(?:"
    r"i(?:'ve|\s+have)?\s+saved\s+(?:this|that|the)\s+(?:car|vehicle|listing)|"
    r"i(?:'ve|\s+have)?\s+saved\s+it(?:\s+to\s+(?:your\s+)?favou?rites)?|"
    r"added\s+(?:it|this|that|the\s+\w+)\s+to\s+your\s+favou?rites|"
    r"saved\s+(?:to\s+(?:your\s+)?favou?rites|as\s+(?:a\s+)?favou?rite)"
    r")\b",
    re.IGNORECASE,
)

_FALSE_PREF_SAVE_CLAIM_RE = re.compile(
    r"\b(?:i(?:'ve|\s+have)?\s+(?:saved|updated|remembered)\s+your\s+(?:budget|preference|preferences))\b",
    re.IGNORECASE,
)

_FALSE_BOOKING_CLAIM_RE = re.compile(
    r"\b(?:your\s+(?:viewing\s+appointment|viewing|appointment|test\s*drive)\s+(?:is|has\s+been)\s+(?:booked|confirmed|scheduled)|i(?:'ve|\s+have)?\s+(?:booked|confirmed)\s+your\s+(?:viewing\s+appointment|viewing|appointment|test\s*drive))\b",
    re.IGNORECASE,
)


class GuardrailResult(BaseModel):
    """Result returned when a deterministic guardrail intercepts a turn."""

    blocked: bool
    reason: str
    reply: str


def redact_sensitive_text(text: str) -> str:
    """Redact API keys, phone numbers, and emails before storing traces or logging."""
    if not text:
        return text
    cleaned = text
    for secret in (
        settings.GEMINI_API_KEY,
        settings.ADMIN_API_KEY,
        os.getenv("ADMIN_API_KEY", ""),
    ):
        clean_secret = (secret or "").strip()
        if clean_secret and clean_secret not in {
            "your-gemini-api-key-here",
            "your-admin-api-key-here",
        }:
            cleaned = cleaned.replace(clean_secret, "[REDACTED_API_KEY]")
    cleaned = _API_KEY_RE.sub("[REDACTED_API_KEY]", cleaned)
    cleaned = _CONTACT_URL_RE.sub("[REDACTED_CONTACT]", cleaned)
    cleaned = _EMIRATES_ID_RE.sub("[REDACTED_ID]", cleaned)
    cleaned = _EMAIL_RE.sub("[REDACTED_EMAIL]", cleaned)
    cleaned = _PHONE_RE.sub("[REDACTED_PHONE]", cleaned)
    cleaned = _CONTEXT_PHONE_RE.sub(r"\1[REDACTED_PHONE]", cleaned)
    return cleaned


def sanitize_message_content(content: str) -> str:
    """Sanitize conversational text before persistent storage in the messages table."""
    return redact_sensitive_text(content)


def check_input_guardrail(message: str) -> Optional[GuardrailResult]:
    """Deterministic pre-LLM checks for prompt injection, competitors, and off-topic queries."""
    text = message.strip()
    if not text:
        return GuardrailResult(
            blocked=True,
            reason="empty_message",
            reply="Please enter a question about cars available on dubizzle.",
        )

    if _INJECTION_RE.search(text):
        return GuardrailResult(
            blocked=True,
            reason="prompt_injection",
            reply=(
                "I can only help you discover and compare used cars from the dubizzle inventory. "
                "What make, model, or budget would you like to explore?"
            ),
        )

    if _COMPETITOR_RE.search(text):
        return GuardrailResult(
            blocked=True,
            reason="competitor_mention",
            reply=(
                "I'm dubizzle's car discovery assistant, so I can't comment on or recommend "
                "other marketplaces. I'd be happy to help you search or compare cars in our "
                "dubizzle inventory instead—what are you looking for?"
            ),
        )

    if _OFF_TOPIC_RE.search(text) and not _AUTOMOTIVE_DOMAIN_RE.search(text):
        return GuardrailResult(
            blocked=True,
            reason="off_topic",
            reply=(
                "I'm here specifically to help you discover used cars on dubizzle—searching "
                "inventory, checking specs, comparing options, or saving favourites. "
                "Let me know what type of car or budget you have in mind!"
            ),
        )

    # Preserve session follow-up handling even when no explicit car keyword appears
    if _SESSION_FOLLOWUP_RE.search(text):
        return None

    return None


def check_output_guardrail(
    reply: str,
    *,
    executed_tools: list[dict[str, Any]],
) -> str:
    """Deterministic post-LLM checks ensuring secret safety, scope, and tool-action honesty."""
    cleaned = redact_sensitive_text(reply).strip()

    if _PROMPT_LEAK_RE.search(cleaned):
        return (
            "I can only help you discover and compare used cars from the dubizzle inventory. "
            "What make, model, or budget would you like to explore?"
        )

    if _COMPETITOR_RE.search(cleaned):
        cleaned = _COMPETITOR_RE.sub("another platform", cleaned)

    saved_succeeded = any(
        t.get("tool_name") == "save_favourite" and t.get("result", {}).get("saved") is True
        for t in executed_tools
    )
    if _FALSE_SAVE_CLAIM_RE.search(cleaned) and not saved_succeeded:
        cleaned = (
            "I wasn't able to verify saving that vehicle to your favourites yet. "
            "Could you confirm which listing you'd like me to save?"
        )

    pref_saved = any(
        t.get("tool_name") in {"save_preference", "update_preference"}
        and t.get("result", {}).get("saved") is True
        for t in executed_tools
    )
    if _FALSE_PREF_SAVE_CLAIM_RE.search(cleaned) and not pref_saved:
        cleaned = (
            "I haven't stored that preference permanently yet. "
            "Could you restate the preference you'd like me to remember?"
        )

    booking_confirmed = any(
        t.get("tool_name") == "confirm_booking"
        and t.get("result", {}).get("success") is True
        and t.get("result", {}).get("booking", {}).get("status") == "confirmed"
        for t in executed_tools
    )
    if _FALSE_BOOKING_CLAIM_RE.search(cleaned) and not booking_confirmed:
        cleaned = (
            "Viewing bookings cannot be confirmed directly in this step—please propose and "
            "explicitly confirm a viewing slot once booking is initiated."
        )

    return cleaned
