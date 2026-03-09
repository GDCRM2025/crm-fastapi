from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from backend.core.database import get_db
from backend.core.auth import get_current_user, require_admin
from backend.core.security import hash_password, verify_password
from backend.modules.login.repository import User
from .schemas import UserOut, UserCreate, ChangePassword

router = APIRouter(prefix="/users", tags=["users"])

@router.get("", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(User).all()

@router.post("", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    exists = db.query(User).filter(User.email == payload.email).first()
    if exists:
        raise HTTPException(status_code=400, detail="El email ya existe")
    u = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u

@router.post("/change-password")
def change_password(body: ChangePassword, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    if not verify_password(body.old_password, me.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Contraseña actual incorrecta")
    me.password_hash = hash_password(body.new_password)
    db.add(me)
    db.commit()
    return {"ok": True}
