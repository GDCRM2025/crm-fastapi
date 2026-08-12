#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import dotenv_values
from sqlalchemy import create_engine, text


def _db_url(raw: str) -> str:
    raw = raw.strip().replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    return raw.replace("postgresql://", "postgresql+psycopg://", 1) if raw.startswith("postgresql://") else raw


def _write_once(path: Path, value: str) -> str:
    if path.exists():
        if not path.is_file():
            raise RuntimeError(f"BOOTSTRAP_TARGET_INVALID:{path.name}")
        path.chmod(0o600)
        return "PRESERVED"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value.strip() + "\n")
    return "CREATED"


def main() -> int:
    parser = argparse.ArgumentParser(description="Install CRM bootstrap credentials without printing values.")
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--target-dir", required=True, type=Path)
    args = parser.parse_args()

    if not args.env_file.is_file():
        raise RuntimeError("LEGACY_ENV_NOT_FOUND")
    legacy = {key: str(value or "").strip() for key, value in dotenv_values(args.env_file).items()}
    database_url = legacy.get("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL_NOT_CONFIGURED")

    engine = create_engine(_db_url(database_url), pool_pre_ping=True)
    with engine.connect() as conn:
        table_ready = bool(conn.execute(text("SELECT to_regclass('public.wi_integration_credentials') IS NOT NULL")).scalar())
        records = int(conn.execute(text("SELECT count(*) FROM wi_integration_credentials WHERE status='CONFIGURED'")).scalar()) if table_ready else 0

    args.target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.target_dir.chmod(0o700)
    database_result = _write_once(args.target_dir / "database_url", database_url)

    key_path = args.target_dir / "credential_vault_key"
    legacy_key = legacy.get("GD_CREDENTIAL_MASTER_KEY", "")
    legacy_key_file = Path(legacy.get("GD_CREDENTIAL_MASTER_KEY_FILE", "")) if legacy.get("GD_CREDENTIAL_MASTER_KEY_FILE") else None
    existing_key = key_path.is_file()
    if not existing_key and legacy_key:
        key_result = _write_once(key_path, legacy_key)
    elif not existing_key and legacy_key_file and legacy_key_file.is_file():
        key_result = _write_once(key_path, legacy_key_file.read_text(encoding="utf-8").strip())
    elif not existing_key and records == 0:
        key_result = _write_once(key_path, Fernet.generate_key().decode("ascii"))
    elif not existing_key:
        raise RuntimeError("VAULT_KEY_MISSING_WITH_CONFIGURED_RECORDS")
    else:
        key_path.chmod(0o600)
        key_result = "PRESERVED"

    try:
        fernet = Fernet(key_path.read_bytes().strip())
        with engine.connect() as conn:
            encrypted = conn.execute(text("SELECT encrypted_value FROM wi_integration_credentials WHERE status='CONFIGURED' AND encrypted_value IS NOT NULL")).scalars() if table_ready else []
            for value in encrypted:
                fernet.decrypt(str(value).encode("ascii"))
    except Exception as exc:
        raise RuntimeError("VAULT_DECRYPT_CHECK_FAILED") from exc

    print(f"DATABASE_CREDENTIAL={database_result}")
    print(f"VAULT_KEY={key_result}")
    print(f"VAULT_RECORDS={records}")
    print("VAULT_DECRYPT=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"BOOTSTRAP_INSTALL=FAIL:{exc}", file=sys.stderr)
        raise SystemExit(1)
