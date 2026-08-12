from __future__ import annotations

import unittest
from pathlib import Path

from backend.gd_intelligence.integration_state import canonical_integration_state, overview_summary, site_capability_state


ROOT = Path(__file__).resolve().parents[1]


class IntegrationStateParityTests(unittest.TestCase):
    def test_public_detected_without_credential_is_not_not_configured(self):
        item = canonical_integration_state({"provider": "GA4", "status": "DETECTED", "external_id": "G-ABCDEFGHIJ"})
        self.assertEqual(item["status"], "DETECTED")
        self.assertTrue(item["public_detected"])

    def test_ga4_tag_detected_api_missing_is_partial(self):
        item = canonical_integration_state({"provider": "GA4", "status": "DETECTED", "external_id": "G-ABCDEFGHIJ"})
        self.assertEqual(item["installation_status"], "DETECTED")
        self.assertEqual(item["api_status"], "READY_FOR_CREDENTIAL")

    def test_gtm_detected_requires_no_secret(self):
        item = canonical_integration_state({"provider": "GTM", "status": "DETECTED", "external_id": "GTM-ABC123"})
        self.assertEqual(item["status"], "DETECTED")
        self.assertFalse(item["credential_required"])

    def test_gd_tracker_operational_requires_no_secret(self):
        item = canonical_integration_state({"provider": "TRACKING", "status": "DETECTED", "external_id": "gd-tracker.js"}, tracking_events=3)
        self.assertEqual(item["status"], "CONNECTED")
        self.assertTrue(item["data_available"])
        self.assertFalse(item["credential_required"])

    def test_gd_tracker_installed_without_events_is_no_data(self):
        item = canonical_integration_state({"provider": "TRACKING", "status": "DETECTED", "external_id": "gd-tracker.js"})
        self.assertEqual(item["status"], "NO_DATA")

    def test_site_health_findings_do_not_mean_service_unconfigured(self):
        site = {"id": 1, "code": "CAM"}
        state = site_capability_state(site, [], {"http_status": 200, "https_ok": True, "missing_alt_count": 2})
        self.assertEqual(state["health"]["status"], "REQUIRES_ATTENTION")
        self.assertIn("Operativo", state["health"]["detail"])

    def test_google_ads_missing_oauth_is_ready_for_credential(self):
        state = site_capability_state({"id": 1, "code": "CAM"}, [], None)
        self.assertEqual(state["ads"]["status"], "READY_FOR_CREDENTIAL")

    def test_meta_ads_missing_token_is_ready_for_credential(self):
        item = canonical_integration_state({"provider": "META", "status": "READY_FOR_CREDENTIAL", "external_id": None})
        self.assertEqual(item["status"], "READY_FOR_CREDENTIAL")

    def test_overview_counts_public_detections(self):
        items = [canonical_integration_state({"provider": "GTM", "status": "DETECTED", "external_id": "GTM-ABC123"})]
        result = overview_summary(items, [{"enabled": True}], [])
        self.assertEqual(result["installations_detected"], 1)

    def test_overview_does_not_report_zero_when_public_integrations_exist(self):
        items = [canonical_integration_state({"provider": "GA4", "status": "DETECTED", "external_id": "G-ABCDEFGHIJ"})]
        self.assertGreater(overview_summary(items, [], [])["installations_detected"], 0)

    def test_configuration_progress_not_zero_when_tracking_and_tags_exist(self):
        rows = [
            canonical_integration_state({"site_id": 1, "provider": "GA4", "status": "DETECTED", "external_id": "G-ABCDEFGHIJ"}),
            canonical_integration_state({"site_id": 1, "provider": "TRACKING", "status": "DETECTED", "external_id": "gd-tracker.js"}),
        ]
        site_state = site_capability_state({"id": 1, "code": "CAM"}, rows, {"http_status": 200, "https_ok": True})
        result = overview_summary(rows, [{"enabled": True}], [site_state])
        self.assertGreater(result["configuration_progress"], 0)

    def test_last_known_good_survives_temporary_failure(self):
        source = (ROOT / "backend/gd_intelligence/repository.py").read_text(encoding="utf-8")
        self.assertIn("excluded.status='ERROR' AND wi_integrations.last_success_at IS NOT NULL", source)
        self.assertIn("wi_integrations.external_id ELSE excluded.external_id", source)

    def test_secret_values_never_in_overview(self):
        source = (ROOT / "backend/routers/gd_intelligence.py").read_text(encoding="utf-8")
        overview = source[source.index('def overview('):source.index('@router.get("/bootstrap/status")')]
        for forbidden in ("encrypted_value", "use_internal", "access_token", "refresh_token"):
            self.assertNotIn(forbidden, overview)

    def test_meta_public_detection_requires_numeric_pixel_id(self):
        source = (ROOT / "backend/gd_intelligence/integration_inventory.py").read_text(encoding="utf-8")
        self.assertNotIn("meta_seen", source)
        self.assertIn('"DETECTED" if meta_ids else "READY_FOR_CREDENTIAL"', source)


if __name__ == "__main__":
    unittest.main()
