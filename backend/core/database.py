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

def _patch_sqlalchemy_pg_version_bytes() -> None:
    """
    Hotfix: en algunos ambientes (cPanel/Passenger) el driver devuelve bytes para
    `select pg_catalog.version()`, y SQLAlchemy intenta hacer `re.match()` con patrón str.
    Resultado: `TypeError: cannot use a string pattern on a bytes-like object`.
    """
    try:
        import re
        from sqlalchemy.dialects.postgresql.base import PGDialect

        if getattr(PGDialect, "_gd_patched_version_bytes", False):
            return

        def _get_server_version_info(self, connection):  # type: ignore[no-untyped-def]
            v = connection.exec_driver_sql("select pg_catalog.version()").scalar()
            if isinstance(v, (bytes, bytearray, memoryview)):
                try:
                    v = bytes(v).decode("utf-8", "ignore")
                except Exception:
                    v = str(v)
            m = re.match(
                r".*(?:PostgreSQL|EnterpriseDB) "
                r"(\d+)\.?(\d+)?(?:\.(\d+))?(?:\.\d+)?(?:devel|beta)?",
                v,
            )
            if not m:
                raise AssertionError(
                    "Could not determine version from string '%s'" % v
                )
            return tuple([int(x) for x in m.group(1, 2, 3) if x is not None])

        PGDialect._get_server_version_info = _get_server_version_info  # type: ignore[assignment]
        PGDialect._gd_patched_version_bytes = True  # type: ignore[attr-defined]
    except Exception:
        # Nunca romper el arranque por esto.
        return


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


DATABASE_URL = _normalize_sqlalchemy_url(os.getenv("DATABASE_URL", ""))
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no está configurada. Defínela en el .env protegido del servidor.")
DATABASE_URL = _normalize_sqlalchemy_url(DATABASE_URL)

_patch_sqlalchemy_pg_version_bytes()

connect_args = {}
try:
    if str(DATABASE_URL or "").startswith("postgresql"):
        # Force UTF-8 at connect time to avoid bytes payloads / SQL_ASCII weirdness on some hostings.
        connect_args = {"options": "-c client_encoding=UTF8"}
except Exception:
    connect_args = {}

_pool_kwargs = {}
try:
    # Defaults de SQLAlchemy son ok para dev, pero en Passenger con muchos usuarios se necesita ajustar.
    # Solo activamos si vienen como env (para no romper entornos chicos).
    ps = str(os.getenv("CRM_DB_POOL_SIZE") or "").strip()
    mo = str(os.getenv("CRM_DB_MAX_OVERFLOW") or "").strip()
    pt = str(os.getenv("CRM_DB_POOL_TIMEOUT") or "").strip()
    if ps:
        _pool_kwargs["pool_size"] = max(1, int(ps))
    if mo:
        _pool_kwargs["max_overflow"] = max(0, int(mo))
    if pt:
        _pool_kwargs["pool_timeout"] = max(1, int(pt))
except Exception:
    _pool_kwargs = {}

engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True, connect_args=connect_args, **_pool_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
