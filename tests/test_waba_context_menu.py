from __future__ import annotations

import unittest
from pathlib import Path


class WabaContextMenuContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "web/js/greenie_context_menu_v1.js").read_text(encoding="utf-8")

    def test_active_greenie_view_loads_context_menu(self):
        page = (self.root / "web/views/tools_whatsapp_greenie.html").read_text(encoding="utf-8")
        self.assertIn("greenie_context_menu_v1.js", page)

    def test_desktop_and_touch_activation(self):
        self.assertIn("'contextmenu'", self.source)
        self.assertIn("pointerType==='mouse'", self.source)
        self.assertIn("550", self.source)

    def test_existing_message_actions_are_reused(self):
        self.assertIn("[data-reply]", self.source)
        self.assertIn("/send-reaction", self.source)
        self.assertIn("navigator.clipboard.writeText", self.source)

    def test_commercial_actions_are_real(self):
        for marker in (
            "commercial-360",
            "assign-executive",
            ".greenieClientCreate",
            "#createQuoteBtn",
            "#followBtn",
            "#openLeadBtn",
            "#closeBtn",
        ):
            self.assertIn(marker, self.source)

    def test_forward_is_review_before_send_and_delete_is_not_faked(self):
        self.assertIn("Reenvío preparado", self.source)
        self.assertIn("no envía", self.source)
        self.assertIn("Meta no permite borrar remotamente", self.source)
        self.assertNotIn("/delete-message", self.source)


if __name__ == "__main__":
    unittest.main()
