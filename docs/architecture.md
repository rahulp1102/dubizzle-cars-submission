# Architecture — dubizzle AI

## System Architecture

```
Streamlit Frontend
        │
        │ HTTP / JSON
        ▼
FastAPI Routes (backend/api/)
        │
        │ Pydantic-validated calls
        ▼
Service Layer (backend/services/, backend/inventory/, backend/memory/)
        │
        ├── Inventory Service — search, details, extraction
        ├── Agent Service — LLM orchestration, tool loop
        ├── Booking Service — deterministic validation
        ├── Memory Service — preferences, favourites
        ├── Session Service — state persistence
        ├── User Service — profiles, deletion
        └── Lead Service — qualification, export
        │
        ▼
Repository Layer (backend/inventory/repository.py, backend/memory/repository.py, backend/services/booking_repository.py)
        │
        ▼
SQLite + FTS5 (data/database/dubizzle.db)
```

## Layers

### Routes (`backend/api/`)
- HTTP request handling
- Pydantic validation
- Call services, format responses
- Never contain business logic

### Services (`backend/services/`, `backend/inventory/`, `backend/memory/`)
- All business logic lives here
- Inventory search, booking validation, memory operations
- Agent tool implementations wrap service functions
- Deterministic reference resolution

### Repositories
- Raw SQLite queries and writes
- Transactions
- FTS5 operations
- No business logic

### Agent (`backend/agent/`)
- Intent interpretation via Gemini
- Tool selection and argument generation
- Bounded tool-calling loop (max 5 iterations)
- Natural-language response generation
- Never executes SQL or implements business rules

## Key Principles

1. **LLM is a translator, not an authority.** Python and the database are the source of truth.
2. **Same business logic for UI and agent.** Both call the same service functions.
3. **Graceful degradation.** If Gemini is unavailable, search/cards/favourites/bookings still work.
4. **Session state in SQLite.** Server restarts preserve conversation context.
5. **Prototype identity vs. production authentication.**
   - **Current prototype scoping:** `user_id` serves as a tenant/profile identifier (auto-generated as `buyer_<16-hex>` for guests or switched in the demo UI) rather than cryptographic user authentication. Backend endpoints enforce strict resource-level ownership (`session_id`, `booking_id`) across users so one `user_id` cannot access or mutate another user's sessions, bookings, or preference provenance, while administrative global lead/CSV routes require `ADMIN_API_KEY`.
   - **Production requirement:** A unique `user_id` is **not** authentication. External production deployment requires server-verified authentication (signed HTTP-only session cookies, JWT, or OAuth2) where the backend derives the acting `user_id` from the verified credential instead of accepting self-asserted `user_id` values in URL paths, query parameters, or request bodies.

## Data Flow: User Query

1. User types "Show me Mercedes SUVs under 200k"
2. Streamlit sends POST /chat
3. FastAPI passes to Agent Service
4. Agent (Gemini) interprets → calls `search_inventory(make="mercedes-benz", body_type="suv", max_price=200000)`
5. Tool validates args (Pydantic), calls Inventory Service
6. Inventory Service queries Repository → SQLite
7. Results returned through tool → Agent generates natural response
8. FastAPI returns response + vehicle card data to Streamlit
9. Streamlit renders cards from structured DB data (not LLM text)
