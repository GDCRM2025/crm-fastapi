from __future__ import annotations

import base64
import hashlib
import hmac
import os

try:
    from passlib.context import CryptContext  # type: ignore
except Exception:  # pragma: no cover
    CryptContext = None  # type: ignore


_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto") if CryptContext else None


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s or "") + pad)


def hash_password(password: str) -> str:
    """
    Hash de password robusto para entornos cPanel donde `passlib` puede no estar instalado.
    - Preferimos passlib(bcrypt) si existe.
    - Fallback: PBKDF2-HMAC-SHA256 con salt aleatorio.
    """
    if _pwd_context is not None:
        return _pwd_context.hash(password)

    salt = os.urandom(16)
    iterations = 200_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64e(salt)}${_b64e(dk)}"


def verify_password(plain: str, hashed: str) -> bool:
    """
    Verificación robusta:
    - passlib(bcrypt) si está disponible
    - bcrypt directo si está instalado y el hash es $2...
    - pbkdf2_sha256$...
    - legacy: comparación directa (por compat con datos antiguos)
    """
    if not hashed:
        return False

    if _pwd_context is not None:
        try:
            return bool(_pwd_context.verify(plain, hashed))
        except Exception:
            pass

    if str(hashed).startswith("$2"):
        try:
            import bcrypt  # type: ignore

            return bool(bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8")))
        except Exception:
            return False

    if str(hashed).startswith("pbkdf2_sha256$"):
        try:
            _, it_s, salt_s, dk_s = str(hashed).split("$", 3)
            iterations = int(it_s)
            salt = _b64d(salt_s)
            expected = _b64d(dk_s)
            actual = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False

    return hmac.compare_digest(str(plain), str(hashed))
