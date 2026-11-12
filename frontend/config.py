"""Centralized configuration for the Streamlit frontend (API URL and timeouts only)."""

from __future__ import annotations

import os


def get_api_base_url() -> str:
    """Resolve FastAPI backend base URL without any direct DB or LLM credentials."""
    return os.getenv("DUBIZZLE_API_URL", "http://127.0.0.1:8000").rstrip("/")


REQUEST_TIMEOUT_SECONDS: float = float(os.getenv("DUBIZZLE_API_TIMEOUT", "12.0"))
CHAT_TIMEOUT_SECONDS: float = float(os.getenv("DUBIZZLE_CHAT_TIMEOUT", "60.0"))
