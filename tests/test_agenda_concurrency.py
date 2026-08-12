from __future__ import annotations

import unittest
from pathlib import Path

from backend.core.agenda_lock import agenda_lock_key


ROOT = Path(__file__).resolve().parents[1]


class AgendaConcurrencyContractTests(unittest.TestCase):
    def test_lock_is_stable_for_same_lead_and_distinct_between_leads(self):
        self.assertEqual(agenda_lock_key(3903), agenda_lock_key(3903))
        self.assertNotEqual(agenda_lock_key(3903), agenda_lock_key(3904))

    def test_all_calendar_mutations_use_per_lead_lock(self):
        for relative in (
            "backend/routers/tools.py",
            "backend/routers/leads_agenda.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("agenda_lock_key(id_lead)", source)
            self.assertNotIn("pg_try_advisory_lock(26042401)", source)
            self.assertNotIn("pg_advisory_unlock(26042401)", source)

    def test_google_calendar_has_bounded_network_timeout(self):
        source = (ROOT / "backend/routers/tools.py").read_text(encoding="utf-8")
        self.assertIn('GCAL_HTTP_TIMEOUT_SECONDS', source)
        self.assertIn("httplib2.Http(timeout=timeout)", source)
        self.assertIn("cache_discovery=False", source)

    def test_stale_recovery_does_not_kill_active_or_90_second_work(self):
        source = (ROOT / "backend/routers/agenda_confirmacion.py").read_text(encoding="utf-8")
        self.assertIn("_STALE_LOCK_SECONDS = 15 * 60", source)
        self.assertIn('state.startswith("idle")', source)
        self.assertNotIn("age >= 90 or", source)


if __name__ == "__main__":
    unittest.main()
