from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.core.storage import persistent_data_root


class PersistentStorageTests(unittest.TestCase):
    def test_local_development_uses_project_data(self):
        root = Path("/tmp/gd-project")
        with patch.dict(os.environ, {"GD_PERSISTENT_DATA_DIR": ""}, clear=False):
            self.assertEqual(persistent_data_root(root), root / "data")

    def test_explicit_persistent_root_has_precedence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"GD_PERSISTENT_DATA_DIR": tmp}, clear=False
        ):
            self.assertEqual(persistent_data_root(Path("/opt/greendiamond/releases/abc")), Path(tmp))

    def test_quote_router_does_not_write_to_immutable_release_data(self):
        source = (Path(__file__).resolve().parents[1] / "backend/routers/quotes_override.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('base_dir = persistent_data_root() / "quotes"', source)
        self.assertNotIn('parents[2] / "data" / "quotes"', source)


if __name__ == "__main__":
    unittest.main()
