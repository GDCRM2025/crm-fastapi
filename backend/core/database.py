from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base


# Carga .env desde la RAÍZ del proyecto, aunque ejecutes uvicorn desde otro lado
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # .../CRM 2025
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _normalize_sqlalchemy_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        # psycopg3
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


DATABASE_URL = _normalize_sqlalchemy_url(os.getenv("DATABASE_URL", "")) or \
    "postgresql+psycopg://BDGD:SpC18302020@127.0.0.1:5432/BDGD"

if not DATABASE_URL.startswith("postgresql+psycopg://"):
    raise RuntimeError(f"DATABASE_URL debe ser PostgreSQL (postgresql+psycopg://...). Actual: {DATABASE_URL!r}")

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
