# backend/core/settings.py

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]  # .../CRM 2025


class Settings(BaseSettings):
    # In production, set JWT_SECRET (preferred) or SECRET_KEY via env.
    # Default is intentionally weak for local dev only.
    SECRET_KEY: str = "dev-secret-change-me"
    JWT_SECRET: str | None = None
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # In production, always set DATABASE_URL via env (no cleartext default).
    DATABASE_URL: str = "postgresql+psycopg://BDGD:SpC18302020@127.0.0.1:5432/BDGD"

    CORS_ORIGINS: list[str] = [
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]

    DEV_NO_AUTH: bool = False

    model_config = SettingsConfigDict(
        env_file=(str(ROOT_DIR / ".env"), str(ROOT_DIR / ".env.local")),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
