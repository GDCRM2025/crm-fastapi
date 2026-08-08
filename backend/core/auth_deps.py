# backend/core/auth_deps.py
from typing import List, Optional, Dict, Any
from fastapi import Header, HTTPException, status, Depends
from jose import jwt, JWTError
from backend.core.settings import settings

def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token requerido")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

    # estructura mínima esperada
    sub = payload.get("sub")
    role = payload.get("role") or "Viewer"
    marcas = payload.get("marcas") or []
    name = payload.get("name") or ""
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido (sub)")

    return {"id": int(sub), "role": role, "marcas": list(marcas), "name": name}
