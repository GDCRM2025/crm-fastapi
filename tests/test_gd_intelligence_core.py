from pathlib import Path
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.gd_intelligence.permissions import PERMISSIONS, default_permissions
from backend.gd_intelligence.schemas import IntegrationPublicUpdate, SiteCreate, normalize_domain
from backend.gd_intelligence.connectors import (
    parse_crux_metrics,
    parse_ga4_report,
    parse_pagespeed,
    parse_search_console_rows,
)
from backend.gd_intelligence.utm import build_utm_url, campaign_identifier
from backend.gd_intelligence.rbac_admin import ROLE_KEYS
from backend.gd_intelligence.site_health import PageSignals, _request
from backend.gd_intelligence.integration_inventory import GA4_RE, GTM_RE, _ids
from backend.gd_intelligence.integration_center import actionable_integration
from backend.gd_intelligence.tracking import attribution_touches, parse_utm, public_event_metadata, waba_confidence
from backend.gd_intelligence.paid_media import business_metrics, change_risk, parse_google_ads_row, parse_meta_ads_row
from backend.gd_intelligence.credential_vault import CredentialValidationError, CredentialVault, redact
from cryptography.fernet import Fernet


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

    def test_integration_inventory_migration_is_additive(self):
        sql = (ROOT / "migrations/2026_08_08_integration_inventory.sql").read_text().upper()
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "DROP COLUMN"):
            self.assertNotIn(forbidden, sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS LAST_VERIFIED_AT", sql)

    def test_tracking_migration_keeps_core_leads_intact(self):
        sql = (ROOT / "migrations/2026_08_09_tracking_attribution.sql").read_text().upper()
        self.assertIn("WI_SESSIONS", sql)
        self.assertIn("WI_EVENTS", sql)
        self.assertIn("WI_LEAD_ATTRIBUTION", sql)
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE PUBLIC.LEADS"):
            self.assertNotIn(forbidden, sql)

    def test_paid_media_help_migration_is_additive_and_secret_free(self):
        sql = (ROOT / "migrations/2026_08_09_paid_media_help.sql").read_text().upper()
        for forbidden in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER TABLE PUBLIC.LEADS"):
            self.assertNotIn(forbidden, sql)
        self.assertIn("WI_AD_METRICS_DAILY", sql)
        self.assertIn("HELP_ARTICLES", sql)
        self.assertNotIn("PASSWORD", sql)


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

    def test_paid_media_platform_parsers_normalize_without_credentials(self):
        google = parse_google_ads_row({"segments": {"date": "2026-08-09"}, "campaign": {"id": 12}, "metrics": {"costMicros": 2500000, "clicks": 4}})
        meta = parse_meta_ads_row({"date_start": "2026-08-09", "campaign_id": "m1", "spend": "4.5", "actions": [{"action_type": "lead", "value": "2"}]})
        self.assertEqual(google["spend"], 2.5)
        self.assertEqual(google["campaign_id"], "12")
        self.assertEqual(meta["conversions"], 2.0)


class PaidMediaTests(unittest.TestCase):
    def test_crm_metrics_are_separate_and_zero_safe(self):
        metrics = business_metrics(spend=100, impressions=1000, clicks=20, leads=5, quotes=2, sales=1, revenue=400)
        self.assertEqual(metrics["ctr"], .02)
        self.assertEqual(metrics["cpl_crm"], 20)
        self.assertEqual(metrics["roas_crm"], 4)
        self.assertIsNone(business_metrics(spend=0, impressions=0, clicks=0, leads=0, quotes=0, sales=0, revenue=0)["cpc"])

    def test_change_risk_respects_cooldown_learning_and_low_data(self):
        self.assertEqual(change_risk(hours_since_change=12, conversions_30d=100, learning=False)["state"], "RECENT_CHANGE")
        self.assertEqual(change_risk(hours_since_change=100, conversions_30d=100, learning=True)["state"], "LEARNING")
        self.assertEqual(change_risk(hours_since_change=100, conversions_30d=3, learning=False)["recommendation"], "INSUFFICIENT_EVIDENCE")

    def test_marketing_can_view_but_not_manage_paid_media(self):
        marketing = default_permissions("MARKETING")
        self.assertIn("paid_media_view", marketing)
        self.assertNotIn("paid_media_manage", marketing)


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

    def test_tracking_utm_parser(self):
        parsed = parse_utm("https://example.cl/?utm_source=google&utm_medium=cpc&utm_campaign=invierno")
        self.assertEqual(parsed["utm_source"], "google")
        self.assertEqual(parsed["utm_campaign"], "invierno")


class AttributionTests(unittest.TestCase):
    def test_first_touch_is_persistent_and_last_touch_updates(self):
        first, last = attribution_touches(None, {"source": "google"})
        first2, last2 = attribution_touches(first, {"source": "instagram"})
        self.assertEqual(first2["source"], "google")
        self.assertEqual(last2["source"], "instagram")

    def test_waba_confidence_never_invents_a_match(self):
        self.assertEqual(waba_confidence(session_id_match=True, click_id_match=False, phone_match=False), ("WABA_SESSION_ID", 0.95))
        self.assertEqual(waba_confidence(session_id_match=False, click_id_match=False, phone_match=True), ("WABA_PHONE_TIME_WINDOW", 0.65))
        self.assertIsNone(waba_confidence(session_id_match=False, click_id_match=False, phone_match=False))

    def test_waba_linking_requires_explicit_click_reference(self):
        tracker = (ROOT / "web/js/gd-tracker.js").read_text()
        waba = (ROOT / "backend/routers/whatsapp_commercial.py").read_text()
        self.assertIn("Ref GD:", tracker)
        self.assertIn("WABA_CLICK_ID", waba)
        self.assertIn("never infer attribution from phone alone", waba)

    def test_event_metadata_removes_secret_and_pii_keys(self):
        safe = public_event_metadata({"click_id": "public", "token": "hidden", "email": "hidden"})
        self.assertEqual(safe, {"click_id": "public"})


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
        self.assertIn("No pudimos comunicarnos con el CRM", self.html)

    def test_frontend_api_base_is_path_derived(self):
        login = (ROOT / "web/login.html").read_text()
        self.assertIn("location.pathname.startsWith('/crm/')", login)
        self.assertNotIn("location.hostname", self.html)
        self.assertNotIn("location.hostname", self.panel)

    def test_login_supports_explicit_clean_session(self):
        login = (ROOT / "web/login.html").read_text()
        self.assertIn('get("reset_session") === "1"', login)
        self.assertIn('localStorage.removeItem("token")', login)

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

    def test_dashboard_exposes_public_integration_inventory(self):
        self.assertIn("Dominios administrados y sus identificadores públicos", self.html)
        self.assertIn("/api/gd-intelligence/web/integrations", self.html)
        self.assertIn("nunca pueden volver a mostrarse", self.html)

    def test_integration_center_has_actionable_statuses(self):
        self.assertIn('data-tab="integrations"', self.html)
        self.assertIn("Detectado", self.html)
        self.assertIn("data-action", self.html)
        for label in ("Conectado", "Falta configurar", "Requiere atención", "Error de conexión", "Deshabilitado"):
            self.assertIn(label, self.html)

    def test_tracker_persists_visitor_and_session_without_hardcoded_host(self):
        tracker = (ROOT / "web/js/gd-tracker.js").read_text()
        self.assertIn('localStorage, "gd_visitor_id"', tracker)
        self.assertIn('sessionStorage, "gd_session_id"', tracker)
        self.assertNotIn("127.0.0.1", tracker)
        self.assertNotIn("localhost", tracker)

    def test_manual_lead_ui_has_commercial_sources_not_utm_fields(self):
        leads = (ROOT / "web/js/leads_app.js").read_text()
        for label in ("Web", "WhatsApp", "Teléfono", "Instagram", "Facebook", "Google", "Referido", "Evento", "Otro"):
            self.assertIn(label, leads)
        create_section = leads[leads.index("async function createLead"):]
        self.assertNotIn('id="utm_', create_section)

    def test_attribution_edit_is_rbac_guarded(self):
        leads_router = (ROOT / "backend/routers/leads.py").read_text()
        self.assertIn("Sólo roles autorizados pueden editar atribución", leads_router)
        self.assertIn('action="lead.attribution.update"', leads_router)

    def test_paid_media_and_context_help_are_reachable_without_hardcoded_host(self):
        paid = (ROOT / "web/views/paid_media.html").read_text()
        help_html = (ROOT / "web/views/ayuda.html").read_text()
        self.assertIn('id: "gdi_paid_media"', self.panel)
        self.assertIn("/api/gd-intelligence/paid-media/summary", paid)
        self.assertIn("READ · ANALYZE · RECOMMEND", paid)
        self.assertNotIn("127.0.0.1", paid)
        self.assertNotIn("localhost", paid)
        self.assertIn("/api/help/context/", help_html)
        self.assertIn("screen_id", self.panel)

    def test_generated_catalog_has_real_coverage(self):
        catalog = __import__("json").loads((ROOT / "docs/user-guide/source/screen_catalog.json").read_text())
        self.assertGreaterEqual(len(catalog["screens"]), 60)
        self.assertGreaterEqual(len(catalog["routes"]), 300)
        self.assertTrue(any(x["id"] == "gdi_paid_media" for x in catalog["screens"]))


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


class IntegrationInventoryTests(unittest.TestCase):
    def test_public_tag_ids_are_detected_without_false_assistant_match(self):
        content = "GTM-52G8CVS G-4ZMLEC3SWJ G-ASSISTANT"
        self.assertEqual(_ids(GTM_RE, content), ["GTM-52G8CVS"])
        self.assertEqual(_ids(GA4_RE, content), ["G-4ZMLEC3SWJ"])

    def test_secret_like_values_are_rejected(self):
        with self.assertRaises(ValidationError):
            IntegrationPublicUpdate(external_id="api_key=do-not-store", enabled=True)

    def test_integration_center_response_contains_no_secret_value(self):
        with patch.dict("os.environ", {"PAGESPEED_API_KEY": "never-return-this"}, clear=False):
            item = actionable_integration({"provider": "PAGESPEED", "status": "WARNING", "external_id": "https://example.cl/"})
        self.assertNotIn("never-return-this", str(item))
        self.assertEqual(item["action"], "VERIFICAR CONEXIÓN")


class CredentialVaultSecurityTests(unittest.TestCase):
    def test_encryption_at_rest_and_internal_use_only(self):
        value = "test-api-key-123456789"
        vault = CredentialVault(Fernet.generate_key(), validator=lambda *_a, **_k: {"valid": True})
        encrypted = vault.encrypt(value)
        self.assertNotIn(value, encrypted)
        self.assertEqual(vault._decrypt_internal(encrypted), value)
        self.assertFalse(hasattr(vault, "get_secret_for_frontend"))

    def test_invalid_secret_is_rejected_before_storage(self):
        def reject(*_args, **_kwargs):
            raise CredentialValidationError("AUTH_REJECTED", "Credencial rechazada")
        vault = CredentialVault(Fernet.generate_key(), validator=reject)
        with self.assertRaises(CredentialValidationError):
            vault.validate("PAGESPEED", "invalid-key-value", domain="example.cl")

    def test_successful_validation_is_supported(self):
        vault = CredentialVault(Fernet.generate_key(), validator=lambda provider, _secret, **_k: {"valid": True, "provider": provider})
        self.assertTrue(vault.validate("PAGESPEED", "valid-key-value", domain="example.cl")["valid"])

    def test_redaction_removes_secret_values_from_nested_metadata(self):
        safe = redact({"provider": "PAGESPEED", "api_key": "hidden", "nested": {"refresh_token": "hidden", "result": "ok"}})
        self.assertEqual(safe, {"provider": "PAGESPEED", "nested": {"result": "ok"}})

    def test_no_credential_read_endpoint_exists(self):
        router = (ROOT / "backend/routers/gd_intelligence.py").read_text()
        self.assertNotIn('@router.get("/web/integrations/{integration_id}/credential")', router)
        self.assertIn('@router.put("/web/integrations/{integration_id}/credential")', router)
        self.assertIn('@router.delete("/web/integrations/{integration_id}/credential")', router)

    def test_api_list_never_selects_ciphertext(self):
        repository = (ROOT / "backend/gd_intelligence/repository.py").read_text()
        list_query = repository[repository.index("def list_integrations"):repository.index("def update_public_integration")]
        self.assertNotIn("encrypted_value", list_query)
        self.assertIn("credential_suffix", list_query)

    def test_replacement_form_is_password_only_and_blank(self):
        html = (ROOT / "web/views/gd_intelligence.html").read_text()
        self.assertIn('name="credential" type="password"', html)
        self.assertIn('name="confirmation" type="password"', html)
        self.assertIn("f.reset();f.integration_id.value", html)
        self.assertNotRegex(html, r'name="credential"[^>]*\svalue=')
        self.assertNotIn("Mostrar credencial", html)
        self.assertNotIn("Copiar credencial", html)

    def test_vault_migration_supports_replace_revoke_and_safe_audit(self):
        sql = (ROOT / "migrations/2026_08_10_credential_vault.sql").read_text().upper()
        self.assertIn("ENCRYPTED_VALUE", sql)
        self.assertIn("CREDENTIAL_REPLACED", sql)
        self.assertIn("INTEGRATION_DISCONNECTED", sql)
        self.assertIn("status='REVOKED'", (ROOT / "backend/gd_intelligence/credential_vault.py").read_text())
        self.assertNotIn("CREDENTIAL PAYLOAD", sql)

    def test_backend_rbac_guards_configure_and_disconnect(self):
        router = (ROOT / "backend/routers/gd_intelligence.py").read_text()
        write = router[router.index("async def integration_credential_write"):router.index('@router.post("/web/integrations/{integration_id}/verify")')]
        revoke = router[router.index("def integration_credential_revoke"):router.index('@router.get("/web/integrations/google/properties")')]
        self.assertIn('"web_intelligence_configure"', write)
        self.assertIn('"system_integrations_manage"', revoke)


if __name__ == "__main__":
    unittest.main()
