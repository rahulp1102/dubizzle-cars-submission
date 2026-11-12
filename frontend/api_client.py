"""HTTP-only FastAPI client for the dubizzle AI Streamlit frontend."""

from __future__ import annotations

from typing import Any, Optional

import httpx

from frontend.config import (
    CHAT_TIMEOUT_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    get_api_base_url,
)


class DubizzleAPIError(Exception):
    """Typed error raised when FastAPI is unreachable, times out, or returns an error."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        is_connection_error: bool = False,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.error_code = error_code
        self.is_connection_error = is_connection_error


class DubizzleAPIClient:
    """Typed HTTP wrapper over FastAPI endpoints (never accesses SQLite or Gemini directly)."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        chat_timeout: float = CHAT_TIMEOUT_SECONDS,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.base_url = (base_url or get_api_base_url()).rstrip("/")
        self.timeout = timeout
        self.chat_timeout = chat_timeout
        self.transport = transport

    def _client(self, timeout_override: Optional[float] = None) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=timeout_override or self.timeout,
            transport=self.transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json_body: Optional[dict[str, Any]] = None,
        timeout_override: Optional[float] = None,
        raw_bytes: bool = False,
    ) -> Any:
        try:
            with self._client(timeout_override=timeout_override) as client:
                resp = client.request(method, path, params=params, json=json_body)
        except httpx.ConnectError as exc:
            raise DubizzleAPIError(
                f"Cannot reach backend API at {self.base_url}. Ensure the FastAPI server is running.",
                is_connection_error=True,
                error_code="connection_error",
            ) from exc
        except httpx.TimeoutException as exc:
            raise DubizzleAPIError(
                "The backend API timed out while processing your request. Please retry.",
                is_connection_error=True,
                error_code="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise DubizzleAPIError(
                "Unable to communicate with the assistant service right now. Please try again.",
                is_connection_error=True,
                error_code="http_error",
            ) from exc

        if resp.status_code >= 400:
            detail = f"Request failed ({resp.status_code})."
            error_code = None
            try:
                body = resp.json()
                if isinstance(body, dict):
                    detail = str(body.get("detail") or detail)
                    error_code = body.get("error_code")
            except ValueError:
                if resp.status_code == 429:
                    detail = "Too many requests right now. Please wait a moment and try again."
                    error_code = "rate_limited"
                elif resp.status_code >= 500:
                    detail = "The assistant service is temporarily unavailable. Please try again in a moment."
                    error_code = "server_error"
            raise DubizzleAPIError(
                detail,
                status_code=resp.status_code,
                error_code=error_code,
            )

        if raw_bytes:
            return bytes(resp.content)
        if not resp.content:
            return None
        return resp.json()

    # ── Health & Inventory ──

    def health(self) -> dict[str, Any]:
        return dict(self._request("GET", "/health"))

    def get_inventory_makes(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/inventory/makes") or [])

    def get_suggested_prompts(self) -> list[str]:
        return [str(p) for p in (self._request("GET", "/inventory/suggested-prompts") or [])]

    def search_inventory(self, **filters: Any) -> dict[str, Any]:
        clean = {k: v for k, v in filters.items() if v is not None and v != ""}
        return dict(self._request("GET", "/inventory", params=clean))

    def get_listing_summaries(self, ids: list[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        params = [("ids", int(lid)) for lid in ids]
        return list(self._request("GET", "/inventory/summaries", params=params) or [])

    def get_listing_detail(self, listing_id: int) -> dict[str, Any]:
        return dict(self._request("GET", f"/inventory/{listing_id}"))

    # ── Users, Preferences, Search History, Favourites ──

    def create_or_get_user(self, user_id: str, name: Optional[str] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"user_id": user_id}
        if name and name.strip():
            payload["name"] = name.strip()
        return dict(self._request("POST", "/users", json_body=payload))

    def get_user(self, user_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/users/{user_id}"))

    def delete_user(self, user_id: str, *, confirm: bool = True) -> dict[str, Any]:
        return dict(
            self._request(
                "DELETE",
                f"/users/{user_id}",
                params={"confirm": str(confirm).lower()},
            )
        )

    def get_preferences(self, user_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/users/{user_id}/preferences"))

    def upsert_preference(
        self,
        user_id: str,
        *,
        preference_key: str,
        preference_value: str,
        amount: Optional[float] = None,
        currency: Optional[str] = None,
        original_wording: Optional[str] = None,
        source_type: str = "ui",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "preference_key": preference_key,
            "preference_value": preference_value,
            "source_type": source_type,
        }
        if amount is not None:
            payload["amount"] = amount
        if currency:
            payload["currency"] = currency
        if original_wording:
            payload["original_wording"] = original_wording
        return dict(self._request("PUT", f"/users/{user_id}/preferences", json_body=payload))

    def delete_preference(self, user_id: str, preference_key: str) -> dict[str, Any]:
        return dict(self._request("DELETE", f"/users/{user_id}/preferences/{preference_key}"))

    def get_search_history(self, user_id: str, limit: int = 10) -> dict[str, Any]:
        return dict(self._request("GET", f"/users/{user_id}/searches", params={"limit": limit}))

    def get_returning_summary(
        self, user_id: str, session_id: Optional[str] = None
    ) -> dict[str, Any]:
        params = {"session_id": session_id} if session_id else None
        return dict(self._request("GET", f"/users/{user_id}/returning-summary", params=params))

    def get_user_snapshot(
        self,
        user_id: str,
        *,
        session_id: Optional[str] = None,
        name: Optional[str] = None,
        search_limit: int = 10,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"search_limit": search_limit}
        if session_id:
            params["session_id"] = session_id
        if name and name.strip():
            params["name"] = name.strip()
        return dict(self._request("GET", f"/users/{user_id}/snapshot", params=params))

    def list_favourites(self, user_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/users/{user_id}/favourites") or [])

    def add_favourite(self, user_id: str, listing_id: int) -> dict[str, Any]:
        return dict(
            self._request(
                "POST", f"/users/{user_id}/favourites", json_body={"listing_id": listing_id}
            )
        )

    def remove_favourite(self, user_id: str, listing_id: int) -> dict[str, Any]:
        return dict(self._request("DELETE", f"/users/{user_id}/favourites/{listing_id}"))

    # ── Sessions & Chat ──

    def create_session(self, user_id: str, session_id: Optional[str] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"user_id": user_id}
        if session_id:
            payload["session_id"] = session_id
        return dict(self._request("POST", "/sessions", json_body=payload))

    def get_session(self, session_id: str, user_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/sessions/{session_id}", params={"user_id": user_id}))

    def focus_vehicle(self, session_id: str, user_id: str, listing_id: int) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/sessions/{session_id}/focus",
                json_body={"user_id": user_id, "listing_id": listing_id},
            )
        )

    def search_in_session(
        self, session_id: str, user_id: str, filters: dict[str, Any], limit: int = 8
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/sessions/{session_id}/search",
                json_body={"user_id": user_id, "filters": filters, "limit": limit},
            )
        )

    def resolve_reference(
        self,
        session_id: str,
        user_id: str,
        *,
        reference_text: Optional[str] = None,
        ordinal: Optional[int] = None,
        explicit_listing_id: Optional[int] = None,
        update_focus: bool = True,
    ) -> dict[str, Any]:
        return dict(
            self._request(
                "POST",
                f"/sessions/{session_id}/resolve",
                json_body={
                    "user_id": user_id,
                    "reference_text": reference_text,
                    "ordinal": ordinal,
                    "explicit_listing_id": explicit_listing_id,
                    "update_focus": update_focus,
                },
            )
        )

    def chat(
        self,
        session_id: str,
        user_id: str,
        message: str,
        *,
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": session_id,
            "user_id": user_id,
            "message": message,
        }
        if request_id:
            payload["request_id"] = request_id
        return dict(
            self._request(
                "POST",
                "/chat",
                json_body=payload,
                timeout_override=self.chat_timeout,
            )
        )

    # ── Bookings & Leads ──

    def propose_booking(self, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(self._request("POST", "/bookings/propose", json_body=payload))

    def confirm_booking(self, booking_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(self._request("POST", f"/bookings/{booking_id}/confirm", json_body=payload))

    def get_booking(self, booking_id: int, user_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/bookings/{booking_id}", params={"user_id": user_id}))

    def list_user_bookings(self, user_id: str) -> list[dict[str, Any]]:
        return list(self._request("GET", f"/users/{user_id}/bookings") or [])

    def get_lead(self, user_id: str) -> dict[str, Any]:
        return dict(self._request("GET", f"/leads/{user_id}"))

    def upsert_lead(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(self._request("PUT", f"/leads/{user_id}", json_body=payload))
