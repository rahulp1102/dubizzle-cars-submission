"""Shared pytest fixtures for dubizzle AI."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.db import init_db


@pytest.fixture()
def temp_db(tmp_path: Path) -> Path:
    """Provide an isolated, initialised SQLite database path per test."""
    db_path = tmp_path / "test_dubizzle.db"
    init_db(db_path)
    return db_path
