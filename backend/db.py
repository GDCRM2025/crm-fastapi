"""
Compat shim: el proyecto importa `get_db` desde `backend.db`.
Aquí centralizamos el acceso a PostgreSQL.
"""
from backend.core.database import Base, SessionLocal, engine, get_db

__all__ = ["Base", "SessionLocal", "engine", "get_db"]
