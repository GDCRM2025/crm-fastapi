from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .exceptions import SIICertificateError


class SIICredentialVault:
    """Adaptador SII del CredentialVault central; persiste solo blobs cifrados."""

    def __init__(self) -> None:
        try:
            from backend.gd_intelligence.credential_vault import CredentialVault
            self._vault = CredentialVault()
        except Exception as exc:
            raise SIICertificateError("CredentialVault central no disponible") from exc
        try:
            from backend.core.storage import persistent_data_root
            base = persistent_data_root()
        except Exception:
            base = Path(os.getenv("GD_PERSISTENT_DATA_DIR") or Path(__file__).resolve().parents[2] / "data")
        self._root = Path(os.getenv("SII_PRIVATE_CREDENTIAL_DIR") or base / "sii" / "credentials")

    def _path(self, secret_key: str) -> Path:
        name = Path(secret_key).name
        if not name or name != secret_key or not name.endswith(".credential"):
            raise SIICertificateError("Referencia de certificado invalida")
        return self._root / name

    def put_certificate(self, conn, *, secret_key: str, certificate: bytes, password: str, actor_id: int | None) -> None:
        import base64
        payload = json.dumps({"pfx": base64.b64encode(certificate).decode("ascii"), "password": password}, separators=(",", ":"))
        encrypted = self._vault.encrypt(payload)
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._root, 0o700)
        target = self._path(secret_key)
        fd, temporary = tempfile.mkstemp(prefix=".sii-", dir=self._root, text=True)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load_certificate_internal(self, conn, secret_key: str) -> tuple[bytes, str]:
        import base64
        target = self._path(secret_key)
        if not target.is_file():
            raise SIICertificateError("Certificado no configurado")
        try:
            payload = json.loads(self._vault._decrypt_internal(target.read_text(encoding="ascii").strip()))
            return base64.b64decode(payload["pfx"]), str(payload["password"])
        except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
            raise SIICertificateError("No fue posible descifrar el certificado") from exc

    def delete(self, conn, secret_key: str) -> None:
        self._path(secret_key).unlink(missing_ok=True)
