"""SQLite repository layer for vehicle viewing bookings and customer leads."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from backend.db import get_connection, get_db
from backend.models.entities import Booking, BookingStatus, Lead, LeadStatus


class BookingLeadRepository:
    """Pure SQLite persistence for viewing bookings and lead records."""

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.db_path = db_path

    # ── Bookings ──

    def _row_to_booking(self, row: sqlite3.Row) -> Booking:
        return Booking(
            booking_id=int(row["booking_id"]),
            user_id=row["user_id"],
            session_id=row["session_id"],
            listing_id=int(row["listing_id"]),
            booking_date=row["booking_date"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            duration_minutes=int(row["duration_minutes"]),
            status=BookingStatus(row["status"]),
            timezone=row["timezone"],
            customer_name=row["customer_name"],
            customer_phone=row["customer_phone"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            confirmed_at=(
                datetime.fromisoformat(row["confirmed_at"]) if row["confirmed_at"] else None
            ),
        )

    def create_proposed_booking(
        self,
        *,
        user_id: str,
        listing_id: int,
        booking_date: str,
        start_time: str,
        end_time: str,
        duration_minutes: int,
        timezone: str,
        session_id: Optional[str] = None,
        customer_name: Optional[str] = None,
        customer_phone: Optional[str] = None,
    ) -> Booking:
        with get_db(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO bookings (
                    user_id, session_id, listing_id, booking_date, start_time, end_time,
                    duration_minutes, status, timezone, customer_name, customer_phone
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    listing_id,
                    booking_date,
                    start_time,
                    end_time,
                    duration_minutes,
                    timezone,
                    customer_name,
                    customer_phone,
                ),
            )
            booking_id = int(cur.lastrowid or 0)
            row = conn.execute(
                "SELECT * FROM bookings WHERE booking_id = ?",
                (booking_id,),
            ).fetchone()
        assert row is not None
        return self._row_to_booking(row)

    def get_booking_by_id(self, booking_id: int) -> Optional[Booking]:
        with get_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM bookings WHERE booking_id = ?",
                (booking_id,),
            ).fetchone()
        return self._row_to_booking(row) if row is not None else None

    def list_user_bookings(
        self, user_id: str, *, status: Optional[str] = None
    ) -> list[Booking]:
        with get_db(self.db_path) as conn:
            if status is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM bookings
                    WHERE user_id = ? AND status = ?
                    ORDER BY booking_date ASC, start_time ASC, booking_id DESC
                    """,
                    (user_id, status),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM bookings
                    WHERE user_id = ?
                    ORDER BY created_at DESC, booking_id DESC
                    """,
                    (user_id,),
                ).fetchall()
        return [self._row_to_booking(r) for r in rows]

    def has_vehicle_confirmed_overlap(
        self,
        listing_id: int,
        booking_date: str,
        start_time: str,
        end_time: str,
        *,
        exclude_booking_id: Optional[int] = None,
    ) -> bool:
        with get_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM bookings
                WHERE listing_id = ?
                  AND booking_date = ?
                  AND status = 'confirmed'
                  AND (? IS NULL OR booking_id != ?)
                  AND start_time < ?
                  AND end_time > ?
                LIMIT 1
                """,
                (
                    listing_id,
                    booking_date,
                    exclude_booking_id,
                    exclude_booking_id,
                    end_time,
                    start_time,
                ),
            ).fetchone()
        return row is not None

    def has_user_confirmed_overlap(
        self,
        user_id: str,
        booking_date: str,
        start_time: str,
        end_time: str,
        *,
        exclude_booking_id: Optional[int] = None,
    ) -> bool:
        with get_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM bookings
                WHERE user_id = ?
                  AND booking_date = ?
                  AND status = 'confirmed'
                  AND (? IS NULL OR booking_id != ?)
                  AND start_time < ?
                  AND end_time > ?
                LIMIT 1
                """,
                (
                    user_id,
                    booking_date,
                    exclude_booking_id,
                    exclude_booking_id,
                    end_time,
                    start_time,
                ),
            ).fetchone()
        return row is not None

    def has_user_confirmed_for_listing(
        self,
        user_id: str,
        listing_id: int,
        *,
        exclude_booking_id: Optional[int] = None,
    ) -> bool:
        with get_db(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM bookings
                WHERE user_id = ?
                  AND listing_id = ?
                  AND status = 'confirmed'
                  AND (? IS NULL OR booking_id != ?)
                LIMIT 1
                """,
                (user_id, listing_id, exclude_booking_id, exclude_booking_id),
            ).fetchone()
        return row is not None

    def list_confirmed_intervals_for_date(
        self, listing_id: int, user_id: str, booking_date: str
    ) -> list[tuple[str, str]]:
        """Return confirmed (start_time, end_time) intervals blocking either vehicle or user."""
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT start_time, end_time
                FROM bookings
                WHERE booking_date = ?
                  AND status = 'confirmed'
                  AND (listing_id = ? OR user_id = ?)
                ORDER BY start_time ASC
                """,
                (booking_date, listing_id, user_id),
            ).fetchall()
        return [(r["start_time"], r["end_time"]) for r in rows]

    def confirm_booking_transaction(
        self,
        *,
        booking_id: int,
        user_id: str,
        customer_name: Optional[str] = None,
        customer_phone: Optional[str] = None,
    ) -> tuple[str, Optional[Booking]]:
        """Atomically verify availability and confirm a proposed booking inside BEGIN IMMEDIATE."""
        conn = get_connection(self.db_path)
        conn.isolation_level = None
        in_tx = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            in_tx = True

            row = conn.execute(
                "SELECT * FROM bookings WHERE booking_id = ?",
                (booking_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                in_tx = False
                return "not_found", None

            current = self._row_to_booking(row)
            if current.user_id != user_id:
                conn.execute("ROLLBACK")
                in_tx = False
                return "forbidden", current

            if current.status == BookingStatus.CONFIRMED:
                conn.execute("ROLLBACK")
                in_tx = False
                return "already_confirmed", current

            if current.status != BookingStatus.PROPOSED:
                conn.execute("ROLLBACK")
                in_tx = False
                return "invalid_status", current

            veh_conflict = conn.execute(
                """
                SELECT 1 FROM bookings
                WHERE listing_id = ?
                  AND booking_date = ?
                  AND status = 'confirmed'
                  AND booking_id != ?
                  AND start_time < ?
                  AND end_time > ?
                LIMIT 1
                """,
                (
                    current.listing_id,
                    current.booking_date,
                    booking_id,
                    current.end_time,
                    current.start_time,
                ),
            ).fetchone()
            if veh_conflict is not None:
                conn.execute("ROLLBACK")
                in_tx = False
                return "vehicle_conflict", current

            user_slot_conflict = conn.execute(
                """
                SELECT 1 FROM bookings
                WHERE user_id = ?
                  AND booking_date = ?
                  AND status = 'confirmed'
                  AND booking_id != ?
                  AND start_time < ?
                  AND end_time > ?
                LIMIT 1
                """,
                (
                    user_id,
                    current.booking_date,
                    booking_id,
                    current.end_time,
                    current.start_time,
                ),
            ).fetchone()
            if user_slot_conflict is not None:
                conn.execute("ROLLBACK")
                in_tx = False
                return "user_slot_conflict", current

            user_veh_dup = conn.execute(
                """
                SELECT 1 FROM bookings
                WHERE user_id = ?
                  AND listing_id = ?
                  AND status = 'confirmed'
                  AND booking_id != ?
                LIMIT 1
                """,
                (user_id, current.listing_id, booking_id),
            ).fetchone()
            if user_veh_dup is not None:
                conn.execute("ROLLBACK")
                in_tx = False
                return "user_vehicle_duplicate", current

            cur = conn.execute(
                """
                UPDATE bookings
                SET status = 'confirmed',
                    confirmed_at = COALESCE(confirmed_at, datetime('now')),
                    customer_name = COALESCE(?, customer_name),
                    customer_phone = COALESCE(?, customer_phone)
                WHERE booking_id = ? AND status = 'proposed'
                """,
                (customer_name, customer_phone, booking_id),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                in_tx = False
                return "invalid_status", current

            updated_row = conn.execute(
                "SELECT * FROM bookings WHERE booking_id = ?",
                (booking_id,),
            ).fetchone()
            conn.execute("COMMIT")
            in_tx = False
            assert updated_row is not None
            return "confirmed", self._row_to_booking(updated_row)
        except sqlite3.IntegrityError:
            if in_tx:
                conn.execute("ROLLBACK")
                in_tx = False
            latest = self.get_booking_by_id(booking_id)
            return "vehicle_conflict", latest
        except Exception:
            if in_tx:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # ── Leads ──

    @staticmethod
    def _parse_json_list(raw: Any) -> Optional[list[Any]]:
        if raw is None:
            return None
        val = json.loads(raw)
        return list(val) if val is not None else None

    def _row_to_lead(self, row: sqlite3.Row) -> Lead:
        return Lead(
            lead_id=int(row["lead_id"]),
            user_id=row["user_id"],
            name=row["name"],
            phone=row["phone"],
            budget=float(row["budget"]) if row["budget"] is not None else None,
            currency=row["currency"] or "AED",
            original_budget=(
                float(row["original_budget"]) if row["original_budget"] is not None else None
            ),
            original_currency=row["original_currency"] or (row["currency"] or "AED"),
            preferred_makes=self._parse_json_list(row["preferred_makes"]),
            preferred_models=self._parse_json_list(row["preferred_models"]),
            preferred_body_type=row["preferred_body_type"],
            must_have_features=self._parse_json_list(row["must_have_features"]),
            purchase_timeline=row["purchase_timeline"],
            financing_interest=row["financing_interest"],
            listing_ids=(
                [int(x) for x in json.loads(row["listing_ids"])]
                if row["listing_ids"] is not None
                else None
            ),
            booking_id=int(row["booking_id"]) if row["booking_id"] is not None else None,
            status=LeadStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
        )

    def get_lead_by_user(self, user_id: str) -> Optional[Lead]:
        with get_db(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM leads WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return self._row_to_lead(row) if row is not None else None

    def list_leads(self) -> list[Lead]:
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM leads ORDER BY updated_at DESC, lead_id DESC"
            ).fetchall()
        return [self._row_to_lead(r) for r in rows]

    def upsert_lead(
        self,
        *,
        user_id: str,
        name: Optional[str],
        phone: Optional[str],
        budget: Optional[float],
        currency: str,
        original_budget: Optional[float],
        original_currency: str,
        preferred_makes: Optional[list[str]],
        preferred_models: Optional[list[str]],
        preferred_body_type: Optional[str],
        must_have_features: Optional[list[str]],
        purchase_timeline: Optional[str],
        financing_interest: Optional[str],
        listing_ids: Optional[list[int]],
        booking_id: Optional[int],
        status: LeadStatus,
    ) -> Lead:
        makes_json = json.dumps(preferred_makes) if preferred_makes is not None else None
        models_json = json.dumps(preferred_models) if preferred_models is not None else None
        features_json = json.dumps(must_have_features) if must_have_features is not None else None
        listings_json = json.dumps(listing_ids) if listing_ids is not None else None

        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO leads (
                    user_id, name, phone, budget, currency, original_budget, original_currency,
                    preferred_makes, preferred_models, preferred_body_type, must_have_features,
                    purchase_timeline, financing_interest, listing_ids, booking_id, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    name = excluded.name,
                    phone = excluded.phone,
                    budget = excluded.budget,
                    currency = excluded.currency,
                    original_budget = excluded.original_budget,
                    original_currency = excluded.original_currency,
                    preferred_makes = excluded.preferred_makes,
                    preferred_models = excluded.preferred_models,
                    preferred_body_type = excluded.preferred_body_type,
                    must_have_features = excluded.must_have_features,
                    purchase_timeline = excluded.purchase_timeline,
                    financing_interest = excluded.financing_interest,
                    listing_ids = excluded.listing_ids,
                    booking_id = excluded.booking_id,
                    status = excluded.status,
                    updated_at = datetime('now')
                """,
                (
                    user_id,
                    name,
                    phone,
                    budget,
                    currency,
                    original_budget,
                    original_currency,
                    makes_json,
                    models_json,
                    preferred_body_type,
                    features_json,
                    purchase_timeline,
                    financing_interest,
                    listings_json,
                    booking_id,
                    status.value,
                ),
            )
            row = conn.execute(
                "SELECT * FROM leads WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        assert row is not None
        return self._row_to_lead(row)
