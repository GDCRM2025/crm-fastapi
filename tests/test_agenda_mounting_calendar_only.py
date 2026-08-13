from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend.routers import leads_agenda


ROOT = Path(__file__).resolve().parents[1]


class AgendaMountingCalendarOnlyTests(unittest.TestCase):
    def test_incomplete_mounting_draft_is_allowed_only_for_preview_guidance(self):
        item, missing = leads_agenda._mounting_event_draft({"day": "2026-08-14", "start_time": "09:00"})
        self.assertIsNone(item)
        self.assertEqual(missing, ["fin", "comuna", "dirección"])
        source = (ROOT / "backend/routers/leads_agenda.py").read_text(encoding="utf-8")
        self.assertIn("if montaje_pending and not dry_run", source)
        self.assertIn('"montaje_pending": montaje_pending', source)

    def test_complete_mounting_draft_preserves_independent_schedule(self):
        item, missing = leads_agenda._mounting_event_draft({
            "day": "2026-08-14", "start_time": "09:00", "end_time": "10:00",
            "comuna": "Las Condes", "direccion": "Av. Apoquindo 2730",
        })
        self.assertEqual(missing, [])
        self.assertEqual(str(item["day"]), "2026-08-14")
        self.assertEqual(item["start_time"], "09:00")

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
        wizard_source = (ROOT / "web/js/agenda_wizard_v4.js").read_text(encoding="utf-8")
        self.assertIn("¿Requiere montaje operativo separado?", wizard_source)
        self.assertIn('id="gdW4Prior"', wizard_source)
        self.assertIn('priorLegacy.checked = prior.value === "yes"', wizard_source)
        mounting_tag = source.split('id="ag_montaje_event_box"', 1)[1].split(">", 1)[0]
        self.assertNotIn("display:grid", mounting_tag)
        self.assertIn("Montaje operativo · sólo Calendar", source)
        self.assertIn("mismos equipos de la cotización", source)
        self.assertIn("Montaje antes del evento comercial", source)
        self.assertIn("Montaje el mismo día", source)

    def test_confirmed_fields_are_locked_and_operational_fields_sync(self):
        api_source = (ROOT / "backend/routers/leads.py").read_text(encoding="utf-8")
        ui_source = (ROOT / "web/views/leads.html").read_text(encoding="utf-8")
        tools_source = (ROOT / "backend/routers/tools.py").read_text(encoding="utf-8")
        self.assertIn('protected_when_confirmed = {"id_marca", "plataforma", "fecha_evento", "num_cotizacion", "monto_cotizado"}', api_source)
        self.assertIn('sync_fields = sorted({"telefono", "direccion", "id_comuna", "id_tipo_cliente"}', api_source)
        self.assertIn("confirmedLocked", ui_source)
        self.assertIn("sync_confirmed_calendar_metadata", tools_source)
        self.assertIn("calendar_only = bool(item.get", tools_source)

    def test_calendar_removal_discovers_and_deletes_mounting_children(self):
        class Result:
            def __init__(self, *, scalar=None, row=None):
                self._scalar = scalar
                self._row = row

            def scalar(self):
                return self._scalar

            def mappings(self):
                return self

            def first(self):
                return self._row

        class FakeDB:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, statement, _params=None):
                sql = str(statement)
                if "pg_try_advisory_lock" in sql:
                    return Result(scalar=True)
                if "SELECT calendar_event_id" in sql:
                    return Result(
                        row={
                            "calendar_event_id": "commercial-1",
                            "calendar_event_ids_json": '["commercial-1", "mount-1"]',
                            "pre_events_json": '[{"calendar_kind":"COMMERCIAL"},{"calendar_only":true,"calendar_kind":"MOUNTING"}]',
                        }
                    )
                return Result(scalar=True)

        class Request:
            def __init__(self, value):
                self.value = value

            def execute(self):
                return self.value

        class Events:
            def __init__(self):
                self.deleted = []
                self.private_property = None

            def list(self, **kwargs):
                self.private_property = kwargs.get("privateExtendedProperty")
                return Request({"items": [{"id": "mount-1"}, {"id": "mount-2"}]})

            def delete(self, **kwargs):
                self.deleted.append(kwargs["eventId"])
                return Request({})

        events = Events()

        class Service:
            def events(self):
                return events

        with (
            patch.object(leads_agenda, "SessionLocal", return_value=FakeDB()),
            patch("backend.routers.tools._gcal_service", return_value=Service()),
            patch("backend.routers.tools._gcal_default_calendar_id", return_value="ops-calendar"),
        ):
            result = leads_agenda.delete_confirmed_calendar_events(77)

        self.assertEqual(events.private_property, "lead_id=77")
        self.assertEqual(events.deleted, ["commercial-1", "mount-1", "mount-2"])
        self.assertEqual(result["deleted"], 3)
        self.assertEqual(result["mounting_planned"], 1)

    def test_calendar_removal_is_fail_closed_when_discovery_fails(self):
        class Result:
            def __init__(self, scalar=None, row=None): self._scalar, self._row = scalar, row
            def scalar(self): return self._scalar
            def mappings(self): return self
            def first(self): return self._row

        class FakeDB:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def execute(self, statement, _params=None):
                if "pg_try_advisory_lock" in str(statement): return Result(scalar=True)
                if "SELECT calendar_event_id" in str(statement): return Result(row={"calendar_event_id": "event-1", "calendar_event_ids_json": "", "pre_events_json": "[]"})
                return Result(scalar=True)

        class BrokenRequest:
            def execute(self): raise RuntimeError("calendar unavailable")

        class Events:
            def list(self, **_kwargs): return BrokenRequest()

        class Service:
            def events(self): return Events()

        with (
            patch.object(leads_agenda, "SessionLocal", return_value=FakeDB()),
            patch("backend.routers.tools._gcal_service", return_value=Service()),
            patch("backend.routers.tools._gcal_default_calendar_id", return_value="ops-calendar"),
        ):
            with self.assertRaises(HTTPException) as raised:
                leads_agenda.delete_confirmed_calendar_events(88)
        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
