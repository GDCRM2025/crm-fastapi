import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, status

# Para dev: cae a un secreto fijo si no seteas env.
# En prod: setea JWT_SECRET.
SECRET_KEY = os.getenv("JWT_SECRET", "dev_secret_change_me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "720"))  # 12h


def create_access_token(subject: str, role: str = "admin") -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


def verify_user(username: str, password: str) -> tuple[bool, str]:
    """
    Auth simple (dev). Si después quieres usuarios en DB, se cambia acá.
    Retorna (ok, role)
    """
    # default dev user:
    if username == "admin" and password == "admin":
        return True, "admin"

    # opcional: credenciales por env
    env_user = os.getenv("APP_USER")
    env_pass = os.getenv("APP_PASS")
    env_role = os.getenv("APP_ROLE", "admin")
    if env_user and env_pass and username == env_user and password == env_pass:
        return True, env_role

    return False, ""
