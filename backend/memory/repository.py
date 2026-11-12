"""SQLite repository layer for users, preferences, favourites, and sessions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from backend.agent.guardrails import sanitize_message_content
from backend.db import get_db
from backend.models.entities import (
    Booking,
    BookingStatus,
    Preference,
    PreferenceHistoryEntry,
    SearchHistoryEntry,
    Session,
    SessionMessage,
    User,
)


class MemoryRepository:
    """Pure SQLite persistence for user profiles, memory, favourites, and sessions."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.db_path = db_path

    # ── Users ──

    def get_user(self, user_id: str) -> Optional[User]:
        with get_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT user_id, name, created_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return User(
            user_id=row["user_id"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )

    def create_user(self, user_id: str, name: Optional[str] = None) -> User:
        with get_db(self.db_path) as conn:
            conn.execute(
                "INSERT INTO users (user_id, name) VALUES (?, ?)",
                (user_id, name),
            )
        user = self.get_user(user_id)
        assert user is not None
        return user

    def update_user_name(self, user_id: str, name: str) -> None:
        with get_db(self.db_path) as conn:
            conn.execute("UPDATE users SET name = ? WHERE user_id = ?", (name, user_id))

    def delete_user(self, user_id: str) -> bool:
        """Transactionally delete user, associated agent_traces, and all cascaded records."""
        with get_db(self.db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                DELETE FROM agent_traces
                WHERE user_id = ?
                   OR session_id IN (SELECT session_id FROM sessions WHERE user_id = ?)
                """,
                (user_id, user_id),
            )
            cur = conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            return cur.rowcount > 0

    def get_user_counts(self, user_id: str) -> tuple[int, int]:
        """Return (favourite_count, booking_count) for a user."""
        with get_db(self.db_path) as conn:
            fav_cnt = conn.execute(
                "SELECT COUNT(*) FROM favourites WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            book_cnt = conn.execute(
                "SELECT COUNT(*) FROM bookings WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
        return int(fav_cnt), int(book_cnt)

    def get_user_bookings_readonly(self, user_id: str) -> list[dict[str, Any]]:
        """Read-only booking history rows (bookings creation arrives in Phase 7)."""
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT booking_id, user_id, listing_id, booking_date, start_time, end_time,
                       duration_minutes, status, timezone, created_at, confirmed_at
                FROM bookings
                WHERE user_id = ?
                ORDER BY created_at DESC, booking_id DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Preferences & History ──

    def _row_to_preference(self, r: Any) -> Preference:
        return Preference(
            user_id=r["user_id"],
            preference_key=r["preference_key"],
            preference_value=r["preference_value"],
            original_wording=r["original_wording"],
            metadata=json.loads(r["metadata_json"]) if r["metadata_json"] else None,
            source_type=r["source_type"],
            is_explicit=bool(r["is_explicit"]),
            status=r["status"],
            confidence=float(r["confidence"]),
            source_turn=r["source_turn"],
            source_message_id=r["source_message_id"],
            created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else None,
            updated_at=datetime.fromisoformat(r["updated_at"]) if r["updated_at"] else None,
        )

    def get_preferences(self, user_id: str) -> list[Preference]:
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT user_id, preference_key, preference_value, original_wording,
                       metadata_json, source_type, is_explicit, status, confidence,
                       source_turn, source_message_id, created_at, updated_at
                FROM preferences
                WHERE user_id = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (user_id,),
            ).fetchall()
        return [self._row_to_preference(r) for r in rows]

    def get_preference(self, user_id: str, preference_key: str) -> Optional[Preference]:
        with get_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT user_id, preference_key, preference_value, original_wording,
                       metadata_json, source_type, is_explicit, status, confidence,
                       source_turn, source_message_id, created_at, updated_at
                FROM preferences
                WHERE user_id = ? AND preference_key = ?
                """,
                (user_id, preference_key),
            ).fetchone()
        return self._row_to_preference(row) if row is not None else None

    def upsert_preference(
        self,
        *,
        user_id: str,
        preference_key: str,
        preference_value: str,
        original_wording: Optional[str],
        metadata: Optional[dict[str, Any]],
        source_type: str,
        is_explicit: bool,
        status: str,
        confidence: float,
        source_turn: Optional[int],
        source_message_id: Optional[int],
    ) -> Preference:
        meta_json = json.dumps(metadata) if metadata is not None else None
        with get_db(self.db_path) as conn:
            existing = conn.execute(
                "SELECT preference_value FROM preferences WHERE user_id = ? AND preference_key = ?",
                (user_id, preference_key),
            ).fetchone()
            old_val = existing["preference_value"] if existing is not None else None

            conn.execute(
                """
                INSERT INTO preferences (
                    user_id, preference_key, preference_value, original_wording,
                    metadata_json, source_type, is_explicit, status, confidence,
                    source_turn, source_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, preference_key) DO UPDATE SET
                    preference_value  = excluded.preference_value,
                    original_wording  = excluded.original_wording,
                    metadata_json     = excluded.metadata_json,
                    source_type       = excluded.source_type,
                    is_explicit       = excluded.is_explicit,
                    status            = excluded.status,
                    confidence        = excluded.confidence,
                    source_turn       = excluded.source_turn,
                    source_message_id = excluded.source_message_id,
                    updated_at        = datetime('now')
                """,
                (
                    user_id,
                    preference_key,
                    preference_value,
                    original_wording,
                    meta_json,
                    source_type,
                    1 if is_explicit else 0,
                    status,
                    confidence,
                    source_turn,
                    source_message_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO preference_history (
                    user_id, preference_key, old_value, new_value,
                    original_wording, metadata_json, source_type, source_message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    preference_key,
                    old_val,
                    preference_value,
                    original_wording,
                    meta_json,
                    source_type,
                    source_message_id,
                ),
            )
        pref = self.get_preference(user_id, preference_key)
        assert pref is not None
        return pref

    def delete_preference(self, user_id: str, preference_key: str) -> bool:
        with get_db(self.db_path) as conn:
            existing = conn.execute(
                "SELECT preference_value FROM preferences WHERE user_id = ? AND preference_key = ?",
                (user_id, preference_key),
            ).fetchone()
            if existing is None:
                return False
            conn.execute(
                """
                INSERT INTO preference_history (
                    user_id, preference_key, old_value, new_value,
                    original_wording, source_type
                ) VALUES (?, ?, ?, '[DELETED]', 'User deleted preference', 'ui')
                """,
                (user_id, preference_key, existing["preference_value"]),
            )
            cur = conn.execute(
                "DELETE FROM preferences WHERE user_id = ? AND preference_key = ?",
                (user_id, preference_key),
            )
            return cur.rowcount > 0

    def get_preference_history(
        self, user_id: str, preference_key: Optional[str] = None
    ) -> list[PreferenceHistoryEntry]:
        with get_db(self.db_path) as conn:
            if preference_key:
                rows = conn.execute(
                    """
                    SELECT id, user_id, preference_key, old_value, new_value,
                           original_wording, metadata_json, source_type, source_message_id, changed_at
                    FROM preference_history
                    WHERE user_id = ? AND preference_key = ?
                    ORDER BY id DESC
                    """,
                    (user_id, preference_key),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, user_id, preference_key, old_value, new_value,
                           original_wording, metadata_json, source_type, source_message_id, changed_at
                    FROM preference_history
                    WHERE user_id = ?
                    ORDER BY id DESC
                    """,
                    (user_id,),
                ).fetchall()
        return [
            PreferenceHistoryEntry(
                id=r["id"],
                user_id=r["user_id"],
                preference_key=r["preference_key"],
                old_value=r["old_value"],
                new_value=r["new_value"],
                original_wording=r["original_wording"],
                metadata=json.loads(r["metadata_json"]) if r["metadata_json"] else None,
                source_type=r["source_type"],
                source_message_id=r["source_message_id"],
                changed_at=datetime.fromisoformat(r["changed_at"]) if r["changed_at"] else None,
            )
            for r in rows
        ]

    # ── Search History ──

    def record_search(
        self,
        *,
        user_id: str,
        query_text: Optional[str],
        filters: dict[str, Any],
        result_count: int,
        listing_ids: list[int],
    ) -> int:
        with get_db(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO searches (user_id, query_text, filters_json, result_count, listing_ids_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    query_text,
                    json.dumps(filters),
                    result_count,
                    json.dumps(listing_ids),
                ),
            )
            return int(cur.lastrowid or 0)

    def get_search_history(self, user_id: str, limit: int = 15) -> list[SearchHistoryEntry]:
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, query_text, filters_json, result_count, listing_ids_json, created_at
                FROM searches
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [
            SearchHistoryEntry(
                id=r["id"],
                user_id=r["user_id"],
                query_text=r["query_text"],
                filters=json.loads(r["filters_json"]) if r["filters_json"] else {},
                result_count=int(r["result_count"]),
                listing_ids=json.loads(r["listing_ids_json"]) if r["listing_ids_json"] else [],
                created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else None,
            )
            for r in rows
        ]

    def get_last_user_messages(
        self, session_id: str, limit: int = 6
    ) -> list[SessionMessage]:
        """Fetch recent user-authored messages in a session for provenance verification."""
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, role, content, vehicle_cards, created_at
                FROM messages
                WHERE session_id = ? AND role = 'user'
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [
            SessionMessage(
                id=r["id"],
                session_id=r["session_id"],
                role=r["role"],
                content=r["content"],
                vehicle_cards=json.loads(r["vehicle_cards"]) if r["vehicle_cards"] else [],
                created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else None,
            )
            for r in rows
        ]

    # ── Favourites ──

    def add_favourite(self, user_id: str, listing_id: int) -> None:
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO favourites (user_id, listing_id)
                VALUES (?, ?)
                ON CONFLICT(user_id, listing_id) DO NOTHING
                """,
                (user_id, listing_id),
            )

    def remove_favourite(self, user_id: str, listing_id: int) -> bool:
        with get_db(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM favourites WHERE user_id = ? AND listing_id = ?",
                (user_id, listing_id),
            )
            return cur.rowcount > 0

    def list_favourite_ids(self, user_id: str) -> list[int]:
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT listing_id FROM favourites WHERE user_id = ? ORDER BY created_at DESC, id DESC",
                (user_id,),
            ).fetchall()
        return [int(r["listing_id"]) for r in rows]

    # ── Sessions ──

    def _row_to_session(self, row: Any) -> Session:
        return Session(
            session_id=row["session_id"],
            user_id=row["user_id"],
            last_result_ids=json.loads(row["last_result_ids"]) if row["last_result_ids"] else [],
            current_vehicle=row["current_vehicle"],
            active_filters=json.loads(row["active_filters"]) if row["active_filters"] else None,
            booking_draft=json.loads(row["booking_draft"]) if row["booking_draft"] else None,
            context_summary=row["context_summary"],
        )

    def get_session(
        self, session_id: str, *, include_messages: bool = True, message_limit: int = 20
    ) -> Optional[Session]:
        with get_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT session_id, user_id, last_result_ids, current_vehicle,
                       active_filters, booking_draft, context_summary
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        session = self._row_to_session(row)
        if include_messages:
            session.recent_messages = self.get_recent_messages(session_id, limit=message_limit)
        return session

    def create_session(self, session_id: str, user_id: str) -> Session:
        with get_db(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, user_id, last_result_ids) VALUES (?, ?, '[]')",
                (session_id, user_id),
            )
        session = self.get_session(session_id)
        assert session is not None
        return session

    def set_session_focus(self, session_id: str, listing_id: Optional[int]) -> None:
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE sessions
                SET current_vehicle = ?, updated_at = datetime('now')
                WHERE session_id = ?
                """,
                (listing_id, session_id),
            )

    def update_session_search(
        self,
        session_id: str,
        *,
        result_ids: list[int],
        active_filters: Optional[dict[str, Any]],
        clear_focus: bool = True,
    ) -> None:
        with get_db(self.db_path) as conn:
            if clear_focus:
                conn.execute(
                    """
                    UPDATE sessions
                    SET last_result_ids = ?,
                        active_filters = ?,
                        current_vehicle = NULL,
                        updated_at = datetime('now')
                    WHERE session_id = ?
                    """,
                    (
                        json.dumps(result_ids),
                        json.dumps(active_filters) if active_filters is not None else None,
                        session_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE sessions
                    SET last_result_ids = ?,
                        active_filters = ?,
                        updated_at = datetime('now')
                    WHERE session_id = ?
                    """,
                    (
                        json.dumps(result_ids),
                        json.dumps(active_filters) if active_filters is not None else None,
                        session_id,
                    ),
                )

    def update_session_booking_draft(
        self, session_id: str, booking_draft: Optional[dict[str, Any]]
    ) -> None:
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE sessions
                SET booking_draft = ?, updated_at = datetime('now')
                WHERE session_id = ?
                """,
                (
                    json.dumps(booking_draft) if booking_draft is not None else None,
                    session_id,
                ),
            )

    def update_session_context_summary(
        self, session_id: str, context_summary: Optional[str]
    ) -> None:
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                UPDATE sessions
                SET context_summary = ?, updated_at = datetime('now')
                WHERE session_id = ?
                """,
                (context_summary, session_id),
            )

    def list_user_focused_listing_ids(self, user_id: str, limit: int = 10) -> list[int]:
        """Return distinct non-null current_vehicle listing IDs across the user's recent sessions."""
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT current_vehicle
                FROM sessions
                WHERE user_id = ? AND current_vehicle IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        seen: list[int] = []
        for r in rows:
            lid = int(r["current_vehicle"])
            if lid > 0 and lid not in seen:
                seen.append(lid)
        return seen

    # ── Messages ──

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        vehicle_cards: Optional[list[int]] = None,
    ) -> SessionMessage:
        safe_content = sanitize_message_content(content)
        cards_json = json.dumps(vehicle_cards) if vehicle_cards is not None else None
        with get_db(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO messages (session_id, role, content, vehicle_cards)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, role, safe_content, cards_json),
            )
            conn.execute(
                "UPDATE sessions SET updated_at = datetime('now') WHERE session_id = ?",
                (session_id,),
            )
            msg_id = cur.lastrowid
            row = conn.execute(
                "SELECT id, session_id, role, content, vehicle_cards, created_at FROM messages WHERE id = ?",
                (msg_id,),
            ).fetchone()
        assert row is not None
        return SessionMessage(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            vehicle_cards=json.loads(row["vehicle_cards"]) if row["vehicle_cards"] else [],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )

    def get_recent_messages(self, session_id: str, limit: int = 20) -> list[SessionMessage]:
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, role, content, vehicle_cards, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        # Return in chronological order (oldest -> newest)
        ordered = list(reversed(rows))
        return [
            SessionMessage(
                id=r["id"],
                session_id=r["session_id"],
                role=r["role"],
                content=r["content"],
                vehicle_cards=json.loads(r["vehicle_cards"]) if r["vehicle_cards"] else [],
                created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else None,
            )
            for r in ordered
        ]

    # ── Agent Traces ──

    def save_agent_trace(
        self,
        *,
        session_id: str,
        user_id: str,
        user_message: str,
        tool_names: list[str],
        tool_calls_json: str,
        execution_ms: int,
        llm_error: Optional[str],
        assistant_response: str,
    ) -> int:
        with get_db(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO agent_traces (
                    session_id, user_id, user_message, tool_names,
                    tool_calls_json, execution_ms, llm_error, assistant_response
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    user_id,
                    user_message,
                    json.dumps(tool_names),
                    tool_calls_json,
                    execution_ms,
                    llm_error,
                    assistant_response,
                ),
            )
            return int(cur.lastrowid or 0)

    def get_session_traces(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, session_id, user_id, user_message, tool_names,
                       tool_calls_json, execution_ms, llm_error, assistant_response, created_at
                FROM agent_traces
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ── Consolidated Single-Connection Readers ──

    def _load_session_messages(
        self, conn: Any, session_id: str, limit: int
    ) -> list[SessionMessage]:
        rows = conn.execute(
            """
            SELECT id, session_id, role, content, vehicle_cards, created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        return [
            SessionMessage(
                id=r["id"],
                session_id=r["session_id"],
                role=r["role"],
                content=r["content"],
                vehicle_cards=json.loads(r["vehicle_cards"]) if r["vehicle_cards"] else [],
                created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else None,
            )
            for r in reversed(rows)
        ]

    def _load_user_bundle(
        self,
        conn: Any,
        user_id: str,
        *,
        search_limit: int,
        include_history: bool,
        include_full_bookings: bool,
    ) -> dict[str, Any]:
        pref_rows = conn.execute(
            """
            SELECT user_id, preference_key, preference_value, original_wording,
                   metadata_json, source_type, is_explicit, status, confidence,
                   source_turn, source_message_id, created_at, updated_at
            FROM preferences
            WHERE user_id = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
        prefs = [self._row_to_preference(r) for r in pref_rows]

        pref_history: list[PreferenceHistoryEntry] = []
        if include_history:
            hist_rows = conn.execute(
                """
                SELECT id, user_id, preference_key, old_value, new_value,
                       original_wording, metadata_json, source_type, source_message_id, changed_at
                FROM preference_history
                WHERE user_id = ?
                ORDER BY id DESC
                """,
                (user_id,),
            ).fetchall()
            pref_history = [
                PreferenceHistoryEntry(
                    id=r["id"],
                    user_id=r["user_id"],
                    preference_key=r["preference_key"],
                    old_value=r["old_value"],
                    new_value=r["new_value"],
                    original_wording=r["original_wording"],
                    metadata=json.loads(r["metadata_json"]) if r["metadata_json"] else None,
                    source_type=r["source_type"],
                    source_message_id=r["source_message_id"],
                    changed_at=datetime.fromisoformat(r["changed_at"]) if r["changed_at"] else None,
                )
                for r in hist_rows
            ]

        search_rows = conn.execute(
            """
            SELECT id, user_id, query_text, filters_json, result_count, listing_ids_json, created_at
            FROM searches
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, search_limit),
        ).fetchall()
        searches = [
            SearchHistoryEntry(
                id=r["id"],
                user_id=r["user_id"],
                query_text=r["query_text"],
                filters=json.loads(r["filters_json"]) if r["filters_json"] else {},
                result_count=int(r["result_count"]),
                listing_ids=json.loads(r["listing_ids_json"]) if r["listing_ids_json"] else [],
                created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else None,
            )
            for r in search_rows
        ]

        fav_rows = conn.execute(
            "SELECT listing_id FROM favourites WHERE user_id = ? ORDER BY created_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        fav_ids = [int(r["listing_id"]) for r in fav_rows]

        if include_full_bookings:
            bk_rows = conn.execute(
                """
                SELECT booking_id, user_id, session_id, listing_id, booking_date, start_time,
                       end_time, duration_minutes, status, timezone, customer_name,
                       customer_phone, created_at, confirmed_at
                FROM bookings
                WHERE user_id = ?
                ORDER BY created_at DESC, booking_id DESC
                """,
                (user_id,),
            ).fetchall()
            bookings: list[Any] = [
                Booking(
                    booking_id=int(r["booking_id"]),
                    user_id=r["user_id"],
                    session_id=r["session_id"],
                    listing_id=int(r["listing_id"]),
                    booking_date=r["booking_date"],
                    start_time=r["start_time"],
                    end_time=r["end_time"],
                    duration_minutes=int(r["duration_minutes"]),
                    status=BookingStatus(r["status"]),
                    timezone=r["timezone"],
                    customer_name=r["customer_name"],
                    customer_phone=r["customer_phone"],
                    created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else None,
                    confirmed_at=datetime.fromisoformat(r["confirmed_at"]) if r["confirmed_at"] else None,
                )
                for r in bk_rows
            ]
        else:
            bk_rows = conn.execute(
                """
                SELECT booking_id, user_id, listing_id, booking_date, start_time, end_time,
                       duration_minutes, status, timezone, created_at, confirmed_at
                FROM bookings
                WHERE user_id = ?
                ORDER BY created_at DESC, booking_id DESC
                """,
                (user_id,),
            ).fetchall()
            bookings = [dict(r) for r in bk_rows]

        return {
            "preferences": prefs,
            "preference_history": pref_history,
            "searches": searches,
            "fav_ids": fav_ids,
            "bookings": bookings,
        }

    def get_turn_bootstrap(
        self,
        *,
        user_id: str,
        session_id: str,
        recent_limit: int = 24,
    ) -> dict[str, Any]:
        """Load session, messages, user profile, and memory state in one DB connection."""
        with get_db(self.db_path) as conn:
            sess_row = conn.execute(
                """
                SELECT session_id, user_id, last_result_ids, current_vehicle,
                       active_filters, booking_draft, context_summary
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if sess_row is None:
                return {"session": None}
            session = self._row_to_session(sess_row)
            if session.user_id != user_id:
                return {"session": session}

            session.recent_messages = self._load_session_messages(conn, session_id, recent_limit)
            total_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            total_messages = int(total_row["cnt"]) if total_row is not None else len(session.recent_messages)

            user_row = conn.execute(
                "SELECT user_id, name, created_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            user = (
                User(
                    user_id=user_row["user_id"],
                    name=user_row["name"],
                    created_at=datetime.fromisoformat(user_row["created_at"]) if user_row["created_at"] else None,
                )
                if user_row is not None
                else None
            )
            bundle = self._load_user_bundle(
                conn,
                user_id,
                search_limit=5,
                include_history=False,
                include_full_bookings=False,
            )
        return {
            "session": session,
            "user": user,
            "total_messages": total_messages,
            **bundle,
        }

    def get_user_snapshot_raw(
        self,
        *,
        user_id: str,
        session_id: Optional[str],
        name: Optional[str],
        new_session_id: str,
        search_limit: int = 10,
    ) -> dict[str, Any]:
        """Ensure user/session and fetch full UI snapshot state in a single DB connection."""
        clean_name = name.strip() if name and name.strip() else None
        with get_db(self.db_path) as conn:
            user_row = conn.execute(
                "SELECT user_id, name, created_at FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if user_row is None:
                conn.execute(
                    "INSERT INTO users (user_id, name) VALUES (?, ?)",
                    (user_id, clean_name),
                )
                user_row = conn.execute(
                    "SELECT user_id, name, created_at FROM users WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            elif clean_name and not user_row["name"]:
                conn.execute(
                    "UPDATE users SET name = ? WHERE user_id = ?",
                    (clean_name, user_id),
                )
                user_row = conn.execute(
                    "SELECT user_id, name, created_at FROM users WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
            assert user_row is not None
            user = User(
                user_id=user_row["user_id"],
                name=user_row["name"],
                created_at=datetime.fromisoformat(user_row["created_at"]) if user_row["created_at"] else None,
            )

            sess_row = None
            if session_id and session_id.strip():
                sess_row = conn.execute(
                    """
                    SELECT session_id, user_id, last_result_ids, current_vehicle,
                           active_filters, booking_draft, context_summary
                    FROM sessions
                    WHERE session_id = ?
                    """,
                    (session_id.strip(),),
                ).fetchone()
                if sess_row is not None and sess_row["user_id"] != user_id:
                    sess_row = None

            if sess_row is None:
                conn.execute(
                    "INSERT INTO sessions (session_id, user_id, last_result_ids) VALUES (?, ?, '[]')",
                    (new_session_id, user_id),
                )
                sess_row = conn.execute(
                    """
                    SELECT session_id, user_id, last_result_ids, current_vehicle,
                           active_filters, booking_draft, context_summary
                    FROM sessions
                    WHERE session_id = ?
                    """,
                    (new_session_id,),
                ).fetchone()
            assert sess_row is not None
            session = self._row_to_session(sess_row)
            session.recent_messages = self._load_session_messages(conn, session.session_id, 30)

            bundle = self._load_user_bundle(
                conn,
                user_id,
                search_limit=search_limit,
                include_history=True,
                include_full_bookings=True,
            )
        return {
            "user": user,
            "session": session,
            **bundle,
        }

    def delete_message(self, message_id: int) -> None:
        """Delete a single message row (used to clean up an orphaned user turn on unexpected failure)."""
        with get_db(self.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))

    def get_chat_request_response(
        self,
        request_id: str,
        *,
        session_id: str,
        user_id: str,
    ) -> Optional[dict[str, Any]]:
        """Return previously completed ChatResponse JSON for an idempotent request_id."""
        with get_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT response_json
                FROM chat_requests
                WHERE request_id = ? AND session_id = ? AND user_id = ?
                """,
                (request_id, session_id, user_id),
            ).fetchone()
        if row is None:
            return None
        return dict(json.loads(row["response_json"]))

    def save_chat_request_response(
        self,
        request_id: str,
        *,
        session_id: str,
        user_id: str,
        response_dict: dict[str, Any],
    ) -> None:
        """Persist completed ChatResponse JSON for idempotent retries."""
        payload = json.dumps(response_dict, default=str)
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO chat_requests (request_id, session_id, user_id, response_json)
                VALUES (?, ?, ?, ?)
                """,
                (request_id, session_id, user_id, payload),
            )
