from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.gd_intelligence.tracking import safe_public_url
from backend.jobs.tracking_edge_pull import canonical_signature
from backend.routers import tracking_collector as collector


ROOT = Path(__file__).resolve().parents[1]


class TrackingCollectorSecurityTests(unittest.TestCase):
    def setUp(self):
        collector._rate_hits.clear()

    def test_payload_allowlist_accepts_minimal_event(self):
        collector._allowlist({
            "site_code": "CAM",
            "session_id": "00000000-0000-4000-8000-000000000001",
            "event_id": "00000000-0000-4000-8000-000000000002",
            "event_type": "page_view",
            "page_url": "https://carritoscamaleon.cl/",
            "metadata": {"language": "es-CL"},
        }, collector.EVENT_KEYS)

    def test_sensitive_fields_are_rejected_recursively(self):
        with self.assertRaises(HTTPException):
            collector._allowlist({"site_code": "CAM", "metadata": {"access_token": "never"}}, collector.EVENT_KEYS)

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(HTTPException):
            collector._allowlist({"site_code": "CAM", "form_body": "never"}, collector.EVENT_KEYS)

    def test_marketing_url_is_minimized(self):
        value = safe_public_url("https://Example.cl/path?utm_source=google&email=secret%40example.cl&gclid=abc#private")
        self.assertEqual(value, "https://example.cl/path?utm_source=google&gclid=abc")
        self.assertNotIn("email", value)
        self.assertNotIn("private", value)

    def test_page_host_must_match_origin(self):
        with self.assertRaises(HTTPException):
            collector._require_page_host({"page_url": "https://evil.example/"}, "carritoscamaleon.cl", "page_url")

    def test_http_origins_are_rejected(self):
        with self.assertRaises(HTTPException):
            collector._origin_host("http://carritoscamaleon.cl")

    def test_rate_limit_rejects_excess(self):
        request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.5"))
        with patch.object(collector, "RATE_LIMIT", 2):
            collector._rate_limit(request, "CAM")
            collector._rate_limit(request, "CAM")
            with self.assertRaises(HTTPException) as raised:
                collector._rate_limit(request, "CAM")
        self.assertEqual(raised.exception.status_code, 429)

    def test_four_site_codes_are_routed_fail_closed(self):
        domains = {
            "CAM": "carritoscamaleon.cl",
            "EXP": "carritosexpress.cl",
            "GOU": "carritosgourmet.cl",
            "DEL": "carritosdelsabor.cl",
        }
        for index, (code, domain) in enumerate(domains.items(), 1):
            result = MagicMock()
            result.mappings.return_value.one_or_none.return_value = {"id": index, "code": code, "domain": domain}
            conn = MagicMock(); conn.execute.return_value = result
            self.assertEqual(collector._site(conn, code, domain)["id"], index)
            with self.assertRaises(HTTPException):
                collector._site(conn, code, "wrong.example")

    def test_collector_has_no_auth_dependency_and_metrics_are_protected(self):
        public_source = (ROOT / "backend/routers/tracking_collector.py").read_text(encoding="utf-8")
        admin_source = (ROOT / "backend/routers/gd_intelligence.py").read_text(encoding="utf-8")
        self.assertNotIn("get_current_user", public_source)
        self.assertIn('@router.get("/tracking/metrics")', admin_source)
        self.assertIn('"web_intelligence_view"', admin_source)

    def test_tracker_uses_public_collector_and_session_start(self):
        source = (ROOT / "web/js/gd-tracker.js").read_text(encoding="utf-8")
        self.assertIn('send("/v1/session"', source)
        self.assertIn('event_type: "session_start"', source)
        self.assertNotIn('/collect/v1/session', source)
        self.assertNotIn('/api/gd-intelligence/tracking/session', source)
        self.assertIn("window.__GD_TRACKER_LOADED__", source)
        self.assertIn("window.GD_TRACKER_API_BASE", source)

    def test_discovery_requires_actual_tracker_script(self):
        source = (ROOT / "backend/gd_intelligence/integration_inventory.py").read_text(encoding="utf-8")
        self.assertIn("<script[^>]+", source)
        self.assertNotIn('gd[-_]tracker|first[ -]?party', source)

    def test_migration_is_additive_and_metric_only(self):
        sql = (ROOT / "migrations/2026_08_12_tracking_collector.sql").read_text(encoding="utf-8").upper()
        self.assertIn("CREATE TABLE IF NOT EXISTS", sql)
        self.assertIn("WI_TRACKING_COLLECTOR_METRICS", sql)
        self.assertNotIn("DROP TABLE", sql)
        self.assertNotIn("ALTER TABLE PUBLIC.LEADS", sql)

    def test_edge_exposes_only_tracking_and_signed_delivery_routes(self):
        source = (ROOT / "edge/tracking-relay/public/index.php").read_text(encoding="utf-8")
        htaccess = (ROOT / "edge/tracking-relay/public/.htaccess").read_text(encoding="utf-8")
        for route in ("/v1/session", "/v1/event", "/internal/v1/pull", "/internal/v1/ack"):
            self.assertIn(route, source)
        for forbidden in ("/crm", "/admin", "/docs", "/openapi.json", "postgresql://", "192.168.100.51", "127.0.0.1:8000"):
            self.assertNotIn(forbidden, source)
        self.assertIn("Options -Indexes", htaccess)
        self.assertIn("+MultiViews", htaccess)
        for relative in ("v1/session.php", "v1/event.php", "internal/v1/pull.php", "internal/v1/ack.php"):
            self.assertTrue((ROOT / "edge/tracking-relay/public" / relative).is_file())

    def test_edge_queue_is_durable_leased_and_never_deleted_before_ack(self):
        source = (ROOT / "edge/tracking-relay/public/index.php").read_text(encoding="utf-8")
        self.assertIn("PRAGMA journal_mode=WAL", source)
        self.assertIn("BEGIN IMMEDIATE", source)
        self.assertIn("lease_until", source)
        self.assertIn("acked_at IS NULL", source)
        self.assertIn("SET acked_at=?", source)
        self.assertIn("UNIQUE(event_type,event_id)", source)

    def test_edge_hmac_canonical_vector_is_stable(self):
        result = canonical_signature(
            "a" * 32,
            "1700000000",
            "b" * 32,
            "POST",
            "/internal/v1/pull",
            b'{"limit":500}',
        )
        self.assertEqual(result, "6070d36f0ce5220e739da701efa1a520641ce14becdd8190deb12795682361fb")

    def test_edge_rejects_recursive_pii_and_has_replay_protection(self):
        source = (ROOT / "edge/tracking-relay/public/index.php").read_text(encoding="utf-8")
        self.assertIn("reject_sensitive($item, $depth + 1)", source)
        self.assertIn("hash_equals($expected, $signature)", source)
        self.assertIn("INSERT INTO nonces", source)
        self.assertIn("replayed_request", source)
        for name in ("pass", "authorization", "cookie", "email", "telefono", "message"):
            self.assertRegex(source.lower(), name)

    def test_worker_is_one_shot_locked_and_revalidates_payloads(self):
        source = (ROOT / "backend/jobs/tracking_edge_pull.py").read_text(encoding="utf-8")
        self.assertIn("pg_try_advisory_xact_lock", source)
        self.assertIn("validation._allowlist", source)
        self.assertIn("SESSION_SITE_MISMATCH", source)
        self.assertNotIn("while True", source)
        self.assertIn("ACK parcial", source)

    def test_edge_migration_is_additive(self):
        sql = (ROOT / "migrations/2026_08_13_tracking_edge.sql").read_text(encoding="utf-8").upper()
        self.assertIn("WI_TRACKING_EDGE_PULL_RUNS", sql)
        self.assertIn("WI_TRACKING_EDGE_REJECTIONS", sql)
        self.assertNotIn("DROP TABLE", sql)
        self.assertNotIn("ALTER TABLE PUBLIC.LEADS", sql)


if __name__ == "__main__":
    unittest.main()
