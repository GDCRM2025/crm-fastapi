from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12

from .exceptions import SIICertificateError, SIICertificateExpired, SIICertificatePasswordInvalid


@dataclass(frozen=True)
class LoadedCertificate:
    private_key: object
    certificate: x509.Certificate
    chain: tuple[x509.Certificate, ...]

    @property
    def metadata(self) -> dict[str, object]:
        cert = self.certificate
        return {
            "subject": cert.subject.rfc4514_string(),
            "serial": format(cert.serial_number, "X"),
            "valid_from": cert.not_valid_before_utc,
            "valid_to": cert.not_valid_after_utc,
        }


def load_pkcs12(data: bytes, password: str) -> LoadedCertificate:
    if not data:
        raise SIICertificateError("Certificado vacio")
    try:
        private_key, certificate, chain = pkcs12.load_key_and_certificates(data, password.encode("utf-8"))
    except ValueError as exc:
        raise SIICertificatePasswordInvalid("Certificado o password invalido") from exc
    if private_key is None or certificate is None:
        raise SIICertificateError("El PKCS#12 no contiene llave privada y certificado")
    now = datetime.now(timezone.utc)
    if certificate.not_valid_after_utc <= now:
        raise SIICertificateExpired("Certificado expirado")
    if certificate.not_valid_before_utc > now:
        raise SIICertificateError("Certificado aun no vigente")
    return LoadedCertificate(private_key, certificate, tuple(chain or ()))
