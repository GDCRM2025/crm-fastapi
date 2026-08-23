from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.parser import BytesParser

from .exceptions import SIIConnectionError


@dataclass(frozen=True)
class EmailDTEAttachment:
    xml_bytes: bytes
    external_id: str
    metadata: dict[str, str]


class GIAEmailDTEProvider:
    """Lectura IMAP read-only sobre las cuentas de pago ya configuradas en Correo GIA."""

    def __init__(self, *, max_messages_per_account: int = 100) -> None:
        self.max_messages_per_account = max(1, min(int(max_messages_per_account), 500))

    def fetch_new_dte_attachments(self) -> list[EmailDTEAttachment]:
        try:
            from backend.routers.gia_email import _imap_connect, _load_accounts
        except Exception as exc:
            raise SIIConnectionError("Infraestructura de correo GIA no disponible") from exc
        accounts = [a for a in _load_accounts() if str(a.get("inbox_type") or "").lower() == "payments"]
        if not accounts:
            raise SIIConnectionError("No hay una cuenta de pagos configurada para XML DTE")
        attachments: list[EmailDTEAttachment] = []
        for account in accounts:
            imap = None
            try:
                imap = _imap_connect(host=account["imap_host"], port=int(account["imap_port"]), use_ssl=bool(account["imap_ssl"]))
                imap.login(account["username"], account["password"])
                folder = str(account.get("imap_folder") or "INBOX")
                status, _ = imap.select(folder, readonly=True)
                if status != "OK":
                    raise SIIConnectionError("No fue posible abrir la casilla DTE en modo lectura")
                status, data = imap.uid("search", None, "ALL")
                if status != "OK":
                    raise SIIConnectionError("No fue posible consultar la casilla DTE")
                uids = (data[0].split() if data and data[0] else [])[-self.max_messages_per_account :]
                for uid in uids:
                    fetch_status, message_data = imap.uid("fetch", uid, "(BODY.PEEK[])")
                    if fetch_status != "OK" or not message_data:
                        continue
                    raw = next((item[1] for item in message_data if isinstance(item, tuple) and len(item) > 1), None)
                    if not raw:
                        continue
                    message = BytesParser(policy=policy.default).parsebytes(raw)
                    message_id = str(message.get("Message-ID") or "").strip()
                    for index, part in enumerate(message.walk(), 1):
                        filename = str(part.get_filename() or "")
                        content_type = str(part.get_content_type() or "").lower()
                        if not (filename.lower().endswith(".xml") or content_type in {"application/xml", "text/xml"}):
                            continue
                        payload = part.get_payload(decode=True)
                        if not payload or len(payload) > 10 * 1024 * 1024:
                            continue
                        account_id = str(account.get("from_email") or account.get("username") or "mailbox").lower()
                        uid_text = uid.decode("ascii", "ignore") if isinstance(uid, bytes) else str(uid)
                        external_id = f"{account_id}:{folder}:{uid_text}:{index}"
                        attachments.append(EmailDTEAttachment(
                            xml_bytes=payload,
                            external_id=external_id,
                            metadata={"message_id": message_id[:500], "filename": filename[:255], "account": account_id, "folder": folder},
                        ))
            except SIIConnectionError:
                raise
            except Exception as exc:
                raise SIIConnectionError("No fue posible leer XML DTE desde Correo GIA") from exc
            finally:
                if imap is not None:
                    try:
                        imap.logout()
                    except Exception:
                        pass
        return attachments
