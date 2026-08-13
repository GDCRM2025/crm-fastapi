from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend.routers import gd_sales, tools


ROOT = Path(__file__).resolve().parents[1]


class SalesDayRegressionTests(unittest.TestCase):
    def test_agenda_timestamp_is_converted_from_utc_to_chile(self) -> None:
        with patch.object(tools, "_col_exists", return_value=True):
            expression = tools._agenda_sale_date_expr(object())
        self.assertIn("AT TIME ZONE 'UTC'", expression)
        self.assertIn("AT TIME ZONE 'America/Santiago'", expression)

    def test_sales_drilldown_and_dashboard_share_expression(self) -> None:
        source = (ROOT / "backend/routers/tools.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("_agenda_sale_date_expr(db)"), 4)


class ReportsRegressionTests(unittest.TestCase):
    def test_active_reporter_supports_real_tipos_cliente_schema(self) -> None:
        source = (ROOT / "backend/routers/tools.py").read_text(encoding="utf-8")
        active = source[source.index("def _dashboard_reportes_v2(") : source.index('@router.get("/dashboard/reportes")')]
        self.assertIn('"tipo" if "tipo" in tipos_cliente_cols', active)
        self.assertIn("AS tipo_cliente", active)

    def test_report_assets_are_local_and_reproducible(self) -> None:
        html = (ROOT / "web/views/reportes_v2.html").read_text(encoding="utf-8")
        self.assertNotIn("cdn.jsdelivr.net", html)
        for asset in (
            "web/vendor/sweetalert2-11.22.2.all.min.js",
            "web/vendor/echarts-5.5.0.min.js",
            "web/vendor/exceljs-4.4.0.min.js",
        ):
            self.assertTrue((ROOT / asset).is_file(), asset)


class GDSalesSecurityTests(unittest.TestCase):
    def test_admin_is_denied(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            gd_sales._require_superadmin({"role": "ADMIN"})
        self.assertEqual(caught.exception.status_code, 403)

    def test_superadmin_variants_are_allowed(self) -> None:
        for role in ("SUPERADMIN", "SUPER ADMIN", "SUPER_ADMIN"):
            gd_sales._require_superadmin({"role": role})

    def test_menu_is_explicitly_superadmin_only(self) -> None:
        panel = (ROOT / "web/js/panel.js").read_text(encoding="utf-8")
        self.assertIn('id: "gd_sales"', panel)
        self.assertIn("superadminOnly: true", panel)
        self.assertIn("external: true", panel)
        self.assertIn('window.open(viewURL(it.url), "_blank")', panel)
        self.assertIn("if (it.superadminOnly && !isCurrentSuperAdmin()) return;", panel)

    def test_static_server_snapshot_is_not_reintroduced(self) -> None:
        self.assertFalse((ROOT / "web/gd-sales/data.json").exists())
        html = (ROOT / "web/views/gd_sales.html").read_text(encoding="utf-8")
        self.assertIn("/tools/gd-sales/command-center", html)
        self.assertIn("Datos reales", html)

    def test_live_command_center_has_requested_contact_filters_and_actions(self) -> None:
        html = (ROOT / "web/views/gd_sales.html").read_text(encoding="utf-8")
        router = (ROOT / "backend/routers/gd_sales.py").read_text(encoding="utf-8")
        for marker in ('id="brand"', 'id="state"', 'id="from"', 'id="to"', "Monto mayor", "Monto menor"):
            self.assertIn(marker, html)
        self.assertIn("telefono", router)
        self.assertIn("id_cotizacion", router)
        self.assertIn("openQuote", html)
        self.assertIn('window.open(viewPath(`leads.html?open_lead=', html)
        self.assertNotIn("ASIGNAR EJECUTIVO", router)

    def test_wizard_single_event_and_layout_are_progressive(self) -> None:
        wizard = (ROOT / "web/js/agenda_wizard_v6.js").read_text(encoding="utf-8")
        leads = (ROOT / "web/views/leads.html").read_text(encoding="utf-8")
        self.assertIn("gdW6Simple #ag_products_allocator{display:none!important}", wizard)
        self.assertIn("ASIGNACIÓN AUTOMÁTICA", wizard)
        self.assertIn("gdW6PaymentRow", wizard)
        self.assertIn("font-size:21px!important", wizard)
        self.assertIn("movePriorMountingToFirstStep", wizard)
        self.assertIn("montaje_pending", leads)


class SurveyUXTests(unittest.TestCase):
    def test_management_defaults_to_seven_day_window(self) -> None:
        html = (ROOT / "web/views/encuestas_eventos.html").read_text(encoding="utf-8")
        self.assertIn("window_days=7", html)
        self.assertIn("No hay encuestas pendientes en los últimos 7 días", html)

    def test_invalid_public_link_cannot_start_survey(self) -> None:
        html = (ROOT / "web/views/satisfaccion.html").read_text(encoding="utf-8")
        self.assertIn('qs("#startBtn").style.display="none"', html)
        self.assertIn("Por seguridad no es posible comenzar", html)


if __name__ == "__main__":
    unittest.main()
