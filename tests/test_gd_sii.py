from __future__ import annotations

import os
import tempfile
import unittest
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from backend.gd_sii.auth import SII_ENVIRONMENTS, _find_response_value
from backend.gd_sii.certificate import load_pkcs12
from backend.gd_sii.dte_parser import parse_dte_xml
from backend.gd_sii.email_provider import GIAEmailDTEProvider
from backend.gd_sii.exceptions import (
    SIICertificateExpired,
    SIICertificatePasswordInvalid,
    SIIRCVInvalid,
    SIIXMLInvalid,
    SupplierRUTInvalid,
)
from backend.gd_sii.rcv_parser import parse_rcv_csv
from backend.gd_sii.rut import calculate_dv, normalize_chilean_rut
from backend.gd_sii.schemas import SIIToken
from backend.gd_sii.storage import PrivateXMLStorage
from backend.gd_sii.token_cache import SIITokenCache
from backend.gd_sii.xml_signer import sign_seed_xml
from backend.routers.sii_finance import _role

ROOT = Path(__file__).resolve().parents[1]
RECEIVER_BODY = "76472046"
ISSUER_BODY = "76123456"
RECEIVER = f"{RECEIVER_BODY}-{calculate_dv(RECEIVER_BODY)}"
ISSUER = f"{ISSUER_BODY}-{calculate_dv(ISSUER_BODY)}"


def dte_xml(doc_type: str, *, namespace: bool = True, receiver: str = RECEIVER) -> bytes:
    ns = ' xmlns="http://www.sii.cl/SiiDte"' if namespace else ""
    reference = "" if doc_type in {"33", "34"} else "<Referencia><NroLinRef>1</NroLinRef><TpoDocRef>33</TpoDocRef><FolioRef>99</FolioRef><FchRef>2026-08-01</FchRef></Referencia>"
    return f'''<?xml version="1.0" encoding="ISO-8859-1"?>
<DTE{ns}><Documento ID="D1"><Encabezado><IdDoc><TipoDTE>{doc_type}</TipoDTE><Folio>123</Folio><FchEmis>2026-08-20</FchEmis><FchVenc>2026-09-20</FchVenc></IdDoc>
<Emisor><RUTEmisor>{ISSUER}</RUTEmisor><RznSoc>PROVEEDOR SPA</RznSoc><GiroEmis>SERVICIOS</GiroEmis><Acteco>620200</Acteco><DirOrigen>CALLE 1</DirOrigen><CmnaOrigen>SANTIAGO</CmnaOrigen></Emisor>
<Receptor><RUTRecep>{receiver}</RUTRecep></Receptor><Totales><MntNeto>1000</MntNeto><IVA>190</IVA><MntTotal>1190</MntTotal></Totales></Encabezado>
<Detalle><NroLinDet>1</NroLinDet><NmbItem>Servicio</NmbItem><QtyItem>1</QtyItem><PrcItem>1000</PrcItem><MontoItem>1000</MontoItem></Detalle>{reference}</Documento></DTE>'''.encode("iso-8859-1")


def make_pfx(*, password: str = "correcta", expired: bool = False) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Prueba SII")])
    now = datetime.now(timezone.utc)
    end = now - timedelta(days=1) if expired else now + timedelta(days=30)
    start = now - timedelta(days=30)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(end)
            .sign(key, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b"sii", key, cert, None, serialization.BestAvailableEncryption(password.encode()))


class RutTests(unittest.TestCase):
    def test_formats_normalize_to_one_value(self):
        formatted = f"{RECEIVER_BODY[:2]}.{RECEIVER_BODY[2:5]}.{RECEIVER_BODY[5:]}-{RECEIVER[-1]}"
        self.assertEqual(normalize_chilean_rut(formatted), RECEIVER)
        self.assertEqual(normalize_chilean_rut(RECEIVER.replace("-", "")), RECEIVER)

    def test_invalid_dv_is_rejected(self):
        wrong = "0" if RECEIVER[-1] != "0" else "1"
        with self.assertRaises(SupplierRUTInvalid):
            normalize_chilean_rut(f"{RECEIVER_BODY}-{wrong}")


class DTEParserTests(unittest.TestCase):
    def test_supported_types_namespaces_lines_and_references(self):
        for doc_type in ("33", "34", "56", "61"):
            with self.subTest(doc_type=doc_type):
                parsed = parse_dte_xml(dte_xml(doc_type))
                self.assertEqual(parsed.document_type, doc_type)
                self.assertEqual(parsed.receiver_rut, RECEIVER)
                self.assertEqual(parsed.issuer.rut, ISSUER)
                self.assertEqual(parsed.total_amount, 1190)
                self.assertEqual(len(parsed.lines), 1)
                self.assertEqual(len(parsed.references), 0 if doc_type in {"33", "34"} else 1)
                self.assertEqual(len(parsed.xml_sha256 or ""), 64)

    def test_no_namespace_is_supported(self):
        self.assertEqual(parse_dte_xml(dte_xml("33", namespace=False)).folio, "123")

    def test_invalid_and_xxe_xml_are_blocked(self):
        with self.assertRaises(SIIXMLInvalid):
            parse_dte_xml(b"<DTE>")
        xxe = b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><DTE>&e;</DTE>'
        with self.assertRaises(SIIXMLInvalid):
            parse_dte_xml(xxe)


class RCVParserTests(unittest.TestCase):
    def test_bom_semicolon_extra_columns_and_header_variations(self):
        csv_data = ("Tipo Documento;Folio;RUT Proveedor;Razon Social;Fecha Emision;Monto Neto;IVA Recuperable;Monto Total;Columna Extra\n"
                    f"33;77;{ISSUER};PROVEEDOR SPA;20/08/2026;1.000;190;1.190;ignorar\n").encode("utf-8-sig")
        docs = parse_rcv_csv(csv_data, receiver_rut=RECEIVER)
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].issuer.rut, ISSUER)
        self.assertEqual(docs[0].source, "SII_RCV_CSV")
        self.assertEqual(docs[0].total_amount, 1190)

    def test_missing_required_columns_fails(self):
        with self.assertRaises(SIIRCVInvalid):
            parse_rcv_csv(b"foo;bar\n1;2\n", receiver_rut=RECEIVER)


class CertificateAndAuthTests(unittest.TestCase):
    def test_valid_pfx_and_signature(self):
        loaded = load_pkcs12(make_pfx(), "correcta")
        signed = sign_seed_xml("123456", loaded)
        self.assertIn("SignatureValue", signed)
        self.assertNotIn("correcta", signed)

    def test_wrong_password_and_expired_certificate(self):
        with self.assertRaises(SIICertificatePasswordInvalid):
            load_pkcs12(make_pfx(), "incorrecta")
        with self.assertRaises(SIICertificateExpired):
            load_pkcs12(make_pfx(expired=True), "correcta")

    def test_official_environment_urls_and_nested_soap_response(self):
        self.assertIn("palena.sii.cl", SII_ENVIRONMENTS["PRODUCTION"]["seed_wsdl"])
        response = b"<Envelope><return>&lt;RESPUESTA&gt;&lt;SEMILLA&gt;42&lt;/SEMILLA&gt;&lt;/RESPUESTA&gt;</return></Envelope>"
        self.assertEqual(_find_response_value(response, "SEMILLA"), "42")


class CacheStorageAndSecurityTests(unittest.TestCase):
    def test_token_cache_expiry_and_invalidation(self):
        cache = SIITokenCache(); now = datetime.now(timezone.utc)
        cache.put(1, "CERTIFICATION", SIIToken("secret", now, now + timedelta(hours=1)))
        self.assertEqual(cache.get_valid(1, "certification").value, "secret")
        cache.invalidate(1, "CERTIFICATION")
        self.assertIsNone(cache.get_valid(1, "CERTIFICATION"))

    def test_private_storage_is_content_addressed_and_restricts_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            storage = PrivateXMLStorage(root)
            key = storage.put(1, "a" * 64, b"<DTE/>")
            self.assertEqual(storage.get(key), b"<DTE/>")
            self.assertEqual(key, storage.put(1, "a" * 64, b"different"))
            with self.assertRaises(ValueError): storage.get("../../etc/passwd")

    def test_rbac_rejects_sales_role(self):
        old = os.environ.get("GD_FINANCE_SII_ENABLED"); os.environ["GD_FINANCE_SII_ENABLED"] = "true"
        try:
            with self.assertRaises(HTTPException): _role({"role": "EJECUTIVO"}, {"SUPERADMIN"})
            _role({"role": "SUPER ADMIN"}, {"SUPERADMIN", "ADMIN"})
            _role({"role": "ADMIN"}, {"SUPERADMIN", "ADMIN"})
        finally:
            if old is None: os.environ.pop("GD_FINANCE_SII_ENABLED", None)
            else: os.environ["GD_FINANCE_SII_ENABLED"] = old

    def test_migration_is_additive_idempotent_and_reuses_finance_tables(self):
        sql = (ROOT / "migrations/2026_08_23_sii_received_finance.sql").read_text().upper()
        self.assertNotIn("DROP TABLE", sql)
        self.assertNotIn("TRUNCATE", sql)
        self.assertIn("IF NOT EXISTS", sql)
        self.assertIn("ALTER TABLE INV_PROVEEDORES", sql)
        self.assertIn("ALTER TABLE FIN_GASTOS", sql)
        self.assertIn("UNIQUE(ID_LEGAL_ENTITY, ISSUER_RUT, DOCUMENT_TYPE, FOLIO)", sql)


class EmailDTEProviderTests(unittest.TestCase):
    def test_reads_payment_xml_with_peek_and_readonly(self):
        from unittest.mock import patch
        message = EmailMessage()
        message["From"] = "proveedor@example.cl"
        message["To"] = "pagos@example.cl"
        message["Message-ID"] = "<dte-1@example.cl>"
        message.set_content("DTE adjunto")
        message.add_attachment(dte_xml("33"), maintype="application", subtype="xml", filename="factura.xml")

        class FakeIMAP:
            def __init__(self): self.calls = []
            def login(self, *_): self.calls.append("login")
            def select(self, folder, readonly=False): self.calls.append(("select", folder, readonly)); return "OK", []
            def uid(self, operation, *args):
                self.calls.append((operation, *args))
                if operation == "search": return "OK", [b"7"]
                return "OK", [(b"7", message.as_bytes())]
            def logout(self): self.calls.append("logout")

        fake = FakeIMAP()
        accounts = [{"inbox_type": "payments", "imap_host": "imap.example.cl", "imap_port": 993,
                     "imap_ssl": True, "username": "pagos@example.cl", "password": "secret-for-mock",
                     "from_email": "pagos@example.cl", "imap_folder": "INBOX"}]
        with patch("backend.routers.gia_email._load_accounts", return_value=accounts), patch("backend.routers.gia_email._imap_connect", return_value=fake):
            items = GIAEmailDTEProvider().fetch_new_dte_attachments()
        self.assertEqual(len(items), 1)
        self.assertIn(":7:", items[0].external_id)
        self.assertIn(("select", "INBOX", True), fake.calls)
        self.assertTrue(any(call[0] == "fetch" and "BODY.PEEK" in call[2] for call in fake.calls if isinstance(call, tuple)))


if __name__ == "__main__":
    unittest.main()
