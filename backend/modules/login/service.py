from sqlalchemy.orm import Session
from .repository import validate_user
from backend.core.security import create_access_token

def login(db: Session, email: str, password: str) -> str:
    user = validate_user(db, email, password)
    if not user:
        raise ValueError("Credenciales inválidas")
    return create_access_token({"sub": str(user.id), "email": email, "role": user.role})
