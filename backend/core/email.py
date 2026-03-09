from __future__ import annotations

import os
import shutil
import smtplib
import socket
import subprocess
from email.message import EmailMessage
from typing import Iterable


class EmailConfigError(RuntimeError):
    pass


def _get_env(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    return v


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _smtp_timeout() -> float:
    try:
        return float(_get_env("SMTP_TIMEOUT") or "12")
    except Exception:
        return 12.0


def _resolve_ipv4_first(host: str, port: int) -> list[str]:
    """
    Some cPanel environments have broken IPv6 routing (Errno 101).
    We prefer IPv4 A records when possible and keep host as fallback.
    """
    addrs: list[str] = []
    try:
        infos = socket.getaddrinfo(host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        v4 = [i[4][0] for i in infos if i[0] == socket.AF_INET]
        v6 = [i[4][0] for i in infos if i[0] == socket.AF_INET6]
        # Prefer IPv4 first; keep both in case only one family works.
        addrs.extend(dict.fromkeys(v4 + v6))
    except Exception:
        pass
    # Always keep original host as a final option.
    if host not in addrs:
        addrs.append(host)
    return addrs


def _send_via_sendmail(msg: EmailMessage) -> None:
    sendmail = shutil.which("sendmail") or "/usr/sbin/sendmail"
    if not sendmail or not os.path.exists(sendmail):
        raise RuntimeError("sendmail not available")
    # -t: read recipients from headers
    subprocess.run([sendmail, "-t", "-i"], input=msg.as_bytes(), check=True)

def _send_message(msg: EmailMessage) -> None:
    """
    Envío best-effort usando:
    - SMTP (SSL/TLS)
    - fallback a sendmail (cPanel) si SMTP falla o la red está caída (Errno 101).
    """
    host = _get_env("SMTP_HOST")
    port = int(_get_env("SMTP_PORT") or "587")
    user = _get_env("SMTP_USER")
    password = _get_env("SMTP_PASS")
    from_addr = _get_env("SMTP_FROM") or user

    # Modo sendmail (cPanel suele tener MTA local). Útil cuando hay bloqueos outbound SMTP.
    force_sendmail = _truthy(_get_env("SMTP_SENDMAIL")) or _truthy(_get_env("SMTP_SENDMAIL_ONLY"))

    if force_sendmail:
        if not from_addr:
            raise EmailConfigError("SMTP_FROM requerido para sendmail")
    else:
        if not host or not user or not password or not from_addr:
            raise EmailConfigError("SMTP no configurado (SMTP_HOST/SMTP_USER/SMTP_PASS/SMTP_FROM)")

    # Si se fuerza sendmail, no intentamos SMTP.
    if force_sendmail:
        _send_via_sendmail(msg)
        return

    timeout = _smtp_timeout()
    use_ssl = port == 465 or _truthy(_get_env("SMTP_SSL"))
    use_tls = not (_get_env("SMTP_TLS").strip().lower() in ("0", "false", "no"))
    force_ipv4 = _truthy(_get_env("SMTP_FORCE_IPV4"))
    fallback_sendmail = _get_env("SMTP_FALLBACK_SENDMAIL")
    if fallback_sendmail == "":
        # default ON (cPanel suele tener sendmail local y evita bloqueos outbound)
        fallback_sendmail = "1"

    targets: Iterable[str]
    if force_ipv4:
        targets = _resolve_ipv4_first(host, port)
        # Keep only IPv4 + host fallback (avoid v6 if it breaks routing)
        targets = [t for t in targets if ":" not in t] + [host]
    else:
        targets = _resolve_ipv4_first(host, port)

    last_err: Exception | None = None
    for target in targets:
        try:
            if use_ssl:
                with smtplib.SMTP_SSL(target, port, timeout=timeout) as smtp:
                    smtp.login(user, password)
                    smtp.send_message(msg)
            else:
                with smtplib.SMTP(target, port, timeout=timeout) as smtp:
                    smtp.ehlo()
                    if use_tls:
                        smtp.starttls()
                        smtp.ehlo()
                    smtp.login(user, password)
                    smtp.send_message(msg)
            return
        except Exception as e:
            last_err = e
            continue

    # Fallback: local sendmail (no requiere salida a internet)
    # Si la red está caída (Errno 101), intentamos sendmail aunque el flag esté apagado.
    force_fallback = False
    try:
        if isinstance(last_err, OSError) and getattr(last_err, "errno", None) == 101:
            force_fallback = True
    except Exception:
        force_fallback = False

    if force_fallback or _truthy(str(fallback_sendmail)):
        try:
            _send_via_sendmail(msg)
            return
        except Exception as e:
            last_err = e

    if last_err:
        raise last_err

def send_email(to_addr: str, subject: str, text: str, html: str | None = None) -> None:
    user = _get_env("SMTP_USER")
    from_addr = _get_env("SMTP_FROM") or user
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    _send_message(msg)


def send_email_group(
    to_addrs: list[str] | tuple[str, ...],
    subject: str,
    text: str,
    html: str | None = None,
) -> None:
    """
    Envía UN solo correo a varios destinatarios:
    - To: primer correo
    - Cc: resto
    Esto reduce conexiones SMTP y es más cómodo para operaciones (todos en el mismo hilo).
    """
    to_list = [a.strip() for a in (list(to_addrs) if to_addrs else []) if a and a.strip()]
    if not to_list:
        raise ValueError("to_addrs vacío")
    user = _get_env("SMTP_USER")
    from_addr = _get_env("SMTP_FROM") or user

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_list[0]
    if len(to_list) > 1:
        msg["Cc"] = ", ".join(to_list[1:])
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    _send_message(msg)
