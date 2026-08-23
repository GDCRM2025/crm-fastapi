from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from defusedxml import ElementTree
from lxml import etree

from .certificate import LoadedCertificate
from .exceptions import SIIAuthFailed, SIIConnectionError, SIIInvalidResponse, SIITimeout
from .schemas import SIIToken
from .token_cache import SIITokenCache
from .xml_signer import sign_seed_xml

SII_ENVIRONMENTS = {
    "PRODUCTION": {
        "seed_wsdl": "https://palena.sii.cl/DTEWS/CrSeed.jws?WSDL",
        "token_wsdl": "https://palena.sii.cl/DTEWS/GetTokenFromSeed.jws?WSDL",
    },
    "CERTIFICATION": {
        "seed_wsdl": "https://maullin.sii.cl/DTEWS/CrSeed.jws?WSDL",
        "token_wsdl": "https://maullin.sii.cl/DTEWS/GetTokenFromSeed.jws?WSDL",
    },
}
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 30.0


def _soap(operation: str, argument_name: str | None = None, argument_value: str | None = None) -> bytes:
    envelope = etree.Element("{http://schemas.xmlsoap.org/soap/envelope/}Envelope", nsmap={"soapenv": "http://schemas.xmlsoap.org/soap/envelope/", "web": "http://DefaultNamespace"})
    etree.SubElement(envelope, "{http://schemas.xmlsoap.org/soap/envelope/}Header")
    body = etree.SubElement(envelope, "{http://schemas.xmlsoap.org/soap/envelope/}Body")
    action = etree.SubElement(body, f"{{http://DefaultNamespace}}{operation}")
    if argument_name:
        etree.SubElement(action, argument_name).text = argument_value or ""
    return etree.tostring(envelope, xml_declaration=True, encoding="utf-8")


def _find_response_value(data: bytes, expected: str) -> str:
    try:
        root = ElementTree.fromstring(data)
    except Exception as exc:
        raise SIIInvalidResponse("Respuesta XML invalida del SII") from exc
    values = [node.text.strip() for node in root.iter() if node.tag.split("}")[-1].lower() == expected.lower() and node.text and node.text.strip()]
    if values:
        return values[0]
    # SOAP retorna frecuentemente otro XML escapado dentro de *Return.
    for node in root.iter():
        if node.text and ("<RESPUESTA" in node.text.upper() or "<SEMILLA" in node.text.upper() or "<TOKEN" in node.text.upper()):
            try:
                inner = ElementTree.fromstring(node.text.encode())
                found = [x.text.strip() for x in inner.iter() if x.tag.split("}")[-1].lower() == expected.lower() and x.text]
                if found:
                    return found[0]
            except Exception:
                continue
    raise SIIInvalidResponse(f"Respuesta SII sin {expected}")


class SIIAuthClient:
    def __init__(self, environment: str, *, client: httpx.Client | None = None) -> None:
        self.environment = environment.upper()
        if self.environment not in SII_ENVIRONMENTS:
            raise ValueError("Ambiente SII invalido")
        self._owned_client = client is None
        self.client = client or httpx.Client(timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT), verify=True)

    def close(self) -> None:
        if self._owned_client:
            self.client.close()

    def _post(self, wsdl_key: str, operation: str, payload: bytes) -> bytes:
        url = SII_ENVIRONMENTS[self.environment][wsdl_key].split("?", 1)[0]
        try:
            response = self.client.post(url, content=payload, headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": operation})
            response.raise_for_status()
            return response.content
        except httpx.TimeoutException as exc:
            raise SIITimeout("Timeout al conectar con SII") from exc
        except httpx.HTTPError as exc:
            raise SIIConnectionError("No fue posible conectar con SII") from exc

    def get_seed(self) -> str:
        return _find_response_value(self._post("seed_wsdl", "getSeed", _soap("getSeed")), "SEMILLA")

    def sign_seed(self, seed: str, certificate: LoadedCertificate) -> str:
        return sign_seed_xml(seed, certificate)

    def get_token(self, signed_seed_xml: str) -> str:
        data = self._post("token_wsdl", "getToken", _soap("getToken", "pszXml", signed_seed_xml))
        return _find_response_value(data, "TOKEN")

    def authenticate(self, certificate: LoadedCertificate) -> SIIToken:
        try:
            token = self.get_token(self.sign_seed(self.get_seed(), certificate))
        except (SIIConnectionError, SIIInvalidResponse):
            raise
        except Exception as exc:
            raise SIIAuthFailed("Autenticacion SII fallida") from exc
        now = datetime.now(timezone.utc)
        return SIIToken(token, now, now + timedelta(hours=1))


GLOBAL_TOKEN_CACHE = SIITokenCache()


def authenticate_cached(legal_entity_id: int, environment: str, certificate: LoadedCertificate) -> SIIToken:
    cached = GLOBAL_TOKEN_CACHE.get_valid(legal_entity_id, environment)
    if cached:
        return cached
    with GLOBAL_TOKEN_CACHE.lock_for(legal_entity_id, environment):
        cached = GLOBAL_TOKEN_CACHE.get_valid(legal_entity_id, environment)
        if cached:
            return cached
        client = SIIAuthClient(environment)
        try:
            token = client.authenticate(certificate)
            GLOBAL_TOKEN_CACHE.put(legal_entity_id, environment, token)
            return token
        finally:
            client.close()
