# Extraction Specification — dubizzle AI

## Overview

All listing attributes are extracted using a **deterministic-first, evidence-verified pipeline**:

1. **Preserve original text** (`title`, `description_raw`).
2. **Generate display-safe description** (`description_display`): replace `<br>` with newlines, unescape HTML entities, strip dealer phone numbers and URLs.
3. **Detect listing language** (`en`, `ar`, `mixed`). No Arabic NLP is attempted.
4. **Compute `content_hash`** (SHA-256 of title + raw description) for idempotency and extraction caching.
5. **Run deterministic regex & rule extractors** over normalized combined `title + description`.
6. **Optional Gemini fallback** (only if enabled, key configured, and key fields remain unresolved). Gemini output must cite exact verbatim evidence and any Gemini-inferred cash price is marked `inferred`, never `confirmed`.
7. **Validate evidence** before persistence: every extracted attribute's `evidence` snippet must exist verbatim (whitespace/case normalized) in the listing source text. Attributes failing verification are rejected.
8. **Cache results** in `extraction_cache` keyed by `content_hash`.

## Supported Attributes

| Attribute | Type | Confirmed vs Inferred Rules | Trap Guards |
|---|---|---|---|
| `cash_price` | numeric (AED) | Confirmed only for explicit cash/selling/asking/standalone vehicle prices ≥ 10,000 AED with no conflict. Conflicting or Gemini-guessed prices → `inferred`. | Excludes monthly instalments (`P.M`, `/month`, `EMI`), down payments (`DP`, `0%`), salary requirements (`WPS`), inspection/report fees (`AED 369`). Handles European dot (`25.000`) & space (`89 900`) thousands separators. |
| `monthly_instalment` | numeric (AED) | Confirmed when accompanied by `/month`, `per month`, `P.M`, `monthly`, `EMI`. | Kept strictly separate from `cash_price`. |
| `down_payment` | text / numeric | Confirmed when tied to `down payment`, `downpayment`, `DP`. | Kept strictly separate from `cash_price`. |
| `mileage_km` | numeric (km) | Confirmed when explicit odometer/mileage value found without conflict. Miles (`mi`/`miles`) are converted (`× 1.60934`). | Excludes warranty/service km caps (`or 100,000 kms`), EV range (`701KM`, `WLTP`), speed (`km/h`, `0-100 km/h`), and past/next service intervals when actual odometer exists. |
| `warranty` | text | Confirmed (`Under Warranty`, `Agency Warranty`, `Valid till ...`, `Included`, `ضمان الوكيل`). Inferred (`Warranty Available`, `Can provide warranty`, `Most vehicles include`). | Distinguishes active warranty from optional/available mentions. |
| `spec_region` | text | Confirmed (`GCC`, `US / American`, `Japanese`, `Korean`, `European`, `Canadian`, `Chinese`). | Avoids matching unrelated dealer boilerplate (e.g. "30 years in Japan"). |
| `transmission` | text | Confirmed (`Automatic`, `Tiptronic`, `DCT`, `CVT`, `Manual`). | Excludes `automatic windows`, `automatic tailgate`, `automatic headlights`, `automatic soft-top`, etc. |
| `colour` | text | Confirmed when explicitly tied to exterior paint/colour (`Exterior: Black`, `1. Red with Leather Interior`, `Finished in white exterior`). | Excludes interior colours and trim names (`Black Badge`, `Black Edition`). |
| `body_type` | text | Confirmed when explicitly stated (`SUV`, `Sedan`, `Coupe`, `Convertible`, `Van`, `Chassis`/`Commercial`). Model-based inference → `inferred`. | Commercial vehicles (`TOVA J14`) classified as `Commercial` without passenger assumptions. |
| `engine` | text | Confirmed from displacement/cylinder/EV patterns (`2.0L 4-Cylinder`, `3.0L Twin-Turbo`, `75 kWh`). | — |
| `service_history` | text | Confirmed (`Full service history`, `Agency maintained`, `Last Service: ...`). | — |
| `accident_free` | text | Confirmed (`Yes` for `accident-free`, `no accident`). | Never assumes `No` from absence of mention. |
