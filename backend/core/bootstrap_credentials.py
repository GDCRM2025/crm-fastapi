from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


class BootstrapCredentialError(RuntimeError):
    """Raised when a bootstrap credential exists but cannot be used safely."""


@dataclass(frozen=True)
class BootstrapCredential:
    value: str
    source: str


_SPECS = {
    "database_url": ("DATABASE_URL", "GD_DATABASE_URL_FILE"),
    "credential_vault_key": ("GD_CREDENTIAL_MASTER_KEY", "GD_CREDENTIAL_MASTER_KEY_FILE"),
}


def _read_secret_file(path: Path, *, source: str, strict_permissions: bool) -> BootstrapCredential | None:
    try:
        if not path.is_file():
            return None
        mode = stat.S_IMODE(path.stat().st_mode)
        if strict_permissions and mode & 0o077:
            raise BootstrapCredentialError(
                f"El archivo bootstrap {path.name} tiene permisos inseguros; requiere 0600 o más restrictivo."
            )
        value = path.read_text(encoding="utf-8").strip()
    except BootstrapCredentialError:
        raise
    except OSError as exc:
        raise BootstrapCredentialError(f"No fue posible leer el secreto bootstrap {path.name}.") from exc
    if not value:
        raise BootstrapCredentialError(f"El secreto bootstrap {path.name} está vacío.")
    return BootstrapCredential(value=value, source=source)


def load_bootstrap_credential(name: str) -> BootstrapCredential | None:
    """Load a root credential without ever logging or returning it as metadata.

    Precedence is deliberately fixed: systemd credential, protected file, then
    the legacy environment variable for backwards compatibility.
    """
    if name not in _SPECS:
        raise KeyError(name)
    env_name, file_env_name = _SPECS[name]

    credential_dir = str(os.getenv("CREDENTIALS_DIRECTORY") or "").strip()
    if credential_dir:
        found = _read_secret_file(
            Path(credential_dir) / name,
            source="SYSTEMD_CREDENTIAL",
            strict_permissions=False,
        )
        if found:
            return found

    configured_file = str(os.getenv(file_env_name) or "").strip()
    if configured_file:
        candidates = [(Path(configured_file).expanduser(), True)]
    else:
        candidates = [
            (Path("/etc/greendiamond/secrets") / name, False),
            (Path("/opt/greendiamond/shared/secrets/bootstrap") / name, True),
        ]
    for path, required_if_present in candidates:
        try:
            found = _read_secret_file(path, source="SECURE_FILE", strict_permissions=True)
        except BootstrapCredentialError:
            # A root-only /etc parent may be intentionally non-traversable to
            # the service user. In that case continue to the supported shared
            # fallback. An explicitly configured or shared file still fails closed.
            if required_if_present:
                raise
            continue
        if found:
            return found

    legacy = str(os.getenv(env_name) or "").strip()
    if legacy:
        return BootstrapCredential(value=legacy, source="LEGACY_ENV")
    return None


def bootstrap_source(name: str) -> str:
    credential = load_bootstrap_credential(name)
    return credential.source if credential else "NOT_CONFIGURED"
