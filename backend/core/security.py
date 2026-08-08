# backend/core/security.py
from __future__ import annotations
import hashlib, hmac, os
from typing import Tuple

try:
    import bcrypt  # si está instalado, mejor
except Exception:  # opcional
    bcrypt = None  # type: ignore

# ---- pbkdf2 helpers (fallback) ----
def _pbkdf2_hash(password: str, salt: bytes, iterations: int = 200_000) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2:{iterations}:{salt.hex()}:{dk.hex()}"

def _pbkdf2_verify(password: str, stored: str) -> bool:
    # formato: pbkdf2:ITER:SALTHEX:HASHHEX
    try:
        _, it_s, salt_hex, hash_hex = stored.split(":")
        iterations = int(it_s)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False

def hash_password(password: str) -> str:
    if bcrypt:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    salt = os.urandom(16)
    return _pbkdf2_hash(password, salt)

def verify_password(password: str, stored: str) -> bool:
    # Soporta bcrypt ($2…) y nuestro pbkdf2 fallback
    if stored.startswith("$2"):
        if not bcrypt:
            return False
        try:
            return bcrypt.checkpw(password.encode(), stored.encode())
        except Exception:
            return False
    if stored.startswith("pbkdf2:"):
        return _pbkdf2_verify(password, stored)
    # último recurso: comparación simple (no recomendado, pero por compatibilidad)
    return hmac.compare_digest(stored, password)
