import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from backend.main import _apply_security_headers
from backend.routers import quotes_override


class ServerReconciliationHotfixTests(unittest.TestCase):
    def test_web_assets_are_never_cached(self):
        request = SimpleNamespace(url=SimpleNamespace(path="/web/views/leads.html"))
        response = SimpleNamespace(headers={"content-type": "text/html; charset=utf-8"})

        _apply_security_headers(request, response)

        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(response.headers["Expires"], "0")

    @patch.object(quotes_override, "_cols_for", return_value=["id_cotizacion", "fecha", "numero"])
    @patch.object(quotes_override, "_table_exists", side_effect=lambda table: table == "cotizaciones")
    def test_quote_history_rejects_invalid_filter_values(self, _table_exists, _cols_for):
        common = {
            "id_lead": None,
            "limit": 50,
            "offset": 0,
            "q": "",
            "fecha_hasta": None,
            "tipo_cliente": "",
            "orden": "fecha_desc",
            "user": {"role": "ADMIN", "marcas": []},
        }
        with self.assertRaises(HTTPException) as invalid_date:
            quotes_override.history(fecha_desde="12/08/2026", **common)
        self.assertEqual(invalid_date.exception.status_code, 400)

        with self.assertRaises(HTTPException) as invalid_order:
            quotes_override.history(fecha_desde=None, **{**common, "orden": "DROP TABLE"})
        self.assertEqual(invalid_order.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
