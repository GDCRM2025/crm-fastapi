from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.core.bootstrap_credentials import BootstrapCredential, BootstrapCredentialError, load_bootstrap_credential
from backend.gd_intelligence.permissions import is_superadmin


class BootstrapCredentialTests(unittest.TestCase):
    def _base_env(self) -> dict[str, str]:
        return {
            "DATABASE_URL": "",
            "GD_DATABASE_URL_FILE": "",
            "GD_CREDENTIAL_MASTER_KEY": "",
            "GD_CREDENTIAL_MASTER_KEY_FILE": "",
            "CREDENTIALS_DIRECTORY": "",
        }

    def test_systemd_credential_has_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "database_url").write_text("postgresql://systemd/db\n", encoding="utf-8")
            env = {**self._base_env(), "CREDENTIALS_DIRECTORY": tmp, "DATABASE_URL": "postgresql://legacy/db"}
            with patch.dict(os.environ, env, clear=False):
                item = load_bootstrap_credential("database_url")
            self.assertEqual(item.source, "SYSTEMD_CREDENTIAL")
            self.assertEqual(item.value, "postgresql://systemd/db")

    def test_secure_file_precedes_legacy_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "database_url")
            path.write_text("postgresql://file/db\n", encoding="utf-8")
            path.chmod(0o600)
            env = {**self._base_env(), "GD_DATABASE_URL_FILE": str(path), "DATABASE_URL": "postgresql://legacy/db"}
            with patch.dict(os.environ, env, clear=False):
                item = load_bootstrap_credential("database_url")
            self.assertEqual(item.source, "SECURE_FILE")
            self.assertEqual(item.value, "postgresql://file/db")

    def test_insecure_fallback_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "credential_vault_key")
            path.write_text("not-a-real-key\n", encoding="utf-8")
            path.chmod(0o644)
            env = {**self._base_env(), "GD_CREDENTIAL_MASTER_KEY_FILE": str(path)}
            with patch.dict(os.environ, env, clear=False):
                with self.assertRaises(BootstrapCredentialError):
                    load_bootstrap_credential("credential_vault_key")
            self.assertTrue(stat.S_IMODE(path.stat().st_mode) & 0o044)

    def test_legacy_environment_remains_compatible(self):
        env = {**self._base_env(), "DATABASE_URL": "postgresql://legacy/db"}
        with patch.dict(os.environ, env, clear=False):
            item = load_bootstrap_credential("database_url")
        self.assertEqual(item.source, "LEGACY_ENV")

    def test_inaccessible_default_candidate_does_not_block_secure_fallback(self):
        env = self._base_env()
        with patch.dict(os.environ, env, clear=False), patch(
            "backend.core.bootstrap_credentials._read_secret_file",
            side_effect=[
                BootstrapCredentialError("root-only parent"),
                BootstrapCredential("safe", "SECURE_FILE"),
            ],
        ):
            item = load_bootstrap_credential("database_url")
        self.assertEqual(item.value, "safe")


class SuperadminVisibilityTests(unittest.TestCase):
    def test_only_superadmin_variants_are_accepted(self):
        self.assertTrue(is_superadmin({"role": "SUPERADMIN"}))
        self.assertTrue(is_superadmin({"rol": "super_admin"}))
        self.assertFalse(is_superadmin({"role": "ADMIN"}))
        self.assertFalse(is_superadmin({"role": "MARKETING"}))

    def test_all_intelligence_routers_have_global_guard(self):
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "backend/routers/gd_intelligence.py",
            "backend/routers/paid_media.py",
            "backend/routers/intelligence_platform.py",
        ):
            source = (root / relative).read_text(encoding="utf-8")
            self.assertIn("dependencies=[Depends(_superadmin_only)]", source)


if __name__ == "__main__":
    unittest.main()
