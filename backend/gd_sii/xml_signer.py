from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

from .certificate import LoadedCertificate

DS = "http://www.w3.org/2000/09/xmldsig#"


def _c14n(node) -> bytes:
    return etree.tostring(node, method="c14n", exclusive=False, with_comments=False)


def sign_seed_xml(seed: str, loaded: LoadedCertificate) -> str:
    root = etree.Element("getToken")
    item = etree.SubElement(root, "item")
    etree.SubElement(item, "Semilla").text = str(seed).strip()
    signature = etree.SubElement(root, etree.QName(DS, "Signature"), nsmap={"ds": DS})
    signed_info = etree.SubElement(signature, etree.QName(DS, "SignedInfo"))
    etree.SubElement(signed_info, etree.QName(DS, "CanonicalizationMethod"), Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315")
    etree.SubElement(signed_info, etree.QName(DS, "SignatureMethod"), Algorithm=f"{DS}rsa-sha1")
    reference = etree.SubElement(signed_info, etree.QName(DS, "Reference"), URI="")
    transforms = etree.SubElement(reference, etree.QName(DS, "Transforms"))
    etree.SubElement(transforms, etree.QName(DS, "Transform"), Algorithm=f"{DS}enveloped-signature")
    etree.SubElement(reference, etree.QName(DS, "DigestMethod"), Algorithm=f"{DS}sha1")
    unsigned_root = etree.fromstring(etree.tostring(root))
    unsigned_sig = unsigned_root.xpath("//*[local-name()='Signature']")[0]
    unsigned_sig.getparent().remove(unsigned_sig)
    digest = base64.b64encode(hashlib.sha1(_c14n(unsigned_root)).digest()).decode()
    etree.SubElement(reference, etree.QName(DS, "DigestValue")).text = digest
    signature_bytes = loaded.private_key.sign(_c14n(signed_info), padding.PKCS1v15(), hashes.SHA1())
    etree.SubElement(signature, etree.QName(DS, "SignatureValue")).text = base64.b64encode(signature_bytes).decode()
    key_info = etree.SubElement(signature, etree.QName(DS, "KeyInfo"))
    x509_data = etree.SubElement(key_info, etree.QName(DS, "X509Data"))
    der = loaded.certificate.public_bytes(serialization.Encoding.DER)
    etree.SubElement(x509_data, etree.QName(DS, "X509Certificate")).text = base64.b64encode(der).decode()
    return etree.tostring(root, xml_declaration=True, encoding="ISO-8859-1").decode("ISO-8859-1")
