from pathlib import Path
import unittest

from pydantic import ValidationError

from backend.gd_intelligence.permissions import PERMISSIONS, default_permissions
from backend.gd_intelligence.schemas import SiteCreate, normalize_domain


ROOT = Path(__file__).resolve().parents[1]


class PermissionTests(unittest.TestCase):
    def test_super_admin_has_every_permission(self):
        self.assertEqual(default_permissions("SUPER_ADMIN"), set(PERMISSIONS))

    def test_admin_cannot_deploy_without_explicit_grant(self):
        permissions = default_permissions("ADMIN")
        self.assertIn("web_intelligence_configure", permissions)
        self.assertNotIn("web_intelligence_deploy", permissions)
        self.assertNotIn("web_intelligence_rollback", permissions)

    def test_marketing_and_executive_are_isolated(self):
        marketing = default_permissions("MARKETING")
        executive = default_permissions("Ejecutivo de Ventas")
        self.assertIn("web_intelligence_seo", marketing)
        self.assertNotIn("web_intelligence_seo", executive)
        self.assertIn("waba_reply", executive)
        self.assertNotIn("waba_reply", marketing)


class SiteSchemaTests(unittest.TestCase):
    def test_domain_is_normalized(self):
        self.assertEqual(normalize_domain("https://WWW.Example.CL/path"), "www.example.cl")

    def test_site_defaults_and_code_normalization(self):
        site = SiteCreate(code="cam", name="Carritos Camaleón", domain="carritoscamaleon.cl")
        self.assertEqual(site.code, "CAM")
        self.assertEqual(site.currency, "CLP")
        self.assertEqual(site.timezone, "America/Santiago")

    def test_invalid_site_is_rejected(self):
        with self.assertRaises(ValidationError):
            SiteCreate(code="!", name="x", domain="invalid")


class MigrationSafetyTests(unittest.TestCase):
    def test_core_migration_is_additive(self):
        sql = (ROOT / "migrations/2026_08_08_gd_intelligence_core.sql").read_text().upper()
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE"):
            self.assertNotIn(forbidden, sql)
        self.assertIn("LOCK_TIMEOUT", sql)
        self.assertIn("STATEMENT_TIMEOUT", sql)
        self.assertIn("ON CONFLICT", sql)


if __name__ == "__main__":
    unittest.main()
