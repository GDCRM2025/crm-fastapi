import os
import importlib.util
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from typing import Generator

# intenta cargar .env si existe (sin dependencia externa)
def _load_env_file():
    root = Path(__file__).resolve().parents[2]  # backend/core -> backend -> project
    env_path = root / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)

_load_env_file()

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
                raise AssertionError("Could not determine version from string '%s'" % v)
            return tuple([int(x) for x in m.group(1, 2, 3) if x is not None])

        PGDialect._get_server_version_info = _get_server_version_info  # type: ignore[assignment]
        PGDialect._gd_patched_version_bytes = True  # type: ignore[attr-defined]
    except Exception:
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


def _get_dsn() -> str:
    dsn = _normalize_sqlalchemy_url(os.getenv("DATABASE_URL") or "")
    if not dsn:
        raise RuntimeError('DATABASE_URL no está seteada. Ej:\nexport DATABASE_URL="postgresql://BDGD:***@localhost:5432/BDGD"')
    return dsn


_engine = None

def get_engine():
    global _engine
    if _engine is None:
        _patch_sqlalchemy_pg_version_bytes()
        dsn = _get_dsn()
        # --- Stability knobs (shared hosting) ---
        # Goal: fail fast instead of hanging the only Passenger worker.
        pool_size = int(os.getenv("CRM_DB_POOL_SIZE", "3") or "3")
        max_overflow = int(os.getenv("CRM_DB_MAX_OVERFLOW", "2") or "2")
        pool_timeout = int(os.getenv("CRM_DB_POOL_TIMEOUT", "5") or "5")
        pool_recycle = int(os.getenv("CRM_DB_POOL_RECYCLE", "300") or "300")
        connect_timeout = int(os.getenv("CRM_DB_CONNECT_TIMEOUT", "5") or "5")
        stmt_timeout_ms = int(os.getenv("CRM_DB_STATEMENT_TIMEOUT_MS", "15000") or "15000")
        lock_timeout_ms = int(os.getenv("CRM_DB_LOCK_TIMEOUT_MS", "5000") or "5000")

        connect_args = {}
        if dsn.startswith("postgresql"):
            # Force UTF-8 at connect time to avoid UnicodeDecodeError in some hostings
            # where the client encoding ends up as SQL_ASCII.
            # Also add statement/lock timeouts to prevent indefinite waits.
            # (These are server-side timeouts, so they work even if the client can't cancel.)
            options = [
                "-c client_encoding=UTF8",
                f"-c statement_timeout={max(1000, stmt_timeout_ms)}",
                f"-c lock_timeout={max(500, lock_timeout_ms)}",
            ]
            connect_args = {
                "options": " ".join(options),
                "connect_timeout": max(1, connect_timeout),
            }

        _engine = create_engine(
            dsn,
            pool_pre_ping=True,
            pool_size=max(1, pool_size),
            max_overflow=max(0, max_overflow),
            pool_timeout=max(1, pool_timeout),
            pool_recycle=max(0, pool_recycle),
            future=True,
            connect_args=connect_args,
        )
    return _engine

@contextmanager
def get_connection() -> 'Generator[Connection, None, None]':
    eng = get_engine()
    with eng.connect() as conn:
        yield conn


def table_columns(conn: Connection, table_name: str, schema: str = "public") -> list[str]:
    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema=:s AND table_name=:t
            ORDER BY ordinal_position
            """
        ),
        {"s": schema, "t": str(table_name)},
    ).fetchall()
    return [r[0] for r in rows]
