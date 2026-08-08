from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from backend.core.database import get_db
from backend.models.usuario import Usuario, Marca, UsuarioMarca
from backend.schemas.usuario import UsuarioCreate, UsuarioUpdate, UsuarioOut
from backend.routers.auth import get_current_user, hash_password

router = APIRouter(prefix="/usuarios", tags=["usuarios"])


def _require_admin(me) -> None:
    role = str((me or {}).get("role") or (me or {}).get("rol") or "").upper().replace(" ", "").replace("_", "")
    if role not in ("ADMIN", "SUPERADMIN"):
        raise HTTPException(status_code=403, detail="Solo Admin")

def set_marcas(db: Session, user: Usuario, marcas_ids: List[int] | None):
    if marcas_ids is None:
        return
    # borramos y reasignamos
    db.execute(UsuarioMarca.delete().where(UsuarioMarca.c.id_usuario == user.id_usuario))
    if marcas_ids:
        for mid in marcas_ids:
            # opcional: validar existencia
            m = db.get(Marca, mid)
            if not m:
                raise HTTPException(404, f"Marca {mid} no existe")
            db.execute(UsuarioMarca.insert().values(id_usuario=user.id_usuario, id_marca=mid))

@router.get("/", response_model=List[UsuarioOut])
def list_usuarios(db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_admin(me)
    return db.query(Usuario).all()

@router.post("/", response_model=UsuarioOut, status_code=201)
def create_usuario(payload: UsuarioCreate, db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_admin(me)
    # OJO: aquí asumo que ya guardas password_hash (bcrypt) en tu flujo; para avanzar lo dejamos en claro.
    if db.query(Usuario).filter(Usuario.email == payload.email).first():
        raise HTTPException(400, "Email ya existe")
    u = Usuario(
        nombre_usuario=payload.nombre_usuario,
        email=payload.email,
        nivel=payload.nivel,
        status=payload.status,
        password_hash=hash_password(payload.password),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    set_marcas(db, u, payload.marcas_ids)
    db.commit()
    db.refresh(u)
    return u

@router.get("/{id_usuario}", response_model=UsuarioOut)
def get_usuario(id_usuario: int, db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_admin(me)
    u = db.get(Usuario, id_usuario)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    return u

@router.put("/{id_usuario}", response_model=UsuarioOut)
def update_usuario(id_usuario: int, payload: UsuarioUpdate, db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_admin(me)
    u = db.get(Usuario, id_usuario)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    data = payload.model_dump(exclude_unset=True)
    marcas_ids = data.pop("marcas_ids", None)
    password   = data.pop("password", None)

    for k, v in data.items():
        setattr(u, k, v)
    if password is not None:
        u.password_hash = hash_password(password)

    db.commit()
    db.refresh(u)
    set_marcas(db, u, marcas_ids)
    db.commit()
    db.refresh(u)
    return u

@router.delete("/{id_usuario}", status_code=204)
def delete_usuario(id_usuario: int, db: Session = Depends(get_db), me=Depends(get_current_user)):
    _require_admin(me)
    u = db.get(Usuario, id_usuario)
    if not u:
        raise HTTPException(404, "Usuario no encontrado")
    # Limpia relaciones de marcas
    db.execute(UsuarioMarca.delete().where(UsuarioMarca.c.id_usuario == id_usuario))
    db.delete(u)
    db.commit()
    return None
