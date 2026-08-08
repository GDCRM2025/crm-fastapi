from pathlib import Path
import unittest

from pydantic import ValidationError

from backend.gd_intelligence.permissions import PERMISSIONS, default_permissions
from backend.gd_intelligence.schemas import SiteCreate, normalize_domain
from backend.gd_intelligence.connectors import (
    parse_crux_metrics,
    parse_ga4_report,
    parse_pagespeed,
    parse_search_console_rows,
)
from backend.gd_intelligence.utm import build_utm_url, campaign_identifier


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

    def test_source_migration_is_additive(self):
        sql = (ROOT / "migrations/2026_08_08_web_intelligence_sources.sql").read_text().upper()
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE"):
            self.assertNotIn(forbidden, sql)


class ConnectorParserTests(unittest.TestCase):
    def test_ga4_rows(self):
        payload = {
            "dimensionHeaders": [{"name": "date"}, {"name": "sessionSource"}],
            "metricHeaders": [{"name": "sessions"}],
            "rows": [{"dimensionValues": [{"value": "20260808"}, {"value": "google"}], "metricValues": [{"value": "12"}]}],
        }
        self.assertEqual(parse_ga4_report(payload)[0]["sessions"], 12.0)

    def test_search_console_rows(self):
        rows = parse_search_console_rows(
            {"rows": [{"keys": ["2026-08-08", "carritos"], "clicks": 2, "impressions": 20, "ctr": 0.1, "position": 8.2}]},
            ("date", "query"),
        )
        self.assertEqual(rows[0]["query"], "carritos")
        self.assertEqual(rows[0]["impressions"], 20)

    def test_pagespeed_keeps_lab_and_field_separate(self):
        parsed = parse_pagespeed(
            {
                "lighthouseResult": {
                    "categories": {"performance": {"score": 0.91}},
                    "audits": {"largest-contentful-paint": {"numericValue": 2100}},
                },
                "loadingExperience": {"metrics": {"INTERACTION_TO_NEXT_PAINT": {"percentile": 180, "category": "FAST"}}},
            },
            "mobile",
        )
        self.assertEqual(parsed["lab"]["lcp_ms"], 2100.0)
        self.assertEqual(parsed["field"]["inp_ms"], 180.0)
        self.assertEqual(parsed["strategy"], "MOBILE")

    def test_crux_missing_data_is_not_fabricated(self):
        self.assertEqual(parse_crux_metrics({}), {})


class UTMTests(unittest.TestCase):
    def test_build_url_preserves_existing_params_and_fragment(self):
        url = build_utm_url(
            "https://example.cl/landing?product=1#quote",
            utm_source="google",
            utm_medium="cpc",
            utm_campaign="invierno",
        )
        self.assertIn("product=1", url)
        self.assertIn("utm_source=google", url)
        self.assertTrue(url.endswith("#quote"))

    def test_campaign_identifier(self):
        from datetime import datetime
        self.assertEqual(campaign_identifier("cam", 841, datetime(2026, 8, 8)), "GD-CAM-MKT-2026-00841")


if __name__ == "__main__":
    unittest.main()
