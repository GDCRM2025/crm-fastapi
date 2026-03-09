from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from sqlalchemy import text
from backend.core.db import get_connection
from backend.routers.auth import get_current_user, hash_password, _role_id_for

router = APIRouter(prefix="/users", tags=["users"])

def _ensure_usuarios_marcas(conn) -> None:
    """
    Tabla puente: qué marcas puede ver un usuario (ejecutivos).
    Si no existe, el ejecutivo queda con 0 leads.
    """
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS usuarios_marcas(
              id_usuario INTEGER NOT NULL REFERENCES usuarios(id_usuario) ON DELETE CASCADE,
              id_marca   INTEGER NOT NULL REFERENCES marcas(id_marca)   ON DELETE CASCADE,
              created_at TIMESTAMP DEFAULT now(),
              PRIMARY KEY (id_usuario, id_marca)
            )
            """
        )
    )

class MarcasIn(BaseModel):
    marcas: List[int]

class CreateUserIn(BaseModel):
    nombre: str
    email: str
    username: str | None = None
    rol: str
    password: str

def _ensure_admin_like(me):
    role = (me.get("role") or me.get("rol") or "").upper()
    if role not in ("ADMIN", "SUPERADMIN", "JEFE DE OPERACIONES", "OPERACIONES"):
        raise HTTPException(status_code=403, detail="Solo Admin/Operaciones")

@router.get("/{id_usuario}/brands")
def get_user_brands(id_usuario: int, me = Depends(get_current_user)):
    _ensure_admin_like(me)
    try:
        with get_connection() as conn:
            _ensure_usuarios_marcas(conn)
            rows = conn.execute(
                text("SELECT id_marca FROM usuarios_marcas WHERE id_usuario=:u"),
                {"u": id_usuario},
            ).fetchall()
            mids = [int(r[0]) for r in rows]
            conn.commit()
    except Exception:
        mids = []
    return {"id_usuario": id_usuario, "marcas": list(mids)}

@router.put("/{id_usuario}/brands")
def set_user_brands(id_usuario: int, body: MarcasIn, me = Depends(get_current_user)):
    _ensure_admin_like(me)
    try:
        with get_connection() as conn:
            _ensure_usuarios_marcas(conn)
            ok = conn.execute(
                text("SELECT 1 FROM usuarios WHERE id_usuario=:u"),
                {"u": id_usuario},
            ).scalar()
            if not ok:
                raise HTTPException(status_code=404, detail="Usuario no existe")
            # Si no existe la tabla, devolvemos 200 sin cambios (para no romper UI)
            conn.execute(text("DELETE FROM usuarios_marcas WHERE id_usuario=:u"), {"u": id_usuario})
            for mid in body.marcas or []:
                conn.execute(
                    text("INSERT INTO usuarios_marcas(id_usuario,id_marca) VALUES(:u,:m) ON CONFLICT DO NOTHING"),
                    {"u": id_usuario, "m": mid},
                )
            conn.commit()
    except HTTPException:
        raise
    except Exception:
        return {"ok": True, "id_usuario": id_usuario, "marcas": body.marcas, "notice": "No se pudo guardar usuarios_marcas"}
    return {"ok": True, "id_usuario": id_usuario, "marcas": body.marcas}


@router.post("/create")
def create_user(body: CreateUserIn, me = Depends(get_current_user)):
    _ensure_admin_like(me)
    nombre = (body.nombre or "").strip()
    email = (body.email or "").strip().lower()
    username = (body.username or email.split("@")[0]).strip().lower()
    rol = (body.rol or "").strip().upper()
    password = (body.password or "").strip()
    if not nombre or not email or not password:
        raise HTTPException(status_code=400, detail="Faltan datos")
    if rol not in ("ADMIN", "SUPERADMIN", "JEFE DE OPERACIONES", "OPERACIONES", "EJECUTIVO", "VENTAS", "OPERADOR", "CHOP", "CHOFER", "CONDUCTOR"):
        rol = "OPERADOR"
    with get_connection() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM usuarios WHERE lower(email)=:e OR lower(username)=:u"),
            {"e": email, "u": username},
        ).first()
        if exists:
            raise HTTPException(status_code=400, detail="Usuario ya existe")
        rid = _role_id_for(conn, rol)
        hp = hash_password(password)
        row = conn.execute(
            text(
                """
                INSERT INTO usuarios(nombre,email,username,hashed_password,telefono,cargo,id_rol,rol,is_active,created_at,updated_at)
                VALUES (:n,:e,:u,:hp,NULL,:cargo,:rid,:rol,TRUE,now(),now())
                RETURNING id_usuario
                """
            ),
            {"n": nombre, "e": email, "u": username, "hp": hp, "cargo": rol, "rid": rid, "rol": rol},
        ).fetchone()
        conn.commit()
    return {"ok": True, "id_usuario": int(row[0]) if row else None}
