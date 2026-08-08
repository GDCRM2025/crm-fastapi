from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.core.db import get_db
from backend.core import settings as core_settings
from passlib.hash import bcrypt

router = APIRouter(prefix="/_debug", tags=["_debug"])

@router.get("/dbinfo")
def dbinfo(db: Session = Depends(get_db)):
    info = {"database_url": getattr(core_settings.settings, "DATABASE_URL", "N/A")}
    # Version y tablas
    try:
        ver = db.execute(text("select sqlite_version()")).scalar()
        info["sqlite_version"] = ver
    except Exception as e:
        info["sqlite_version"] = f"err: {e}"
    try:
        tables = db.execute(text("select name from sqlite_master where type='table' order by name")).scalars().all()
        info["tables"] = tables
    except Exception as e:
        info["tables"] = [f"err: {e}"]
    # Preview de columnas/filas clave
    for t in ["usuarios", "usuarios_marcas", "leads", "estados_lead"]:
        try:
            cols = db.execute(text(f"PRAGMA table_info({t})")).mappings().all()
            info[f"{t}__columns"] = [dict(c) for c in cols]
            sample = db.execute(text(f"SELECT * FROM {t} LIMIT 3")).mappings().all()
            info[f"{t}__sample"] = [dict(r) for r in sample]
        except Exception as e:
            info[f"{t}__columns"] = [f"no table ({e})"]
            info[f"{t}__sample"] = []
    return info

@router.post("/bootstrap")
def bootstrap(db: Session = Depends(get_db), password: str = Body("green123")):
    """
    Crea esquema mínimo si falta y seed de usuario greengd / password (bcrypt del body).
    Es idempotente.
    """
    # usuarios
    db.execute(text("""
    CREATE TABLE IF NOT EXISTS usuarios (
      id_usuario     INTEGER PRIMARY KEY AUTOINCREMENT,
      nombre         TEXT NOT NULL,
      email          TEXT NOT NULL UNIQUE,
      username       TEXT NOT NULL UNIQUE,
      hashed_password TEXT NOT NULL,
      telefono       TEXT,
      cargo          TEXT,
      id_rol         INTEGER DEFAULT 5, -- Ejecutivo de Ventas por defecto
      is_active      INTEGER DEFAULT 1,
      created_at     TEXT DEFAULT (datetime('now')),
      updated_at     TEXT
    );
    """))
    # usuarios_marcas
    db.execute(text("""
    CREATE TABLE IF NOT EXISTS usuarios_marcas (
      id_usuario INTEGER NOT NULL,
      id_marca   INTEGER NOT NULL,
      UNIQUE(id_usuario,id_marca)
    );
    """))
    # estados_lead
    db.execute(text("""
    CREATE TABLE IF NOT EXISTS estados_lead (
      id_estado INTEGER PRIMARY KEY AUTOINCREMENT,
      nombre    TEXT NOT NULL UNIQUE
    );
    """))
    # leads
    db.execute(text("""
    CREATE TABLE IF NOT EXISTS leads (
      id_lead        INTEGER PRIMARY KEY AUTOINCREMENT,
      nombre_cliente TEXT,
      email          TEXT,
      telefono       TEXT,
      id_marca       INTEGER,
      id_estado      INTEGER,
      fecha_evento   TEXT,
      monto_cotizado REAL,
      codigo_cliente TEXT,
      created_at     TEXT DEFAULT (datetime('now')),
      updated_at     TEXT
    );
    """))

    # estados seed básicos (idempotente)
    for nombre in ["Nuevo","Contactado","Cotizado","Cerrado","Perdido"]:
        db.execute(text("INSERT OR IGNORE INTO estados_lead(nombre) VALUES(:n)"), {"n": nombre})

    # seed usuario admin-like "greengd"
    row = db.execute(text("SELECT 1 FROM usuarios WHERE lower(username)=lower('greengd')")).scalar()
    if not row:
        hpw = bcrypt.hash(password)
        db.execute(text("""
          INSERT INTO usuarios(nombre,email,username,hashed_password,id_rol,is_active)
          VALUES(:n,:e,:u,:p,:r,1)
        """), {"n": "Green Admin", "e": "admin@greendiamond.local", "u": "greengd", "p": hpw, "r": 1})
    db.commit()
    return {"ok": True}
