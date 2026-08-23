class SIIError(Exception):
    code = "SII_ERROR"

    def __init__(self, safe_message: str = "Error de integracion SII") -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


class SIIAuthFailed(SIIError):
    code = "SII_AUTH_FAILED"


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


class SIIXMLInvalid(SIIError):
    code = "SII_XML_INVALID"


class SIIDTEUnsupported(SIIXMLInvalid):
    code = "SII_DTE_UNSUPPORTED"


class SIIRCVInvalid(SIIError):
    code = "SII_RCV_INVALID"


class ReceiverRUTMismatch(SIIError):
    code = "RECEIVER_RUT_MISMATCH"


class SupplierRUTInvalid(SIIError):
    code = "SUPPLIER_RUT_INVALID"


class SIINotSupportedError(SIIError):
    code = "SII_NOT_SUPPORTED"
