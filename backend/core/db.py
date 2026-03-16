import os
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

def _get_dsn() -> str:
    import os
    dsn = (os.getenv("DATABASE_URL") or "").strip()
    if not dsn:
        raise RuntimeError('DATABASE_URL no está seteada. Ej:\nexport DATABASE_URL="postgresql://BDGD:***@localhost:5432/BDGD"')
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


_engine = None

def get_engine():
    global _engine
    if _engine is None:
        dsn = _get_dsn()
        # Force UTF-8 at connect time to avoid UnicodeDecodeError in some hostings
        # where the client encoding ends up as SQL_ASCII.
        connect_args = {}
        if dsn.startswith("postgresql"):
            connect_args = {"options": "-c client_encoding=UTF8"}

        _engine = create_engine(
            dsn,
            pool_pre_ping=True,
            future=True,
            connect_args=connect_args,
        )
    return _engine

@contextmanager
def get_connection() -> 'Generator[Connection, None, None]':
    eng = get_engine()
    with eng.connect() as conn:
        yield conn
