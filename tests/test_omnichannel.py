from __future__ import annotations

import unittest
from pathlib import Path

from backend.omnichannel.service import (
    CHANNELS,
    can_access_brand,
    capability_payload,
    funnel_metrics,
    visible_items,
)


ROOT = Path(__file__).resolve().parents[1]


class OmnichannelCapabilityTests(unittest.TestCase):
    def test_all_requested_channels_have_explicit_capabilities(self):
        payload = {item["channel"]: item for item in capability_payload()}
        self.assertEqual(set(payload), set(CHANNELS))
        self.assertTrue(payload["WHATSAPP"]["reply"])
        self.assertTrue(payload["EMAIL"]["create_lead"])
        self.assertFalse(payload["INSTAGRAM"]["reply"])
        self.assertFalse(payload["MESSENGER"]["reply"])
        self.assertEqual(payload["INSTAGRAM"]["state"], "RECEIVE_ONLY")

    def test_action_routes_target_existing_tools_shell(self):
        payload = {item["channel"]: item for item in capability_payload()}
        for channel, fragment in {
            "WHATSAPP": "#whatsapp",
            "EMAIL": "#correo",
            "INSTAGRAM": "#instagram",
        }.items():
            route = payload[channel]["action_route"]
            self.assertTrue(route.startswith("/web/views/tools.html"))
            self.assertTrue(route.endswith(fragment))
        self.assertIsNone(payload["MESSENGER"]["action_route"])

    def test_brand_scope_is_fail_closed_for_non_admin(self):
        self.assertFalse(can_access_brand({"role": "EJECUTIVO"}, "CAM"))
        self.assertTrue(can_access_brand({"role": "ADMIN"}, "CAM"))
        user = {"role": "EJECUTIVO", "marcas": [{"brand_code": "CAM"}, {"id_marca": 7}]}
        self.assertTrue(can_access_brand(user, "cam"))
        self.assertTrue(can_access_brand(user, "7"))
        self.assertFalse(can_access_brand(user, "GDG"))

    def test_visible_items_respect_brand_scope(self):
        items = [
            {"channel": "WHATSAPP", "brand_ref": "CAM", "source_ref": "1"},
            {"channel": "EMAIL", "brand_ref": "9", "source_ref": "2"},
        ]
        user = {"role": "EJECUTIVO", "marcas": [{"brand_code": "CAM"}]}
        self.assertEqual([item["source_ref"] for item in visible_items(items, user)], ["1"])


class OmnichannelAnalyticsTests(unittest.TestCase):
    def test_funnel_uses_real_links_and_does_not_infer_sales(self):
        items = [
            {"channel": "WHATSAPP", "source_ref": "10", "lead_id": 101},
            {"channel": "WHATSAPP", "source_ref": "11", "lead_id": 101},
            {"channel": "EMAIL", "source_ref": "20", "lead_id": 202},
        ]
        quotes = [
            {"lead_id": 101, "revenue": 5000, "is_sale": True},
            {"lead_id": 202, "revenue": 9000, "is_sale": False},
        ]
        result = {row["channel"]: row for row in funnel_metrics(items, quotes)}
        self.assertEqual(result["WHATSAPP"]["conversations"], 2)
        self.assertEqual(result["WHATSAPP"]["leads"], 1)
        self.assertEqual(result["WHATSAPP"]["sales"], 1)
        self.assertEqual(result["WHATSAPP"]["revenue"], 5000)
        self.assertEqual(result["EMAIL"]["quotes"], 1)
        self.assertEqual(result["EMAIL"]["sales"], 0)
        self.assertEqual(result["INSTAGRAM"]["data_state"], "NO_DATA")

    def test_no_link_means_no_attributed_funnel(self):
        result = {row["channel"]: row for row in funnel_metrics(
            [{"channel": "MESSENGER", "source_ref": "30", "lead_id": None}],
            [{"lead_id": 303, "revenue": 1000, "is_sale": True}],
        )}
        self.assertEqual(result["MESSENGER"]["leads"], 0)
        self.assertEqual(result["MESSENGER"]["revenue"], 0)


class OmnichannelStorageContractTests(unittest.TestCase):
    def test_contextual_help_uses_the_real_menu_screen_id(self):
        migration = (ROOT / "migrations/2026_08_11_omnichannel_inbox.sql").read_text(encoding="utf-8")
        self.assertIn("'tool_inbox'", migration)
        self.assertNotIn("'tool_omnichannel'", migration)

    def test_inbox_opens_channels_with_authorized_menu_ids(self):
        source = (ROOT / "web/views/omnichannel_inbox.html").read_text(encoding="utf-8")
        for menu_id in ("tool_wapp", "tool_gmail", "tool_ig"):
            self.assertIn(menu_id, source)
        self.assertNotIn("id:'omnichannel_action'", source)

    def test_migration_stores_references_not_message_content(self):
        sql = (ROOT / "migrations/2026_08_11_omnichannel_inbox.sql").read_text().lower()
        self.assertIn("source_ref text not null", sql)
        for forbidden in ("message_body", "body text", "payload json", "attachment"):
            self.assertNotIn(forbidden, sql)

    def test_router_is_authenticated_and_has_no_channel_send_endpoint(self):
        source = (ROOT / "backend/routers/omnichannel.py").read_text()
        self.assertIn("dependencies=[Depends(get_current_user)]", source)
        self.assertNotIn('@router.post("/send', source)
        self.assertIn('@router.patch("/items/{channel}/{source_ref}")', source)

    def test_source_adapters_are_read_only(self):
        source = (ROOT / "backend/omnichannel/service.py").read_text().lower()
        self.assertNotIn("insert into public.whatsapp_messages", source)
        self.assertNotIn("insert into public.gia_email_messages", source)
        self.assertNotIn("insert into public.gia_ig_events", source)


if __name__ == "__main__":
    unittest.main()
