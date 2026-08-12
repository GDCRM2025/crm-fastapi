from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgendaMountingCalendarOnlyTests(unittest.TestCase):
    def test_mounting_is_calendar_only_and_keeps_commercial_event_as_crm_anchor(self):
        source = (ROOT / "backend/routers/leads_agenda.py").read_text(encoding="utf-8")
        self.assertIn('evm["calendar_only"] = True', source)
        self.assertIn('commercial_events = [item for item in eventos if not bool(item.get("calendar_only"))]', source)
        self.assertIn('ev = commercial_events[0]', source)
        self.assertIn('products_text=str(products_text or "").strip()', source)

    def test_calendar_uses_distinct_key_and_persists_primary_commercial_event(self):
        source = (ROOT / "backend/routers/tools.py").read_text(encoding="utf-8")
        self.assertIn("lead_key = f\"{id_lead}:{ev2.get('calendar_key')", source)
        self.assertIn("primary_indexes = [index for index, item in enumerate(to_create) if not bool(item.get(\"calendar_only\"))]", source)
        self.assertIn('"s": primary_event["start"]', source)
        self.assertIn('ok_calendar = ok_primary', source)

    def test_wizard_explains_independent_mounting_schedule_and_same_equipment(self):
        source = (ROOT / "web/views/leads.html").read_text(encoding="utf-8")
        self.assertIn("Montaje operativo · sólo Calendar", source)
        self.assertIn("mismos equipos de la cotización", source)
        self.assertIn("Montaje antes del evento comercial", source)
        self.assertIn("Montaje el mismo día", source)


if __name__ == "__main__":
    unittest.main()
