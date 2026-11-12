# Architectural Decision Records — dubizzle AI

## ADR-001: Use `raw dataset` sheet as authoritative source
**Date**: 2026-11-10
**Status**: Approved

**Context**: The workbook contains two sheets with different inventories. The `cleaned dataset` (35 makes, 0 Honda, pre-assigned IDs) was the basis for the original specification. The `raw dataset` (29 makes, 1 Honda, richer descriptions) offers more extraction material.

**Decision**: Use `raw dataset` exclusively. Do not merge sheets.

**Rationale**:
- Richer descriptions (mean 1,144 vs 747 chars)
- More extraction signals (10 cash prices vs 4, 62 mileage mentions vs 41)
- More recent data (includes 2026 models)
- Better demonstration material

**Consequences**: Some spec statistics (35 makes, 0 Honda, 20 Mercedes-Benz) do not match. All project statistics are recalculated from actual data.

---

## ADR-002: SQLite + FTS5 instead of vector search
**Date**: 2026-11-10
**Status**: Approved

**Context**: The inventory contains 100 listings. Options considered: SQLite FTS5, Elasticsearch, pgvector, ChromaDB.

**Decision**: Use SQLite with FTS5 full-text search.

**Rationale**:
- 100 listings is trivially small for any search engine
- FTS5 provides BM25-based relevance ranking natively
- No additional infrastructure needed
- Structured filters (make, year, price) are exact matches — SQL is ideal
- Zero deployment complexity for assessment
- Vector search adds embedding generation cost and complexity without measurable benefit at this scale

**Consequences**: If the dataset scaled to 10,000+ listings with semantic search needs, vector search should be reconsidered.

---

## ADR-003: Custom agent loop instead of LangChain/framework
**Date**: 2026-11-10
**Status**: Approved

**Context**: Need an LLM-powered conversational agent with tool calling.

**Decision**: Build a custom bounded tool-calling loop using the official `google-genai` SDK directly.

**Rationale**:
- Full control over tool validation, error handling, and iteration limits
- No black-box abstractions hiding tool execution
- Easier to test with mocks
- Lighter dependency footprint
- Transparent tracing of every tool call
- The spec explicitly requires avoiding heavyweight frameworks

**Consequences**: More code to write for orchestration, but complete visibility and testability.

---

## ADR-004: Suspected near-duplicate listing transparency
**Date**: 2026-11-10
**Status**: Approved

**Context**: 17 rows share identical titles/descriptions across 7 groups (10 additional group members). Without vehicle-level identifiers (VIN, plate), identical ad text does not prove whether listings represent the same physical car or multiple vehicles listed with a dealer template.

**Decision**: Preserve all 100 rows with unique `listing_id`s. Assign `duplicate_group` flags for suspected near-duplicate listing groups. Report listing counts (`total_matches`), distinct ad group counts (`distinct_listing_groups`), suspected duplicate-group counts (`suspected_duplicate_groups`), and related-listing counts (`related_duplicate_listings`) separately. Never report 5 similar Audi Q8 listings as "1 confirmed vehicle".

**Rationale**:
- Preserves original data integrity and avoids unverified physical-vehicle collapse
- Makes suspected near-duplication transparent to users and downstream LLM responses
- Prevents repeated template listings from appearing as independent evidence of market availability

**Consequences**: Search results surface all records while clearly annotating `is_suspected_duplicate`, `duplicate_group`, and `suspected_duplicate_ids`.

---

## ADR-005: No Arabic NLP
**Date**: 2026-11-10
**Status**: Approved

**Context**: 9 listings have predominantly Arabic text and 8 have mixed Arabic/English text (83 English). Arabic NLP would require additional libraries and complexity.

**Decision**: Do not implement Arabic NLP. Index structured English fields only. Flag language. Extract attributes only where reliable rules apply.

**Consequences**: Arabic-only descriptions yield fewer extracted attributes. This is documented as a known limitation.

---

## ADR-006: Deterministic booking transactions and lead upsert rules
**Date**: 2026-11-10
**Status**: Approved

**Context**: Viewing bookings and customer lead qualification require strict business-rule enforcement (`Asia/Dubai` hours, lead time, explicit confirmation, concurrency safety, phone privacy, and one lead per customer) that must never be delegated to the LLM.

**Decision**:
- Enforce all date/time parsing, `Asia/Dubai` operating hours (Monday–Saturday, 08:00–20:00, 60-minute slots, latest start 19:00), >= 2h lead time, explicit confirmation rejection of vague phrases (`"okay"`, `"Saturday afternoon"`), and lead scoring (`hot` / `warm` / `cold`) strictly in Python (`BookingService`, `LeadService`).
- Separate `propose` (`status='proposed'`, non-binding) from `confirm` (`BEGIN IMMEDIATE` SQLite transaction checking vehicle overlap, user slot overlap, and user duplicate booking per vehicle, reinforced by partial unique indexes `idx_bookings_confirmed_slot` and `idx_bookings_user_confirmed_slot`).
- Maintain a single lead row per `user_id` via `ON CONFLICT(user_id) DO UPDATE`, export `data/exports/leads.csv` directly from SQLite under a file-export lock, validate phone numbers deterministically, and mask/redact phone numbers in logs and `agent_traces`.

**Consequences**: Concurrent confirmation requests serialize cleanly at SQLite without double-booking races, while `output_guardrail` blocks false confirmation claims unless `confirm_booking` succeeds in the same turn.

---

## ADR-007: Add `GET /inventory/summaries` for session restoration in Streamlit
**Date**: 2026-11-10
**Status**: Approved

**Context**: Phase 8 Streamlit UI restores persisted `SessionMessage.vehicle_cards`, `current_vehicle`, and booking vehicle titles strictly from FastAPI without SQLite access or frontend attribute-parsing logic. Previously, `GET /inventory/{listing_id}` returned raw `Listing` with attribute arrays while only `POST /chat` and `GET /users/{id}/favourites` returned `ListingSummary`.

**Decision**: Add a minimal read-only endpoint `GET /inventory/summaries?ids=...` in `backend/api/inventory.py` delegating directly to `InventorySearchService.get_vehicle_summaries(ids)`.

**Consequences**: All vehicle cards across chat, saved favourites, restored sessions, and bookings render identically from database-backed `ListingSummary` records with zero business logic in Streamlit.

---

## ADR-008: Hybrid Marketplace + Conversational Workspace, Modal Workflows & Tokenized Themes
**Date**: 2026-11-12
**Status**: Approved

**Context**: Post-Phase 8 UI auditing uncovered Streamlit 1.67 DOM mismatches (`.main .block-container`, `stVerticalBlockBorderWrapper`, `primaryFormSubmit`), dark-mode contrast failures on sidebar items and BaseWeb inputs/selectboxes/datepickers, nested 4-column card button clipping at `768px`–`1024px`, silent omission of confirmed warranty chips (`Yes (...)` vs `"yes"`), inline details/booking layout disruption, and missing frontend surfaces for side-by-side vehicle comparison and personal buyer enquiry capture.

**Decision**:
- Preserve strict HTTP isolation (`frontend/` communicates exclusively through `DubizzleAPIClient` with zero DB/LLM imports).
- Standardize CSS custom properties (`--dbz-*`) in `frontend/styles/theme.py` targeting `[data-testid="stMainBlockContainer"]`, descendant `.stButton button` selectors, explicit disabled tokens (`--dbz-disabled-bg` / `--dbz-disabled-text`), BaseWeb / react-aria datepicker, calendar, and select popovers, and `[role="dialog"]` containers (defaulting strictly to a clean Light theme workspace without a dark-mode sidebar toggle).
- Move Vehicle Details, Side-by-Side Comparison, Two-Step Dubai Booking, and Personal Buyer Enquiry capture into coordinated Streamlit `@st.dialog(width="large", on_dismiss=close_dialog)` workflows managed through a single `active_dialog` state coordinator so chat context and scroll position remain undisturbed, while keeping global lead listings and `leads.csv` export restricted behind `ADMIN_API_KEY` on the backend.
- Replace 4-across card buttons with responsive 2-row action groups (`2` + `3`) and collapse 2-card grids cleanly to 1 column at `<= 1120px` to prevent listing-card button clipping on desktop, tablet, and mobile viewports (note: the 4-column top workspace toolbar buttons can still ellipsize labels at narrower desktop/tablet widths while the sidebar is expanded).
- Add optional `mileage_km_status` to `ListingSummary` so cards surface confirmed vs. conflicting/inferred vs. missing attributes accurately alongside fixed warranty matching and smart-cased trims.

**Consequences**: All core buyer journeys (discovery, filtering, evidence-verified inspection, comparison, two-step booking, buyer enquiry capture with backend CSV sync, and profile memory) operate cohesively across desktop, tablet, and mobile without altering deterministic backend business rules or exposing admin lead exports in the UI.
