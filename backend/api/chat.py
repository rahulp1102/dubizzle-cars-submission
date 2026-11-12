"""FastAPI route for grounded conversational car discovery."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.agent.orchestrator import AgentOrchestrator
from backend.api.deps import get_agent_orchestrator
from backend.models.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    orchestrator: AgentOrchestrator = Depends(get_agent_orchestrator),
) -> ChatResponse:
    """Process one conversational turn via the bounded agent orchestrator."""
    return orchestrator.handle_turn(
        session_id=payload.session_id,
        user_id=payload.user_id,
        message=payload.message,
        request_id=payload.request_id,
    )
