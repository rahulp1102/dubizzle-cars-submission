"""Bounded tool-calling agent orchestrator for dubizzle AI."""

from __future__ import annotations

import inspect
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from backend.agent.grounding import (
    NO_TOOL_GROUNDING_REFUSAL,
    build_vehicle_cards,
    verify_grounding,
)
from backend.agent.guardrails import (
    check_input_guardrail,
    check_output_guardrail,
    redact_sensitive_text,
    sanitize_message_content,
)
from backend.agent.llm_client import (
    GeminiLLMClient,
    LLMClient,
    LLMError,
    LLMMessage,
)
from backend.agent.prompts import SYSTEM_PROMPT, build_session_context_note
from backend.agent.tools import build_tool_context, execute_tool, get_tool_schemas
from backend.config import settings
from backend.inventory.search import InventorySearchService
from backend.memory.repository import MemoryRepository
from backend.memory.service import MemoryService
from backend.models.entities import SessionMessage, User
from backend.models.schemas import ChatResponse
from backend.services.exceptions import ForbiddenError, NotFoundError
from backend.services.sessions import SessionService
from backend.services.users import UserService


def _degraded_reply_for_error(code: str) -> str:
    """Return a non-technical, user-friendly degraded-mode message per LLM failure category."""
    if code == "llm_quota_exceeded":
        return (
            "The AI assistant is temporarily busy due to high demand, so chat responses "
            "are running in degraded mode. Please try again shortly, or use the structured "
            "inventory filters, vehicle cards, and viewing bookings directly."
        )
    if code == "llm_timeout":
        return (
            "The AI assistant took too long to respond, so chat responses are temporarily "
            "running in degraded mode. You can retry your question or use the structured "
            "inventory filters, vehicle cards, and viewing bookings directly."
        )
    if code == "llm_malformed_response":
        return (
            "The AI assistant returned an incomplete response, so chat responses are temporarily "
            "running in degraded mode. Please rephrase your question or use the structured "
            "inventory filters, vehicle cards, and viewing bookings directly."
        )
    return (
        "The AI assistant is temporarily unavailable right now, so chat responses "
        "are running in degraded mode. You can still search inventory filters, "
        "inspect vehicle cards, and manage your saved favourites directly."
    )


class AgentOrchestrator:
    """Bounded orchestrator translating user messages into validated service calls."""

    def __init__(
        self,
        *,
        db_path: Optional[Path | str] = None,
        llm_client: Optional[LLMClient] = None,
        max_iterations: int = settings.AGENT_MAX_TOOL_CALLS,
        turn_timeout_seconds: float = settings.AGENT_TURN_TIMEOUT_SECONDS,
        now_fn: Optional[Callable[[], datetime]] = None,
        exports_dir: Optional[Path | str] = None,
    ) -> None:
        self.db_path = db_path
        self.llm_client: LLMClient = llm_client or GeminiLLMClient()
        self.max_iterations = max(1, max_iterations)
        self.turn_timeout_seconds = max(0.05, turn_timeout_seconds)
        self.now_fn = now_fn
        self.exports_dir = exports_dir
        self.inventory = InventorySearchService(db_path)
        self.sessions = SessionService(db_path)
        self.users = UserService(db_path, exports_dir=exports_dir)
        self.memory = MemoryService(db_path, exports_dir=exports_dir)
        self.memory_repo = MemoryRepository(db_path)

    def handle_turn(
        self,
        *,
        session_id: str,
        user_id: str,
        message: str,
        request_id: Optional[str] = None,
    ) -> ChatResponse:
        """Process one conversational turn with bounded tool execution and grounding."""
        start_ns = time.monotonic_ns()
        deadline_monotonic = time.monotonic() + self.turn_timeout_seconds

        # 1. Verify ownership & load bootstrap state in one DB connection
        boot = self.memory_repo.get_turn_bootstrap(
            user_id=user_id,
            session_id=session_id,
            recent_limit=24,
        )
        session = boot.get("session")
        if session is None:
            raise NotFoundError(f"Session '{session_id}' not found.", error_code="session_not_found")
        if session.user_id != user_id:
            raise ForbiddenError(
                f"Session '{session_id}' does not belong to user '{user_id}'.",
                error_code="session_forbidden",
            )

        # 1b. Idempotent replay for retried client request_id
        clean_request_id = request_id.strip() if request_id and request_id.strip() else None
        if clean_request_id:
            cached = self.memory_repo.get_chat_request_response(
                clean_request_id,
                session_id=session_id,
                user_id=user_id,
            )
            if cached is not None:
                return ChatResponse.model_validate(cached)

        # 2. Persist user message
        user_msg = self.sessions.add_message(session_id, user_id, "user", message)

        assistant_persisted = False
        try:
            # 3. Deterministic input guardrails (short-circuit before LLM setup)
            guardrail = check_input_guardrail(message)
            if guardrail is not None:
                self.sessions.add_message(session_id, user_id, "assistant", guardrail.reply)
                assistant_persisted = True
                elapsed_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)
                self._record_trace(
                    session_id=session_id,
                    user_id=user_id,
                    user_message=message,
                    tool_names=[],
                    tool_records=[],
                    execution_ms=elapsed_ms,
                    llm_error=f"guardrail:{guardrail.reason}",
                    assistant_response=guardrail.reply,
                )
                resp = ChatResponse(
                    reply=guardrail.reply,
                    vehicle_cards=[],
                    session_id=session_id,
                    degraded_mode=False,
                    tools_called=[],
                )
                if clean_request_id:
                    self.memory_repo.save_chat_request_response(
                        clean_request_id,
                        session_id=session_id,
                        user_id=user_id,
                        response_dict=resp.model_dump(mode="json"),
                    )
                return resp

            # 4. Assemble conversation messages & trusted context without redundant DB reads
            user_obj = boot.get("user") or User(user_id=user_id)
            prefs = boot.get("preferences", [])
            returning_summary = self.memory.build_returning_summary_from_loaded(
                user=user_obj,
                prefs=prefs,
                searches=boot.get("searches", []),
                fav_ids=boot.get("fav_ids", []),
                bookings=boot.get("bookings", []),
                session=session,
            )
            # Keep the current turn's actual message intact in memory while SQLite stores only sanitized text
            active_user_msg = user_msg.model_copy(update={"content": message})
            all_recent = list(session.recent_messages) + [active_user_msg]
            earlier_summary: Optional[str] = None
            if len(all_recent) > 16:
                earlier_summary = self._build_earlier_summary(all_recent[:-16])
                recent = all_recent[-16:]
            else:
                recent = all_recent

            llm_messages: list[LLMMessage] = [
                LLMMessage(
                    role="assistant" if m.role == "assistant" else "user",
                    text=m.content,
                )
                for m in recent
            ]
            full_system_prompt = (
                f"{SYSTEM_PROMPT}\n\n"
                f"{build_session_context_note(session, user_name=user_obj.name, returning_summary=returning_summary, earlier_context=earlier_summary)}"
            )
            tool_schemas = get_tool_schemas()
            ctx = build_tool_context(
                user_id=user_id,
                session_id=session_id,
                db_path=self.db_path,
                current_user_message=message,
                now_fn=self.now_fn,
                exports_dir=self.exports_dir,
            )

            tool_records: list[dict[str, Any]] = []
            seen_calls: set[tuple[str, str]] = set()
            raw_reply: Optional[str] = None
            llm_error: Optional[str] = None
            degraded_mode = False
            consecutive_blocked_steps = 0
            supports_deadline = (
                "deadline_monotonic" in inspect.signature(self.llm_client.generate).parameters
            )

            # 5. Bounded tool-calling loop with overall turn deadline
            for _step in range(self.max_iterations):
                if time.monotonic() >= deadline_monotonic:
                    llm_error = llm_error or "llm_timeout: turn_deadline_exceeded"
                    if not tool_records:
                        degraded_mode = True
                        raw_reply = _degraded_reply_for_error("llm_timeout")
                    break

                try:
                    gen_kwargs: dict[str, Any] = {
                        "system_prompt": full_system_prompt,
                        "messages": llm_messages,
                        "tools": tool_schemas,
                    }
                    if supports_deadline:
                        gen_kwargs["deadline_monotonic"] = deadline_monotonic
                    llm_resp = self.llm_client.generate(**gen_kwargs)
                except LLMError as exc:
                    llm_error = redact_sensitive_text(f"{exc.code}: {exc}")
                    degraded_mode = True
                    raw_reply = _degraded_reply_for_error(exc.code)
                    break

                if llm_resp.tool_calls:
                    llm_messages.append(
                        LLMMessage(
                            role="assistant",
                            text=llm_resp.text,
                            tool_calls=llm_resp.tool_calls,
                            raw_parts=llm_resp.raw_parts,
                        )
                    )
                    step_had_new_call = False
                    for tc in llm_resp.tool_calls:
                        sig = (tc.name, json.dumps(tc.arguments, sort_keys=True, default=str))
                        if sig in seen_calls:
                            result_payload = {
                                "error": (
                                    f"Repeated identical call to {tc.name} blocked. "
                                    "Use the previous result or ask the user to clarify."
                                )
                            }
                            ok = False
                        else:
                            step_had_new_call = True
                            seen_calls.add(sig)
                            ok, result_payload = execute_tool(tc.name, tc.arguments, ctx)

                        tool_records.append(
                            {
                                "tool_name": tc.name,
                                "args": tc.arguments,
                                "ok": ok,
                                "result": result_payload,
                            }
                        )
                        llm_messages.append(
                            LLMMessage(
                                role="tool",
                                tool_name=tc.name,
                                tool_response=result_payload,
                            )
                        )
                    if not step_had_new_call:
                        consecutive_blocked_steps += 1
                        if consecutive_blocked_steps >= 2:
                            llm_error = llm_error or "repeated_blocked_tool_calls"
                            break
                    else:
                        consecutive_blocked_steps = 0
                    continue

                if llm_resp.text:
                    raw_reply = llm_resp.text
                    break

                # Empty response (neither tool_calls nor text): fail fast rather than burning iterations
                llm_error = "llm_malformed_response: empty_model_response"
                degraded_mode = True
                raw_reply = _degraded_reply_for_error("llm_malformed_response")
                break

            # 6. Iteration-budget / deadline fallback if loop ended without text
            if raw_reply is None:
                llm_error = llm_error or "max_tool_iterations_reached"
                raw_reply = self._build_iteration_fallback(tool_records)

            # 7. Output guardrails + factual grounding verification
            trusted_parts: list[str] = []
            if returning_summary.welcome_message:
                trusted_parts.append(returning_summary.welcome_message)
            for pref in prefs:
                trusted_parts.append(f"{pref.preference_key}={pref.preference_value}")
                if pref.metadata:
                    trusted_parts.append(json.dumps(pref.metadata))
            trusted_context_text = "\n".join(trusted_parts) if trusted_parts else None

            if not degraded_mode:
                guarded = check_output_guardrail(raw_reply, executed_tools=tool_records)
                final_reply = sanitize_message_content(
                    verify_grounding(
                        guarded,
                        user_message=message,
                        tool_records=tool_records,
                        trusted_context_text=trusted_context_text,
                    )
                )
            else:
                final_reply = sanitize_message_content(raw_reply)

            # 8. Build vehicle cards exclusively from SQLite records
            card_ids: list[int]
            if any(r["tool_name"] == "search_inventory" and r["ok"] for r in tool_records):
                card_ids = ctx.search_card_ids
            else:
                card_ids = ctx.detail_card_ids
            cards = build_vehicle_cards(card_ids, self.inventory)

            # 9. Persist assistant message and redacted trace
            self.sessions.add_message(
                session_id,
                user_id,
                "assistant",
                final_reply,
                vehicle_cards=[c.listing_id for c in cards],
            )
            assistant_persisted = True
            greeting_was_refused = (not degraded_mode) and (
                final_reply == NO_TOOL_GROUNDING_REFUSAL
                or "Verification note:" in final_reply
            )
            if (
                not degraded_mode
                and not greeting_was_refused
                and returning_summary.welcome_message
                and (not session.context_summary or "welcome_shown=true" not in session.context_summary)
            ):
                updated_summary = (
                    f"{session.context_summary}; welcome_shown=true"
                    if session.context_summary
                    else "welcome_shown=true"
                )
                self.sessions.update_context_summary(session_id, user_id, updated_summary)
            elapsed_ms = int((time.monotonic_ns() - start_ns) / 1_000_000)
            tool_names = [r["tool_name"] for r in tool_records]
            self._record_trace(
                session_id=session_id,
                user_id=user_id,
                user_message=message,
                tool_names=tool_names,
                tool_records=tool_records,
                execution_ms=elapsed_ms,
                llm_error=llm_error,
                assistant_response=final_reply,
            )

            resp = ChatResponse(
                reply=final_reply,
                vehicle_cards=cards,
                session_id=session_id,
                degraded_mode=degraded_mode,
                tools_called=tool_names,
            )
            if clean_request_id:
                self.memory_repo.save_chat_request_response(
                    clean_request_id,
                    session_id=session_id,
                    user_id=user_id,
                    response_dict=resp.model_dump(mode="json"),
                )
            return resp
        except Exception:
            if not assistant_persisted and user_msg.id is not None:
                self.memory_repo.delete_message(user_msg.id)
            raise

    def _build_earlier_summary(self, earlier_messages: list[SessionMessage]) -> str:
        """Build a compact plain-text digest of older session messages beyond the active window."""
        parts: list[str] = []
        for msg in earlier_messages:
            snippet = " ".join((msg.content or "").split())
            if len(snippet) > 100:
                snippet = snippet[:97] + "..."
            cards_note = ""
            if msg.vehicle_cards:
                cards_note = " [cards: " + ", ".join(f"#{lid}" for lid in msg.vehicle_cards[:6]) + "]"
            parts.append(f"{msg.role}: {snippet}{cards_note}")
        joined = sanitize_message_content(" | ".join(parts))
        return joined[:800] if len(joined) > 800 else joined

    def _build_iteration_fallback(self, tool_records: list[dict[str, Any]]) -> str:
        """Provide a safe grounded fallback when the tool-calling loop hits its iteration cap."""
        for rec in reversed(tool_records):
            if rec["tool_name"] == "search_inventory" and rec["ok"]:
                msg = (rec["result"] or {}).get("message")
                if msg:
                    return f"{msg} Let me know if you'd like details on any of these listings."
            if rec["tool_name"] == "resolve_listing_reference" and rec["ok"]:
                clar = (rec["result"] or {}).get("clarification_prompt")
                if clar:
                    return clar
        return (
            "I reached the tool-step limit for this turn. Could you rephrase or tell me "
            "which specific make, budget, or listing you'd like to focus on?"
        )

    def _record_trace(
        self,
        *,
        session_id: str,
        user_id: str,
        user_message: str,
        tool_names: list[str],
        tool_records: list[dict[str, Any]],
        execution_ms: int,
        llm_error: Optional[str],
        assistant_response: str,
    ) -> None:
        redacted_calls = redact_sensitive_text(json.dumps(tool_records, default=str))
        self.memory_repo.save_agent_trace(
            session_id=session_id,
            user_id=user_id,
            user_message=redact_sensitive_text(user_message),
            tool_names=tool_names,
            tool_calls_json=redacted_calls,
            execution_ms=execution_ms,
            llm_error=redact_sensitive_text(llm_error) if llm_error else None,
            assistant_response=redact_sensitive_text(assistant_response),
        )
