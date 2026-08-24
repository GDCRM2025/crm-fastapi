import tempfile
import unittest
from pathlib import Path

from backend.core.pdf_integrity import (
    is_deliverable_pdf_file,
    is_valid_pdf_bytes,
    is_valid_pdf_file,
)


class PdfIntegrityTests(unittest.TestCase):
    def test_accepts_pdf_with_header_and_eof(self):
        data = b"%PDF-1.7\n" + (b"0" * 256) + b"\n%%EOF\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quote.pdf"
            path.write_bytes(data)

            self.assertTrue(is_valid_pdf_bytes(data))
            self.assertTrue(is_valid_pdf_file(path))
            self.assertTrue(is_deliverable_pdf_file(path, max_bytes=len(data)))
            self.assertFalse(is_deliverable_pdf_file(path, max_bytes=len(data) - 1))

    def test_rejects_html_json_and_truncated_pdf(self):
        truncated = b"%PDF-1.7\n" + (b"0" * 256)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quote.pdf"
            path.write_bytes(truncated)

            self.assertFalse(is_valid_pdf_bytes(b'<html><h1>Error</h1></html>'))
            self.assertFalse(is_valid_pdf_bytes(b"null"))
            self.assertFalse(is_valid_pdf_bytes(truncated))
            self.assertFalse(is_valid_pdf_file(path))


if __name__ == "__main__":
    unittest.main()
