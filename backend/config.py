"""Centralised configuration for dubizzle AI backend."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


class Settings:
    """Application settings, read from environment variables with sensible defaults."""

    # ── Paths ──
    PROJECT_ROOT: Path = _PROJECT_ROOT
    DATA_DIR: Path = _PROJECT_ROOT / "data"
    DATABASE_DIR: Path = _PROJECT_ROOT / "data" / "database"
    EXPORTS_DIR: Path = _PROJECT_ROOT / "data" / "exports"
    DATASET_PATH: Path = _PROJECT_ROOT / "Copy_of_sample_cars_dataset.xlsx"
    DATASET_SHEET: str = "raw dataset"

    # ── Database ──
    DATABASE_URL: str = os.getenv("DATABASE_URL", "data/database/dubizzle.db")

    @property
    def database_path(self) -> Path:
        """Resolve database path relative to project root."""
        raw = self.DATABASE_URL
        if raw == "data/database/dubizzle.db":
            raw = os.getenv("DATABASE_URL", raw)
        if raw.startswith("sqlite:///"):
            raw = raw[len("sqlite:///") :]
        p = Path(raw)
        if not p.is_absolute():
            p = self.PROJECT_ROOT / p
        return p

    @property
    def exports_dir(self) -> Path:
        """Resolve lead CSV export directory, honoring EXPORTS_DIR or isolating non-default DB paths."""
        raw_exports = os.getenv("EXPORTS_DIR", "").strip()
        if raw_exports:
            p = Path(raw_exports)
            return p if p.is_absolute() else (self.PROJECT_ROOT / p)

        default_db = (self.PROJECT_ROOT / "data" / "database" / "dubizzle.db").resolve()
        db_p = self.database_path.resolve()
        if db_p != default_db:
            return db_p.parent / "exports"
        return self.EXPORTS_DIR

    # Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    GEMINI_FALLBACK_MODELS: tuple[str, ...] = (
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    )

    # ── Application ──
    APP_ENV: str = os.getenv("APP_ENV", "development")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "")

    # ── Booking ──
    BOOKING_MIN_LEAD_TIME_HOURS: int = int(os.getenv("BOOKING_MIN_LEAD_TIME_HOURS", "2"))
    BOOKING_VIEWING_DURATION_MINUTES: int = int(
        os.getenv("BOOKING_VIEWING_DURATION_MINUTES", "60")
    )
    BOOKING_TIMEZONE: str = "Asia/Dubai"
    BOOKING_OPEN_HOUR: int = 8
    BOOKING_CLOSE_HOUR: int = 20
    BOOKING_LAST_START_HOUR: int = 19
    # 0=Monday ... 5=Saturday, 6=Sunday
    BOOKING_OPEN_DAYS: tuple[int, ...] = (0, 1, 2, 3, 4, 5)  # Mon-Sat

    # ── Currency ──
    USD_TO_AED_RATE: float = float(os.getenv("USD_TO_AED_RATE", "3.6725"))

    # ── Agent ──
    AGENT_MAX_TOOL_CALLS: int = int(os.getenv("AGENT_MAX_TOOL_CALLS", "5"))
    GEMINI_REQUEST_TIMEOUT_MS: int = int(os.getenv("GEMINI_REQUEST_TIMEOUT_MS", "20000"))
    AGENT_TURN_TIMEOUT_SECONDS: float = float(os.getenv("AGENT_TURN_TIMEOUT_SECONDS", "45.0"))

    @property
    def gemini_available(self) -> bool:
        """Check if Gemini API key is configured."""
        return bool(self.GEMINI_API_KEY) and self.GEMINI_API_KEY != "your-gemini-api-key-here"

    @property
    def admin_auth_configured(self) -> bool:
        """Check if an administrative API key is configured (non-empty and non-placeholder)."""
        key = self.ADMIN_API_KEY.strip()
        return bool(key) and key != "your-admin-api-key-here"


settings = Settings()
