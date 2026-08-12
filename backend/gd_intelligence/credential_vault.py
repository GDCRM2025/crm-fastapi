from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from backend.core.bootstrap_credentials import bootstrap_source, load_bootstrap_credential


SECRET_KEYS = re.compile(r"(?i)(secret|token|password|api[_ -]?key|credential|refresh)")


class VaultUnavailable(RuntimeError):
    pass


class CredentialValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def redact(value: Any) -> Any:
    """Remove secret-like fields before metadata reaches logs or API responses."""
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items() if not SECRET_KEYS.search(str(k))}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _loopback_database() -> bool:
    host = urlsplit(str(os.getenv("DATABASE_URL") or "").replace("postgresql+psycopg://", "postgresql://")).hostname
    return host in {"127.0.0.1", "localhost", "::1"}


def _local_key_path() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime/mac/credential_vault.key"


def load_master_key() -> bytes:
    credential = load_bootstrap_credential("credential_vault_key")
    if credential:
        return credential.value.encode("ascii")
    path = _local_key_path()
    if not _loopback_database():
        raise VaultUnavailable("El almacenamiento seguro de credenciales no está disponible.")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key + b"\n")
    return key


def validate_credential(provider: str, secret: str, *, domain: str | None = None) -> dict[str, Any]:
    provider = str(provider or "").upper()
    if not secret or len(secret.strip()) < 8:
        raise CredentialValidationError("CREDENTIAL_INVALID", "La credencial no tiene un formato válido.")
    try:
        if provider in {"PAGESPEED", "CRUX"}:
            response = requests.get(
                "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
                params={"url": f"https://{domain}/" if domain else "https://www.google.com/", "key": secret.strip(), "strategy": "mobile"},
                timeout=20,
            )
        elif provider == "META":
            response = requests.get(
                "https://graph.facebook.com/v25.0/me",
                params={"fields": "id,name", "access_token": secret.strip()},
                timeout=20,
            )
        else:
            raise CredentialValidationError("CREDENTIAL_NOT_SUPPORTED", "Esta integración utiliza un flujo de conexión diferente.")
        if response.status_code in {400, 401, 403}:
            raise CredentialValidationError("AUTH_REJECTED", "El proveedor rechazó la credencial. Revisa el dato e inténtalo nuevamente.")
        response.raise_for_status()
        return {"valid": True, "provider": provider}
    except CredentialValidationError:
        raise
    except requests.RequestException as exc:
        raise CredentialValidationError("PROVIDER_UNAVAILABLE", "No pudimos verificar la conexión ahora. Inténtalo nuevamente.") from exc


class CredentialVault:
    """Backend-only credential storage. There is intentionally no public read method."""

    def __init__(self, key: bytes | None = None, validator: Callable[..., dict[str, Any]] = validate_credential):
        try:
            self._fernet = Fernet(key or load_master_key())
        except (ValueError, TypeError) as exc:
            raise VaultUnavailable("La configuración segura de credenciales no es válida.") from exc
        self._validator = validator

    def encrypt(self, secret: str) -> str:
        return self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")

    def _decrypt_internal(self, encrypted: str) -> str:
        try:
            return self._fernet.decrypt(encrypted.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise VaultUnavailable("No fue posible utilizar la credencial almacenada.") from exc

    def validate(self, provider: str, secret: str, *, domain: str | None = None) -> dict[str, Any]:
        return self._validator(provider, secret, domain=domain)

    def store(self, conn, *, integration_id: int, credential_type: str, secret: str, actor: str, domain: str | None = None) -> dict[str, Any]:
        provider = conn.execute(text("SELECT provider FROM wi_integrations WHERE id=:id"), {"id": integration_id}).scalar()
        if not provider:
            raise KeyError("INTEGRATION_NOT_FOUND")
        self.validate(str(provider), secret, domain=domain)
        existed = bool(conn.execute(text("SELECT 1 FROM wi_integration_credentials WHERE integration_id=:id AND credential_type=:type"), {"id": integration_id, "type": credential_type}).scalar())
        row = conn.execute(text("""
          INSERT INTO wi_integration_credentials(
            integration_id,credential_type,encrypted_value,masked_suffix,status,created_by,updated_by,
            last_verified_at,last_success_at,last_error_at,last_error_code
          ) VALUES(:id,:type,:value,:suffix,'CONFIGURED',:actor,:actor,now(),now(),NULL,NULL)
          ON CONFLICT(integration_id,credential_type) DO UPDATE SET
            encrypted_value=excluded.encrypted_value,masked_suffix=excluded.masked_suffix,status='CONFIGURED',
            updated_by=excluded.updated_by,updated_at=now(),last_verified_at=now(),last_success_at=now(),
            last_error_at=NULL,last_error_code=NULL
          RETURNING id,integration_id,credential_type,masked_suffix,status,updated_at,last_verified_at,last_success_at
        """), {"id": integration_id, "type": credential_type, "value": self.encrypt(secret), "suffix": secret[-4:], "actor": actor}).mappings().one()
        action = "CREDENTIAL_REPLACED" if existed else "CREDENTIAL_CONFIGURED"
        self._event(conn, integration_id, credential_type, action, actor, True)
        conn.execute(text("UPDATE wi_integrations SET enabled=true,status='CONNECTED',last_verified_at=now(),last_success_at=now(),last_failure_at=NULL,last_error_code=NULL,last_error_safe=NULL,updated_at=now() WHERE id=:id"), {"id": integration_id})
        return self.metadata(dict(row))

    def use_internal(self, conn, *, integration_id: int, credential_type: str) -> str:
        encrypted = conn.execute(text("SELECT encrypted_value FROM wi_integration_credentials WHERE integration_id=:id AND credential_type=:type AND status='CONFIGURED'"), {"id": integration_id, "type": credential_type}).scalar()
        if not encrypted:
            raise KeyError("CREDENTIAL_NOT_CONFIGURED")
        return self._decrypt_internal(str(encrypted))

    def verify(self, conn, *, integration_id: int, credential_type: str, actor: str, domain: str | None = None) -> dict[str, Any]:
        provider = conn.execute(text("SELECT provider FROM wi_integrations WHERE id=:id"), {"id": integration_id}).scalar()
        secret = self.use_internal(conn, integration_id=integration_id, credential_type=credential_type)
        try:
            self.validate(str(provider), secret, domain=domain)
        except CredentialValidationError:
            raise
        conn.execute(text("UPDATE wi_integration_credentials SET last_verified_at=now(),last_success_at=now(),last_error_at=NULL,last_error_code=NULL WHERE integration_id=:id AND credential_type=:type"), {"id": integration_id, "type": credential_type})
        conn.execute(text("UPDATE wi_integrations SET enabled=true,status='CONNECTED',last_verified_at=now(),last_success_at=now(),last_failure_at=NULL,last_error_code=NULL,last_error_safe=NULL,updated_at=now() WHERE id=:id"), {"id": integration_id})
        self._event(conn, integration_id, credential_type, "CONNECTION_VERIFIED", actor, True)
        return {"configured": True, "status": "CONNECTED"}

    def revoke(self, conn, *, integration_id: int, credential_type: str, actor: str) -> dict[str, Any]:
        changed = conn.execute(text("UPDATE wi_integration_credentials SET encrypted_value=NULL,masked_suffix=NULL,status='REVOKED',updated_by=:actor,updated_at=now() WHERE integration_id=:id AND credential_type=:type AND status='CONFIGURED'"), {"id": integration_id, "type": credential_type, "actor": actor}).rowcount
        if not changed:
            raise KeyError("CREDENTIAL_NOT_CONFIGURED")
        conn.execute(text("UPDATE wi_integrations SET enabled=false,status='DISABLED',last_error_code=NULL,last_error_safe=NULL,updated_at=now() WHERE id=:id"), {"id": integration_id})
        self._event(conn, integration_id, credential_type, "INTEGRATION_DISCONNECTED", actor, True)
        return {"configured": False, "status": "REVOKED"}

    def record_failure(self, conn, *, integration_id: int, credential_type: str, actor: str, error_code: str, message: str | None = None) -> None:
        conn.execute(text("UPDATE wi_integration_credentials SET last_verified_at=now(),last_error_at=now(),last_error_code=:code WHERE integration_id=:id AND credential_type=:type"), {"id": integration_id, "type": credential_type, "code": error_code})
        conn.execute(text("UPDATE wi_integrations SET status=CASE WHEN EXISTS(SELECT 1 FROM wi_integration_credentials c WHERE c.integration_id=:id AND c.status='CONFIGURED') THEN status ELSE 'ERROR' END,last_verified_at=now(),last_failure_at=now(),last_error_code=:code,last_error_safe=:message,updated_at=now() WHERE id=:id"), {"id": integration_id, "code": error_code, "message": str(message or "No pudimos verificar la conexión.")[:300]})
        self._event(conn, integration_id, credential_type, "CONNECTION_FAILED", actor, False, error_code)

    @staticmethod
    def metadata(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row.get(key) for key in ("integration_id", "credential_type", "masked_suffix", "status", "updated_at", "last_verified_at", "last_success_at")}

    @staticmethod
    def _event(conn, integration_id: int, credential_type: str, action: str, actor: str, success: bool, error_code: str | None = None) -> None:
        conn.execute(text("INSERT INTO wi_integration_credential_events(integration_id,credential_type,action,actor,success,error_code) VALUES(:id,:type,:action,:actor,:success,:code)"), {"id": integration_id, "type": credential_type, "action": action, "actor": actor, "success": success, "code": error_code})


def vault_bootstrap_status(conn, *, verify_decryption: bool = True) -> dict[str, Any]:
    """Return non-sensitive health metadata and fail closed for unreadable records."""
    table_exists = bool(
        conn.execute(text("SELECT to_regclass('public.wi_integration_credentials') IS NOT NULL")).scalar()
    )
    if not table_exists:
        return {"table_ready": False, "configured_records": 0, "key_source": "NOT_REQUIRED", "decrypt_check": "NOT_REQUIRED"}
    encrypted = list(
        conn.execute(
            text(
                "SELECT encrypted_value FROM wi_integration_credentials "
                "WHERE status='CONFIGURED' AND encrypted_value IS NOT NULL"
            )
        ).scalars()
    )
    configured = len(encrypted)
    source = bootstrap_source("credential_vault_key")
    if source == "NOT_CONFIGURED" and _local_key_path().is_file():
        source = "LOCAL_DEV_FILE"
    if not configured:
        return {"table_ready": True, "configured_records": 0, "key_source": source, "decrypt_check": "NOT_REQUIRED"}
    if source == "NOT_CONFIGURED":
        raise VaultUnavailable("Existen credenciales cifradas, pero la llave maestra no está disponible.")
    if verify_decryption:
        vault = CredentialVault()
        for value in encrypted:
            vault._decrypt_internal(str(value))
    return {"table_ready": True, "configured_records": configured, "key_source": source, "decrypt_check": "PASS"}
