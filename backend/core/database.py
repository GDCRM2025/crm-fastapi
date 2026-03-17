from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base


# Carga .env desde la RAÍZ del proyecto, aunque ejecutes uvicorn desde otro lado
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # .../CRM 2025
load_dotenv(PROJECT_ROOT / ".env", override=False)

def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _preferred_pg_driver() -> str | None:
    # Prefer psycopg3 if installed; otherwise fallback to psycopg2.
    if _has_module("psycopg"):
        return "psycopg"
    if _has_module("psycopg2"):
        return "psycopg2"
    return None


def _normalize_sqlalchemy_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    driver = _preferred_pg_driver()

    # If URL already specifies a driver we don't have, rewrite to the available one.
    if url.startswith("postgresql+psycopg://") and driver == "psycopg2":
        url = url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql+psycopg2://") and driver == "psycopg":
        url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)

    # If URL doesn't specify a driver, pick one that exists.
    if url.startswith("postgresql://") and driver:
        if driver == "psycopg":
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        elif driver == "psycopg2":
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


DATABASE_URL = _normalize_sqlalchemy_url(os.getenv("DATABASE_URL", "")) or "postgresql://BDGD:SpC18302020@127.0.0.1:5432/BDGD"
DATABASE_URL = _normalize_sqlalchemy_url(DATABASE_URL)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
