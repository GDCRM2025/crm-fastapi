from pathlib import Path
import unittest
from unittest.mock import patch

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
from backend.gd_intelligence.rbac_admin import ROLE_KEYS
from backend.gd_intelligence.site_health import PageSignals, _request


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

    def test_site_health_migration_is_additive(self):
        sql = (ROOT / "migrations/2026_08_08_site_health.sql").read_text().upper()
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE"):
            self.assertNotIn(forbidden, sql)
        self.assertIn("LOCK_TIMEOUT", sql)
        self.assertIn("WI_SITE_HEALTH_RUNS", sql)


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


class DashboardContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "web/views/gd_intelligence.html").read_text()
        cls.panel = (ROOT / "web/js/panel.js").read_text()

    def test_dashboard_uses_authenticated_first_party_endpoints(self):
        self.assertIn("/api/gd-intelligence/permissions/me", self.html)
        self.assertIn("/api/gd-intelligence/overview", self.html)
        self.assertIn("authHeaders", self.html)

    def test_dashboard_honors_configure_and_campaign_permissions(self):
        self.assertIn("web_intelligence_configure", self.html)
        self.assertIn("web_intelligence_campaigns", self.html)

    def test_dashboard_is_reachable_from_panel_menu(self):
        self.assertIn('id: "gd_intelligence"', self.panel)
        self.assertIn('/web/views/gd_intelligence.html', self.panel)
        self.assertIn('gdPermission: "web_intelligence_view"', self.panel)
        self.assertIn('gdPermission: "web_intelligence_campaigns"', self.panel)

    def test_dashboard_has_friendly_backend_error(self):
        self.assertIn("Backend GD Intelligence no disponible", self.html)

    def test_frontend_api_base_is_path_derived(self):
        login = (ROOT / "web/login.html").read_text()
        self.assertIn("location.pathname.startsWith('/crm/')", login)
        self.assertNotIn("location.hostname", self.html)
        self.assertNotIn("location.hostname", self.panel)

    def test_gd_navigation_bypasses_only_legacy_menu_matrix(self):
        self.assertIn("!CURRENT_ALLOWED.has(it.id) && !isGdMenuAllowed(it)", self.panel)

    def test_admin_can_really_dismiss_rrhh_gate_for_the_day(self):
        self.assertIn("onDismiss: dismiss", self.panel)
        self.assertIn("gd_sgjo_in_dismissed_", self.panel)
        self.assertIn("clearInterval(_sgjoGateTimer)", self.panel)

    def test_optional_utm_site_filter_has_explicit_postgres_type(self):
        repository = (ROOT / "backend/gd_intelligence/repository.py").read_text()
        self.assertIn("CAST(:site_id AS bigint) IS NULL", repository)

    def test_dashboard_exposes_site_health_and_rbac_tabs(self):
        self.assertIn('data-tab="health"', self.html)
        self.assertIn('data-tab="permissions"', self.html)
        self.assertIn("/api/gd-intelligence/web/site-health/latest", self.html)
        self.assertIn("/api/gd-intelligence/permissions/roles", self.html)


class RbacAdminTests(unittest.TestCase):
    def test_supported_roles_are_explicit(self):
        self.assertEqual(ROLE_KEYS, ("EXECUTIVO", "MARKETING", "ADMIN", "SUPER_ADMIN"))


class SiteHealthParserTests(unittest.TestCase):
    def test_html_signals_are_separated(self):
        parser = PageSignals()
        parser.feed(
            '<html itemscope itemtype="https://schema.org/WebPage"><head>'
            '<title>GD</title><meta name="description" content="Servicios">'
            '<meta name="viewport" content="width=device-width">'
            '<link rel="canonical" href="https://example.cl/"></head>'
            '<body><h1>Hola</h1><img src="/x.png"><a href="/contacto">Contacto</a></body></html>'
        )
        self.assertTrue(parser.title and parser.meta_description and parser.viewport)
        self.assertTrue(parser.canonical and parser.h1 and parser.schema_org)
        self.assertEqual(parser.missing_alt_count, 1)

    def test_redirect_to_private_network_is_rejected(self):
        class RedirectResponse:
            status_code = 302
            headers = {"location": "http://127.0.0.1/private"}

            def close(self):
                return None

        class Session:
            calls = 0

            def request(self, *args, **kwargs):
                self.calls += 1
                return RedirectResponse()

        def dns(hostname, *_args, **_kwargs):
            address = "93.184.216.34" if hostname == "example.com" else "127.0.0.1"
            return [(2, 1, 6, "", (address, 443))]

        session = Session()
        with patch("backend.gd_intelligence.site_health.socket.getaddrinfo", side_effect=dns):
            with self.assertRaises(ValueError):
                _request(session, "GET", "https://example.com/", timeout=1)
        self.assertEqual(session.calls, 1)


if __name__ == "__main__":
    unittest.main()
