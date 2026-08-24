from pathlib import Path
import unittest

from backend.core.omnichannel_access import (
    CHANNELS,
    can_access_brand,
    can_delete_leads,
    can_operate_omnichannel,
    visible_brands,
)

ROOT = Path(__file__).resolve().parents[1]


class OmnichannelPermissionTests(unittest.TestCase):
    def test_all_four_channels_declared(self):
        self.assertEqual(set(CHANNELS), {"WHATSAPP", "INSTAGRAM", "MESSENGER", "EMAIL"})

    def test_control_gestion_can_operate_but_never_delete_leads(self):
        user = {"role": "CONTROL DE GESTION", "marcas": []}
        self.assertTrue(can_operate_omnichannel(user))
        self.assertTrue(can_access_brand(user, "EXPRESS"))
        self.assertFalse(can_delete_leads(user))

    def test_sales_is_restricted_to_assigned_brands(self):
        user = {"role": "EJECUTIVO DE VENTAS", "marcas": [1, 4]}
        self.assertTrue(can_access_brand(user, "CAMALEON"))
        self.assertTrue(can_access_brand(user, "DEL_SABOR"))
        self.assertFalse(can_access_brand(user, "GOURMET"))
        self.assertEqual({item["code"] for item in visible_brands(user)}, {"CAMALEON", "DEL_SABOR"})

    def test_non_commercial_role_cannot_operate(self):
        self.assertFalse(can_operate_omnichannel({"role": "OPS"}))


class MetaRouterStaticTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "backend/routers/greeni_instagram.py").read_text(encoding="utf-8")

    def test_both_meta_webhooks_exist(self):
        self.assertIn('/meta/webhook/instagram', self.source)
        self.assertIn('/meta/webhook/messenger', self.source)

    def test_both_meta_channels_have_authenticated_conversation_api(self):
        self.assertIn('/api/omnichannel/meta/{channel}/conversations', self.source)
        self.assertIn('/api/omnichannel/meta/{channel}/{conversation_id}/messages', self.source)
        self.assertIn('/api/omnichannel/meta/{channel}/{conversation_id}/reply', self.source)

    def test_webhooks_require_signature(self):
        self.assertIn('invalid_signature', self.source)
        self.assertIn('hmac.compare_digest', self.source)

    def test_no_lead_delete_operation(self):
        lower = self.source.lower()
        self.assertNotIn('delete from public.leads', lower)
        self.assertNotIn('@router.delete("/api/omnichannel', lower)

    def test_meta_webhook_retry_is_idempotent(self):
        self.assertIn("ON CONFLICT (event_key) DO NOTHING", self.source)
        self.assertIn("if inserted is None:", self.source)


class OmnichannelMigrationTests(unittest.TestCase):
    def test_migration_is_non_destructive_and_idempotent(self):
        sql = (ROOT / "migrations/2026_08_24_meta_omnichannel.sql").read_text(encoding="utf-8").upper()
        self.assertGreaterEqual(sql.count("CREATE TABLE IF NOT EXISTS"), 4)
        self.assertIn("ADD COLUMN IF NOT EXISTS", sql)
        self.assertNotIn("DROP TABLE", sql)
        self.assertNotIn("TRUNCATE", sql)
        self.assertNotIn("DELETE FROM", sql)


if __name__ == "__main__":
    unittest.main()
