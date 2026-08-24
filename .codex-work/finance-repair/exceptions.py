class SIIError(Exception):
    code = "SII_ERROR"

    def __init__(self, safe_message: str = "Error de integracion SII") -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class SIIAuthFailed(SIIError):
    code = "SII_AUTH_FAILED"


class SIIAuthRejected(SIIAuthFailed):
    code = "SII_AUTH_REJECTED"

    def __init__(self, estado: str, glosa: str | None = None) -> None:
        self.sii_estado = "".join(ch for ch in str(estado or "") if ch.isalnum() or ch in "-_")[:16]
        self.sii_glosa = "".join(ch for ch in str(glosa or "") if ch.isprintable()).strip()[:240] or None
        message = f"Código SII: {self.sii_estado or 'desconocido'}"
        if self.sii_glosa:
            message += f" — {self.sii_glosa}"
        super().__init__(message)


class SIIConnectionError(SIIError):
    code = "SII_CONNECTION_FAILED"


class SIITimeout(SIIConnectionError):
    code = "SII_TIMEOUT"


class SIICertificateError(SIIError):
    code = "SII_CERTIFICATE_INVALID"


class SIICertificateExpired(SIICertificateError):
    code = "SII_CERTIFICATE_EXPIRED"


class SIICertificatePasswordInvalid(SIICertificateError):
    code = "SII_CERTIFICATE_PASSWORD_INVALID"


class SIIInvalidResponse(SIIError):
    code = "SII_INVALID_RESPONSE"


class SIITokenMissing(SIIInvalidResponse):
    code = "SII_TOKEN_MISSING"


class SIIXMLInvalid(SIIError):
    code = "SII_XML_INVALID"


class SIIDTEUnsupported(SIIXMLInvalid):
    code = "SII_DTE_UNSUPPORTED"


class SIIRCVInvalid(SIIError):
    code = "SII_RCV_INVALID"


class RCVEntityConflict(SIIError):
    code = "RCV_ENTITY_CONFLICT"


class ReceiverRUTMismatch(SIIError):
    code = "RECEIVER_RUT_MISMATCH"


class SupplierRUTInvalid(SIIError):
    code = "SUPPLIER_RUT_INVALID"


class SIINotSupportedError(SIIError):
    code = "SII_NOT_SUPPORTED"


class PayableReconciliationError(SIIError):
    code = "PAYABLE_RECONCILIATION_INVALID"
