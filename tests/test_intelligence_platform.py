from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from backend.gd_intelligence.ad_platforms import (
    GoogleAdsReadOnly,
    MetaAdsReadOnly,
    PlatformConfigurationError,
    bounded_history,
    public_assets,
)
from backend.gd_intelligence.intelligence import (
    actionable_alerts,
    executive_readiness,
    safe_ai_context,
    seo_opportunity_score,
)


class RealSourceConnectorTests(unittest.TestCase):
    def test_google_ads_requires_backend_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PlatformConfigurationError):
                GoogleAdsReadOnly()

    def test_meta_ads_requires_backend_token(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(PlatformConfigurationError):
                MetaAdsReadOnly()

    def test_public_assets_never_return_tokens(self):
        safe = public_assets([{"id": "1", "name": "Cuenta", "access_token": "secret", "client_secret": "secret", "business": {"id": "2", "name": "GD", "access_token": "nested-secret"}}])
        self.assertEqual(safe, [{"id": "1", "name": "Cuenta", "business": {"id": "2", "name": "GD"}}])

    def test_meta_pagination_reuses_graph_path_and_opaque_cursor(self):
        adapter = object.__new__(MetaAdsReadOnly)
        adapter._get = MagicMock(side_effect=[
            {"data": [{"id": "1"}], "paging": {"cursors": {"after": "cursor-2"}, "next": "https://unexpected.invalid/?access_token=secret"}},
            {"data": [{"id": "2"}], "paging": {}},
        ])
        self.assertEqual(adapter._all("me/adaccounts", {"fields": "id", "limit": 200}), [{"id": "1"}, {"id": "2"}])
        self.assertEqual(adapter._get.call_args_list[1].args, ("me/adaccounts", {"fields": "id", "limit": 200, "after": "cursor-2"}))

    def test_history_window_is_bounded(self):
        start, end = bounded_history(9999)
        self.assertEqual((end - start).days, 729)

    def test_meta_adapter_is_get_only(self):
        import inspect

        source = inspect.getsource(MetaAdsReadOnly)
        self.assertNotIn("requests.post", source)
        self.assertNotIn("requests.delete", source)

    def test_google_adapter_has_no_mutate_surface(self):
        methods = {name for name in dir(GoogleAdsReadOnly) if not name.startswith("__")}
        self.assertNotIn("mutate", methods)
        self.assertNotIn("update", methods)


class IntelligencePolicyTests(unittest.TestCase):
    def test_seo_without_sources_is_insufficient_not_simulated(self):
        result = seo_opportunity_score()
        self.assertEqual(result["state"], "INSUFFICIENT_DATA")
        self.assertEqual(result["components"], {})

    def test_seo_technical_only_is_explicit(self):
        result = seo_opportunity_score(technical_score=60)
        self.assertEqual(result["state"], "TECHNICAL_ONLY")
        self.assertEqual(result["score"], 40)

    def test_seo_complete_sources_is_ready_and_bounded(self):
        result = seo_opportunity_score(impressions=5000, clicks=100, ctr=.02, position=8, sessions=200, conversions=4, revenue=50000, technical_score=75)
        self.assertEqual(result["state"], "READY")
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 100)

    def test_alerts_are_deduplicated_by_stable_key(self):
        item = {"id": 2, "provider": "GA4", "status": "ERROR"}
        first = actionable_alerts(integrations=[item])[0]
        second = actionable_alerts(integrations=[item])[0]
        self.assertEqual(first["alert_key"], second["alert_key"])
        self.assertTrue(first["action_label"])

    def test_unattended_conversation_alert_has_action(self):
        alert = actionable_alerts(conversations=[{"id": 8, "channel": "WHATSAPP", "unattended_minutes": 45, "action_target": "/inbox/8"}])[0]
        self.assertEqual(alert["category"], "CONVERSATION")
        self.assertEqual(alert["action_label"], "ABRIR CONVERSACIÓN")

    def test_executive_dashboard_waits_for_real_sources(self):
        result = executive_readiness(source_freshness={"crm_sales": True, "paid_media": False, "seo": False})
        self.assertFalse(result["ready"])
        self.assertEqual(result["state"], "INSUFFICIENT_REAL_SOURCES")

    def test_ai_context_is_role_allowlisted_and_read_only(self):
        result = safe_ai_context("Ejecutivo de ventas", ["sales", "paid_media", "integrations"], {"sales": {"count": 1}, "paid_media": {}, "integrations": {}})
        self.assertEqual(result["sources"], ["sales"])
        self.assertFalse(result["arbitrary_sql"])
        self.assertFalse(result["write_capability"])


class PlatformContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]

    def test_platform_migration_is_additive_and_auditable(self):
        sql = (self.root / "migrations/2026_08_10_intelligence_platform.sql").read_text().upper()
        for table in ("WI_AD_SYNC_RUNS", "WI_AD_CLICK_REFS", "WI_SEO_OPPORTUNITIES", "WI_ACTIONABLE_ALERTS"):
            self.assertIn(table, sql)
        self.assertNotIn("DROP TABLE", sql)

    def test_paid_media_ui_exposes_real_intelligence_sections(self):
        html = (self.root / "web/views/paid_media.html").read_text()
        for label in ("Atribución", "Search Terms", "Creative Intelligence", "Change Risk", "SEO Opportunity Engine", "Alertas accionables", "GD AI"):
            self.assertIn(label, html)
        self.assertIn("Sin cambios automáticos", html)

    def test_inbox_ui_is_in_tools_menu(self):
        panel = (self.root / "web/js/panel.js").read_text()
        inbox = (self.root / "web/views/omnichannel_inbox.html").read_text()
        self.assertIn('id: "tool_inbox"', panel)
        self.assertIn("WhatsApp, Instagram, Messenger y Email", inbox)

    def test_router_contract_has_no_provider_mutation(self):
        connector = (self.root / "backend/gd_intelligence/ad_platforms.py").read_text()
        self.assertNotIn("googleAds:mutate", connector)
        self.assertNotIn("requests.delete", connector)
        self.assertIn('mode": "READ_ONLY"', (self.root / "backend/routers/intelligence_platform.py").read_text())

    def test_external_secret_variables_are_blank_in_example(self):
        example = (self.root / ".env.example").read_text().splitlines()
        values = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in example if "=" in line and not line.startswith("#")}
        for key in ("GOOGLE_ADS_REFRESH_TOKEN", "GOOGLE_ADS_DEVELOPER_TOKEN", "META_ACCESS_TOKEN"):
            self.assertEqual(values[key], "")


if __name__ == "__main__":
    unittest.main()
