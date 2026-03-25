from __future__ import annotations

import json
import os
import re
import ssl
from datetime import date, datetime, timezone
from email.header import decode_header
from email.message import Message
from email.parser import BytesParser
from email.policy import default
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import imaplib
import smtplib
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import text

from backend.core.db import get_connection

try:
    from backend.routers.auth import get_current_user  # type: ignore
except Exception:  # pragma: no cover
    def get_current_user():  # type: ignore
        return {"role": "ADMIN", "username": "dev", "marcas": []}


router = APIRouter(prefix="/gia/email", tags=["gia-email"])


def _role(user: dict) -> str:
    return str(user.get("role") or user.get("rol") or "").upper().strip()


def _is_admin(user: dict) -> bool:
    return _role(user) in ("ADMIN", "SUPERADMIN")


def _user_marcas_ids(user: dict) -> list[int]:
    out: list[int] = []
    for x in (user.get("marcas") or []):
        try:
            out.append(int(x))
        except Exception:
            continue
    return out


def _table_exists(conn, table: str) -> bool:
    try:
        return bool(conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())
    except Exception:
        return False


def _resolve_uid(conn, user: dict) -> int | None:
    raw = str(user.get("id") or "").strip()
    if raw.isdigit():
        return int(raw)
    if not _table_exists(conn, "usuarios"):
        return None
    cand = [
        str(user.get("username") or "").strip(),
        str(user.get("email") or "").strip(),
        str(user.get("name") or "").strip(),
        str(user.get("id") or "").strip(),
    ]
    cand = [c for c in cand if c]
    for c in cand:
        try:
            v = conn.execute(
                text(
                    """
                    SELECT id_usuario
                    FROM public.usuarios
                    WHERE email=:u OR username=:u
                    ORDER BY id_usuario
                    LIMIT 1
                    """
                ),
                {"u": c},
            ).scalar()
            if v is not None and str(v).isdigit():
                return int(v)
        except Exception:
            continue
    return None


def _fallback_marcas_ids(conn, user: dict) -> list[int]:
    if not _table_exists(conn, "usuarios_marcas"):
        return []
    uid = _resolve_uid(conn, user)
    if uid is None:
        return []
    try:
        rows = conn.execute(text("SELECT id_marca FROM public.usuarios_marcas WHERE id_usuario=:u ORDER BY id_marca"), {"u": int(uid)}).fetchall()
        out: list[int] = []
        for r in rows:
            try:
                out.append(int(r[0]))
            except Exception:
                pass
        return out
    except Exception:
        return []


def _norm(s: str) -> str:
    s = (s or "").strip().upper()
    s = re.sub(r"\\s+", " ", s)
    return s


def _ensure_schema() -> None:
    with get_connection() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.gia_email_messages (
                  id_msg BIGSERIAL PRIMARY KEY,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

                  id_marca INTEGER,
                  marca TEXT,
                  account_email TEXT NOT NULL,
                  inbox_type TEXT,
                  imap_folder TEXT,

                  imap_uid BIGINT,
                  message_id TEXT,

                  from_email TEXT,
                  from_name TEXT,
                  subject TEXT,
                  received_at TIMESTAMPTZ,

                  body_text TEXT,
                  body_html TEXT,

                  kind TEXT,
                  kind_reason TEXT,
                  reply_to_email TEXT,
                  parsed_email TEXT,
                  parsed_phone TEXT,
                  parsed_comuna TEXT,
                  parsed_fecha_evento DATE,
                  parsed_cliente TEXT,
                  parsed_rid TEXT,

                  ack_sent BOOLEAN NOT NULL DEFAULT FALSE,
                  ack_sent_at TIMESTAMPTZ,
                  ack_error TEXT,

                  reply_sent BOOLEAN NOT NULL DEFAULT FALSE,
                  reply_sent_at TIMESTAMPTZ,
                  reply_error TEXT,
                  reply_body TEXT,

                  lead_id BIGINT
                );
                """
            )
        )
        # columnas nuevas (safe en ambientes antiguos)
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS kind TEXT;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS kind_reason TEXT;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS reply_to_email TEXT;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS parsed_email TEXT;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS parsed_phone TEXT;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS parsed_comuna TEXT;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS parsed_fecha_evento DATE;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS parsed_cliente TEXT;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS parsed_rid TEXT;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS inbox_type TEXT;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS imap_folder TEXT;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS reply_sent BOOLEAN NOT NULL DEFAULT FALSE;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS reply_sent_at TIMESTAMPTZ;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS reply_error TEXT;"))
        conn.execute(text("ALTER TABLE public.gia_email_messages ADD COLUMN IF NOT EXISTS reply_body TEXT;"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_gia_email_messages_marca ON public.gia_email_messages(id_marca, created_at DESC);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_gia_email_messages_account_uid ON public.gia_email_messages(account_email, imap_uid DESC);"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_gia_email_messages_type ON public.gia_email_messages(inbox_type, id_marca, created_at DESC);"))
        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_gia_email_messages_dedupe
                ON public.gia_email_messages(account_email, COALESCE(message_id,''), COALESCE(imap_uid,0));
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.gia_email_state (
                  account_email TEXT PRIMARY KEY,
                  last_uid BIGINT NOT NULL DEFAULT 0,
                  last_sync_at TIMESTAMPTZ
                );
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.gia_email_templates (
                  id_tpl BIGSERIAL PRIMARY KEY,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  id_marca INTEGER,
                  marca TEXT,
                  kind TEXT NOT NULL,
                  subject_tpl TEXT,
                  body_tpl TEXT NOT NULL,
                  active BOOLEAN NOT NULL DEFAULT TRUE,
                  created_by TEXT
                );
                """
            )
        )
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_gia_email_templates ON public.gia_email_templates(id_marca, kind);"))
        conn.commit()


def _decode_mime_words(v: Any) -> str:
    if v is None:
        return ""
    try:
        parts = decode_header(str(v))
    except Exception:
        return str(v)
    out = ""
    for p, enc in parts:
        if isinstance(p, bytes):
            try:
                out += p.decode(enc or "utf-8", errors="replace")
            except Exception:
                out += p.decode("utf-8", errors="replace")
        else:
            out += str(p)
    return out


def _extract_bodies(msg: Message) -> Tuple[str, str]:
    text_body = ""
    html_body = ""

    def _decode_payload(part: Message) -> str:
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            payload = b""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except Exception:
            return payload.decode("utf-8", errors="replace")

    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype == "text/plain" and not text_body:
                text_body = _decode_payload(part)
            elif ctype == "text/html" and not html_body:
                html_body = _decode_payload(part)
    else:
        ctype = (msg.get_content_type() or "").lower()
        if ctype == "text/plain":
            text_body = _decode_payload(msg)
        elif ctype == "text/html":
            html_body = _decode_payload(msg)

    return (text_body or "").strip(), (html_body or "").strip()


def _extract_reply_to(msg: Message) -> str:
    try:
        rt = str(msg.get("Reply-To") or "").strip()
        if not rt:
            return ""
        _n, em = parseaddr(rt)
        return (em or "").strip().lower()
    except Exception:
        return ""


def _extract_email_from_body(body: str) -> str:
    # Prefer "Email:" / "Correo:" like fields
    v = _find_after_label(body or "", ["Email", "E-mail", "Correo", "Mail"])
    if v:
        m = re.search(r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,})", v, flags=re.I)
        if m:
            return m.group(1).strip().lower()
    # Fallback: first email in body
    m = re.search(r"([A-Z0-9._%+-]+@[A-Z0-9.-]+\\.[A-Z]{2,})", body or "", flags=re.I)
    return (m.group(1).strip().lower() if m else "")


def _extract_phone_from_body(body: str) -> str:
    v = _find_after_label(body or "", ["Telefono", "Teléfono", "Phone", "Celular", "Movil", "Móvil"])
    if v:
        digits = re.sub(r"\\D+", "", v)
        return digits
    # fallback: +56xxxxxxxxx
    m = re.search(r"(\\+?56\\s*\\d[\\d\\s]{7,})", body or "", flags=re.I)
    if not m:
        return ""
    return re.sub(r"\\D+", "", m.group(1))


def _extract_rid(body: str) -> str:
    m = re.search(r"(?im)^\\s*RID\\s*[:=]\\s*([A-Za-z0-9._-]{6,64})\\s*$", body or "")
    return (m.group(1).strip() if m else "")


def _extract_oc(subject: str, body_text: str) -> str:
    s = (subject or "") + "\n" + (body_text or "")
    m = re.search(r"(?i)\\bOC\\s*[:#-]?\\s*([0-9]{3,})\\b", s)
    return (m.group(1).strip() if m else "")


def _extract_cot_nums(body_text: str) -> list[str]:
    t = (body_text or "")
    out: list[str] = []
    for m in re.finditer(r"(?i)\\bcotizaci[oó]n(?:es)?\\s*([0-9]{3,})(?:\\s*y\\s*([0-9]{3,}))?", t):
        out.append(m.group(1))
        if m.group(2):
            out.append(m.group(2))
    if not out and re.search(r"(?i)\\bcotiz", t):
        m2 = re.search(r"(?i)\\b([0-9]{3,})\\s*y\\s*([0-9]{3,})\\b", t)
        if m2:
            out.extend([m2.group(1), m2.group(2)])
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq[:6]


def _looks_like_form(body_text: str) -> bool:
    """
    Heurística: muchos correos de formulario no traen la palabra "FORMULARIO" ni RID,
    pero sí vienen como un set de campos tipo "Nombre: ... / Email: ... / Teléfono: ...".
    """
    t = (body_text or "")
    labels = [
        "Nombre",
        "Nombre y Apellido",
        "Cliente",
        "Email",
        "E-mail",
        "Correo",
        "Teléfono",
        "Telefono",
        "Celular",
        "Comuna",
        "Ciudad",
        "Dirección",
        "Direccion",
        "Fecha",
        "Fecha evento",
        "Marca",
        "Mensaje",
        "Comentario",
        "Notas",
    ]
    hits = 0
    for lab in labels:
        if re.search(rf"(?im)^\\s*{re.escape(lab)}\\s*[:=]\\s*\\S+", t):
            hits += 1
    return hits >= 3


def _classify_email(subject: str, body_text: str) -> tuple[str, str]:
    s = (subject or "").upper()
    b = (body_text or "").upper()
    bb = s + "\n" + b

    if "FORMULARIO" in bb or _extract_rid(body_text) or _looks_like_form(body_text):
        return ("form", "marker/fields")

    oc_kw = ("ORDEN DE COMPRA", "PURCHASE ORDER", "ADJUNTO OC", "ADJUNTAMOS OC")
    if any(k in bb for k in oc_kw) or _extract_oc(subject, body_text) or re.search(r"(?i)\\bOC\\s*[0-9]{3,}\\b", subject or ""):
        return ("purchase", "oc")

    pay_kw = ("TRANSFER", "COMPROB", "PAGO", "ABONO", "DEPÓSITO", "DEPOSITO", "VOUCHER", "TRX", "TRANSACTION")
    if any(k in bb for k in pay_kw):
        return ("payment", "keywords")

    lead_kw = ("COTIZ", "PRESUPUEST", "EVENTO", "CONSULT", "CONTACT", "REQUERIM", "SOLICIT")
    if any(k in bb for k in lead_kw):
        return ("lead", "keywords")

    return ("other", "default")


def _prefer_kind(existing: str | None, new: str, new_reason: str) -> tuple[str, str]:
    """
    Si un mensaje ya fue clasificado, permitimos "subir" a una categoría más específica.
    Precedencia (más fuerte → más débil):
      form > payment > purchase > lead > other
    """
    pr = {"form": 5, "payment": 4, "purchase": 3, "lead": 2, "other": 1, "": 0, None: 0}
    e = (existing or "").strip().lower()
    n = (new or "").strip().lower()
    if not n:
        return (e or "other", new_reason)
    if pr.get(n, 1) > pr.get(e, 1):
        return (n, new_reason)
    return (e or n, new_reason)


def _find_lead_by_rid(conn, rid: str) -> Optional[int]:
    rid = (rid or "").strip()
    if not rid:
        return None
    try:
        r = conn.execute(
            text(
                """
                SELECT id_lead
                FROM public.leads
                WHERE notas ILIKE :pat
                ORDER BY id_lead DESC
                LIMIT 1
                """
            ),
            {"pat": f"%RID: {rid}%"},
        ).scalar()
        return int(r) if r is not None else None
    except Exception:
        return None


def _resolve_marca_id(conn, marca_name: str) -> Optional[int]:
    try:
        rows = conn.execute(text("SELECT id_marca, COALESCE(nombre,marca,'') AS nombre FROM public.marcas")).fetchall()
        want = _norm(marca_name)
        for r in rows:
            nm = _norm(str(r[1] or ""))
            if nm and nm == want:
                return int(r[0])
        return None
    except Exception:
        return None


def _guess_cliente(from_name: str, from_email: str) -> str:
    n = (from_name or "").strip()
    if n:
        return n
    e = (from_email or "").strip()
    if not e:
        return "(Sin nombre)"
    local = e.split("@")[0].strip()
    local = re.sub(r"[._\\-]+", " ", local).strip()
    return local.title() if local else "(Sin nombre)"


def _parse_iso_date_any(s: str) -> Optional[str]:
    s = (s or "").strip()
    if not s:
        return None
    # YYYY-MM-DD
    m = re.search(r"(20\\d{2}-\\d{2}-\\d{2})", s)
    if m:
        return m.group(1)
    # DD/MM/YYYY
    m = re.search(r"(\\d{2})/(\\d{2})/(20\\d{2})", s)
    if m:
        dd, mm, yy = m.group(1), m.group(2), m.group(3)
        return f"{yy}-{mm}-{dd}"
    return None


def _find_after_label(body: str, labels: list[str]) -> str:
    t = (body or "")
    for lab in labels:
        # match: "Label: value"
        m = re.search(rf"(?im)^\\s*{re.escape(lab)}\\s*[:=]\\s*(.+?)\\s*$", t)
        if m:
            v = str(m.group(1) or "").strip()
            if v:
                return v
    return ""


def _resolve_comuna_id(conn, comuna_name: str) -> Optional[int]:
    comuna_name = (comuna_name or "").strip()
    if not comuna_name:
        return None
    try:
        return conn.execute(
            text(
                """
                SELECT id_comuna
                FROM public.comunas
                WHERE UPPER(COALESCE(nombre,comuna)) = UPPER(:n)
                   OR UPPER(COALESCE(nombre,comuna)) LIKE UPPER(:n_like)
                LIMIT 1
                """
            ),
            {"n": comuna_name, "n_like": comuna_name + "%"},
        ).scalar()
    except Exception:
        return None


def _resolve_tipo_cliente_id(conn, body: str) -> Optional[int]:
    t = (body or "").upper()
    is_emp = ("EMPRESA" in t) or ("CORPORAT" in t)
    is_part = ("PARTICULAR" in t) or ("PERSONA" in t) or ("NATURAL" in t)
    if not (is_emp or is_part):
        return None
    try:
        if is_emp:
            hit = conn.execute(
                text("SELECT id_tipo_cliente FROM public.tipos_cliente WHERE UPPER(tipo) LIKE '%EMP%' LIMIT 1")
            ).scalar()
            if hit:
                return int(hit)
        if is_part:
            hit = conn.execute(
                text("SELECT id_tipo_cliente FROM public.tipos_cliente WHERE UPPER(tipo) LIKE '%PART%' LIMIT 1")
            ).scalar()
            if hit:
                return int(hit)
    except Exception:
        return None
    return None


def _find_recent_lead_id(conn, *, id_marca: Optional[int], from_email: str) -> Optional[int]:
    """
    Best-effort dedupe: si ya existe un lead reciente para el mismo correo+marca,
    lo reutilizamos para no llenar el CRM de duplicados.
    """
    fe = (from_email or "").strip().lower()
    if not fe:
        return None
    try:
        if id_marca is None:
            q = text(
                """
                SELECT id_lead
                FROM public.leads
                WHERE LOWER(COALESCE(email,'')) = LOWER(:e)
                  AND created_at >= now() - interval '30 days'
                ORDER BY id_lead DESC
                LIMIT 1
                """
            )
            r = conn.execute(q, {"e": fe}).scalar()
            return int(r) if r is not None else None
        q = text(
            """
            SELECT id_lead
            FROM public.leads
            WHERE LOWER(COALESCE(email,'')) = LOWER(:e)
              AND id_marca = :m
              AND created_at >= now() - interval '30 days'
            ORDER BY id_lead DESC
            LIMIT 1
            """
        )
        r = conn.execute(q, {"e": fe, "m": int(id_marca)}).scalar()
        return int(r) if r is not None else None
    except Exception:
        return None


def _estado_nuevo_id(conn) -> int:
    try:
        x = conn.execute(text("SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE '%NUEVO%' LIMIT 1")).scalar()
        return int(x or 1)
    except Exception:
        return 1


def _create_lead_from_email(
    *,
    conn,
    id_marca: Optional[int],
    marca: str,
    from_name: str,
    from_email: str,
    subject: str,
    body_text: str,
) -> Optional[int]:
    """
    Crea un lead con plataforma=FORMULARIO (el formulario llega por correo).
    Best-effort: si no podemos parsear campos, igual creamos lead para que el ejecutivo lo trabaje.
    """
    try:
        cols = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='leads'
                """
            )
        ).scalars().all()
        cols = set([str(c) for c in cols])
    except Exception:
        cols = set()

    if "cliente" not in cols:
        return None

    cliente = _find_after_label(body_text, ["Nombre", "Cliente", "Nombre cliente", "Nombre y Apellido"]) or _guess_cliente(from_name, from_email)
    telefono = _find_after_label(body_text, ["Telefono", "Teléfono", "Phone", "Celular", "Móvil", "Movil"]) or _extract_phone_from_body(body_text or "")
    comuna = _find_after_label(body_text, ["Comuna", "Ciudad", "Lugar"])
    fecha = _find_after_label(body_text, ["Fecha", "Fecha evento", "Fecha_evento", "Fecha Evento"])
    fecha_iso = _parse_iso_date_any(fecha) or _parse_iso_date_any(body_text)

    id_comuna = _resolve_comuna_id(conn, comuna) if comuna else None
    id_tipo_cliente = _resolve_tipo_cliente_id(conn, body_text)
    estado_id = _estado_nuevo_id(conn)

    # Nota: guardamos el correo original (preview) en notas para trazabilidad.
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    body_prev = (body_text or "").strip()
    if len(body_prev) > 1600:
        body_prev = body_prev[:1600] + "…"
    notas = "\n".join(
        [
            "[GIA][EMAIL] %s" % stamp,
            f"Marca: {marca}",
            f"From: {from_email}",
            f"Asunto: {subject or '(Sin asunto)'}",
            "",
            body_prev,
        ]
    ).strip()

    data = {
        "cliente": cliente,
        "email": from_email or None,
        "telefono": telefono or None,
        "direccion": None,
        "id_marca": int(id_marca) if id_marca is not None and "id_marca" in cols else None,
        "id_estado": int(estado_id) if "id_estado" in cols else None,
        "id_comuna": int(id_comuna) if id_comuna is not None and "id_comuna" in cols else None,
        "id_tipo_cliente": int(id_tipo_cliente) if id_tipo_cliente is not None and "id_tipo_cliente" in cols else None,
        "fecha_evento": fecha_iso if fecha_iso and "fecha_evento" in cols else None,
        "monto_cotizado": 0 if "monto_cotizado" in cols else None,
        "plataforma": ("CORREO" if "plataforma" in cols else None),
        "notas": notas if "notas" in cols else None,
        "created_at": datetime.now(timezone.utc) if "created_at" in cols else None,
        "updated_at": datetime.now(timezone.utc) if "updated_at" in cols else None,
    }
    # filtra None y columnas inexistentes
    data = {k: v for k, v in data.items() if (k in cols and v is not None)}
    if "cliente" not in data:
        data["cliente"] = cliente

    keys = list(data.keys())
    cols_sql = ", ".join(keys)
    vals_sql = ", ".join([f":{k}" for k in keys])
    q = text(f"INSERT INTO public.leads({cols_sql}) VALUES ({vals_sql}) RETURNING id_lead")
    new_id = conn.execute(q, data).scalar()
    return int(new_id) if new_id is not None else None


def _load_accounts() -> List[Dict[str, Any]]:
    def _resolve_path(p: str) -> Path | None:
        s = str(p or "").strip()
        if not s:
            return None
        cand: list[Path] = []
        try:
            cand.append(Path(s).expanduser())
        except Exception:
            pass
        try:
            # repo root (backend/routers/ -> backend -> repo)
            cand.append(Path(__file__).resolve().parents[2] / s)
        except Exception:
            pass
        try:
            cand.append(Path.cwd() / s)
        except Exception:
            pass
        try:
            cand.append(Path.home() / "crm" / s)
        except Exception:
            pass
        for c in cand:
            try:
                if c.is_file():
                    return c
            except Exception:
                continue
        return None

    def _read_accounts_blob() -> str:
        """
        Retorna el JSON de cuentas:
        - preferir `GIA_EMAIL_ACCOUNTS_PATH` (archivo) si existe
        - si no, `GIA_EMAIL_ACCOUNTS_JSON` / `GIA_EMAIL_ACCOUNTS`
        - si no existen en env, intentar leer desde `.env` (hosting Passenger)
        """
        # 1) archivo configurado
        path_raw = (os.getenv("GIA_EMAIL_ACCOUNTS_PATH") or "").strip()
        if not path_raw:
            path_raw = _read_dotenv_value("GIA_EMAIL_ACCOUNTS_PATH")
        p = _resolve_path(path_raw) if path_raw else None
        if p:
            try:
                return p.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                return ""

        # 2) env directo
        raw = (os.getenv("GIA_EMAIL_ACCOUNTS_JSON") or os.getenv("GIA_EMAIL_ACCOUNTS") or "").strip()
        if raw:
            return raw

        # 3) fallback .env
        raw = _read_dotenv_value("GIA_EMAIL_ACCOUNTS_JSON")
        if raw:
            return raw
        return _read_dotenv_value("GIA_EMAIL_ACCOUNTS")

    def _read_dotenv_value(var_name: str) -> str:
        try:
            candidates = []
            try:
                candidates.append(Path(__file__).resolve().parents[2] / ".env")  # /crm/.env
            except Exception:
                pass
            try:
                candidates.append(Path.cwd() / ".env")
            except Exception:
                pass
            try:
                candidates.append(Path.home() / "crm" / ".env")
            except Exception:
                pass

            # Prefer python-dotenv parsing when available (más robusto con líneas largas).
            try:
                from dotenv import dotenv_values  # type: ignore
            except Exception:
                dotenv_values = None  # type: ignore

            for p in candidates:
                try:
                    if not p or not p.exists():
                        continue
                    content = p.read_text(encoding="utf-8", errors="replace")

                    # Robust extractor: soporta valores JSON largos y/o partidos en varias líneas.
                    # Busca "VAR_NAME=" y si empieza con "[" o "{", lee hasta cerrar el JSON.
                    try:
                        m = re.search(rf"(?m)^\\s*{re.escape(var_name)}\\s*=\\s*(.+)$", content)
                        if m:
                            start = m.start(1)
                            # include following lines as needed
                            tail = content[start:].lstrip()
                            if tail and tail[0] in ("'", '"'):
                                q = tail[0]
                                # quoted: take until matching quote on same line (best-effort)
                                qend = tail.find(q, 1)
                                if qend > 1:
                                    return tail[1:qend].strip()
                            if tail and tail[0] in ("[", "{"):
                                open_ch = tail[0]
                                close_ch = "]" if open_ch == "[" else "}"
                                in_str = False
                                esc = False
                                depth = 0
                                for idx, ch in enumerate(tail):
                                    if esc:
                                        esc = False
                                        continue
                                    if ch == "\\":
                                        esc = True
                                        continue
                                    if ch == '"':
                                        in_str = not in_str
                                        continue
                                    if in_str:
                                        continue
                                    if ch == open_ch:
                                        depth += 1
                                    elif ch == close_ch:
                                        depth -= 1
                                        if depth == 0:
                                            return tail[: idx + 1].strip()
                    except Exception:
                        pass

                    if dotenv_values is not None:
                        vals = dotenv_values(str(p))  # type: ignore
                        v = (vals.get(var_name) or "").strip()
                        if v:
                            # si parece JSON incompleto (multi-line), vuelve al extractor robusto de arriba
                            if (v.startswith("[") and not v.rstrip().endswith("]")) or (v.startswith("{") and not v.rstrip().endswith("}")):
                                pass
                            else:
                                return v
                    for line in content.splitlines():
                        s = line.strip()
                        if not s or s.startswith("#"):
                            continue
                        if not s.startswith(var_name + "="):
                            continue
                        v = s.split("=", 1)[1].strip()
                        # allow quoted
                        if (len(v) >= 2) and ((v[0] == v[-1]) and v[0] in ("'", '"')):
                            v = v[1:-1].strip()
                        return v
                except Exception:
                    continue
        except Exception:
            return ""
        return ""

    raw = _read_accounts_blob()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for a in data:
        if not isinstance(a, dict):
            continue
        marca = str(a.get("marca") or "").strip()
        inbox_type = str(a.get("inbox_type") or a.get("tipo") or "sales").strip().lower()
        if inbox_type not in ("sales", "payments"):
            inbox_type = "sales"
        from_email = str(a.get("from_email") or a.get("email") or a.get("username") or "").strip()
        username = str(a.get("username") or from_email or "").strip()
        password = str(a.get("password") or "").strip()
        imap_host = str(a.get("imap_host") or "").strip()
        smtp_host = str(a.get("smtp_host") or "").strip()
        if not (marca and from_email and username and password and imap_host and smtp_host):
            continue
        out.append(
            {
                "marca": marca,
                "inbox_type": inbox_type,
                "from_email": from_email,
                "username": username,
                "password": password,
                "imap_host": imap_host,
                "imap_port": int(a.get("imap_port") or 993),
                "imap_ssl": bool(a.get("imap_ssl", True)),
                "imap_folder": str(a.get("imap_folder") or a.get("folder") or "INBOX").strip() or "INBOX",
                "smtp_host": smtp_host,
                "smtp_port": int(a.get("smtp_port") or 465),
                "smtp_ssl": bool(a.get("smtp_ssl", True)),
            }
        )
    return out


def _filter_accounts_for_user(conn, user: dict, accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if _is_admin(user):
        return accounts
    mids = set(_user_marcas_ids(user))
    if not mids:
        mids = set(_fallback_marcas_ids(conn, user))
    if not mids:
        return []
    out: List[Dict[str, Any]] = []
    for a in accounts:
        mid = _resolve_marca_id(conn, str(a.get("marca") or ""))
        if mid is not None and int(mid) in mids:
            out.append(a)
    return out


def _smtp_send(*, smtp_host: str, smtp_port: int, smtp_ssl: bool, username: str, password: str, from_email: str, to_email: str, subject: str, text_body: str) -> None:
    # Minimal: text/plain
    msg = (
        "From: %s\\r\\n"
        "To: %s\\r\\n"
        "Subject: %s\\r\\n"
        "MIME-Version: 1.0\\r\\n"
        "Content-Type: text/plain; charset=utf-8\\r\\n"
        "\\r\\n"
        "%s"
    ) % (from_email, to_email, subject, text_body)

    context = ssl.create_default_context()
    if smtp_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=25) as s:
            s.login(username, password)
            s.sendmail(from_email, [to_email], msg.encode("utf-8", errors="replace"))
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=25) as s:
            s.starttls(context=context)
            s.login(username, password)
            s.sendmail(from_email, [to_email], msg.encode("utf-8", errors="replace"))


def _imap_connect(*, host: str, port: int, use_ssl: bool) -> imaplib.IMAP4:
    if use_ssl:
        return imaplib.IMAP4_SSL(host, port)
    return imaplib.IMAP4(host, port)


@router.get("/status")
def status(user: dict = Depends(get_current_user)):
    _ensure_schema()
    accounts = _load_accounts()
    with get_connection() as conn:
        allowed = _filter_accounts_for_user(conn, user, accounts)
        items = []
        for a in allowed:
            acc = str(a.get("from_email") or "")
            mid = _resolve_marca_id(conn, str(a.get("marca") or ""))
            st = conn.execute(text("SELECT last_uid, last_sync_at FROM public.gia_email_state WHERE account_email=:a"), {"a": acc}).mappings().first() or {}
            items.append(
                {
                    "marca": a.get("marca"),
                    "id_marca": mid,
                    "account_email": acc,
                    "inbox_type": a.get("inbox_type") or "sales",
                    "imap_folder": a.get("imap_folder") or "INBOX",
                    "imap_host": a.get("imap_host"),
                    "smtp_host": a.get("smtp_host"),
                    "last_uid": int(st.get("last_uid") or 0),
                    "last_sync_at": (str(st.get("last_sync_at")) if st.get("last_sync_at") is not None else None),
                }
            )
        return {"ok": True, "configured": bool(accounts), "items": items}


@router.post("/sync")
def sync(
    id_marca: int | None = Query(default=None, ge=1),
    inbox_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(get_current_user),
):
    """
    Sincroniza IMAP (poll) y envía auto-respuesta 1 vez por correo nuevo.
    Configuración via env `GIA_EMAIL_ACCOUNTS_JSON` (NO se guarda en BD).
    """
    _ensure_schema()
    accounts = _load_accounts()
    if not accounts:
        return {"ok": False, "error": "GIA email no configurado (faltan variables de entorno)."}

    with get_connection() as conn:
        allowed = _filter_accounts_for_user(conn, user, accounts)
        if id_marca:
            allowed = [a for a in allowed if (_resolve_marca_id(conn, str(a.get("marca") or "")) == int(id_marca))]
        if inbox_type:
            it = str(inbox_type or "").strip().lower()
            if it in ("sales", "payments"):
                allowed = [a for a in allowed if str(a.get("inbox_type") or "sales").strip().lower() == it]

        synced = 0
        stored = 0
        ack_sent = 0
        errors: list[str] = []

        for a in allowed:
            try:
                marca = str(a.get("marca") or "").strip()
                mid = _resolve_marca_id(conn, marca)
                account_email = str(a.get("from_email") or "").strip()
                acc_type = str(a.get("inbox_type") or "sales").strip().lower()
                folder = str(a.get("imap_folder") or "INBOX").strip() or "INBOX"
                st = conn.execute(text("SELECT last_uid FROM public.gia_email_state WHERE account_email=:a"), {"a": account_email}).scalar()
                last_uid = int(st or 0)

                im = _imap_connect(host=a["imap_host"], port=int(a["imap_port"]), use_ssl=bool(a["imap_ssl"]))
                im.login(a["username"], a["password"])
                im.select(folder)

                # Strategy: fetch last `limit` UIDs; dedupe in DB.
                typ, data = im.uid("search", None, "ALL")
                if typ != "OK":
                    raise RuntimeError("IMAP search failed")
                uids = [int(x) for x in (data[0].split() if data and data[0] else [])]
                uids.sort()
                if last_uid:
                    uids = [u for u in uids if u > last_uid]
                if limit and len(uids) > limit:
                    uids = uids[-limit:]

                max_uid = last_uid
                for uid in uids:
                    max_uid = max(max_uid, uid)
                    typ2, msg_data = im.uid("fetch", str(uid), "(RFC822)")
                    if typ2 != "OK" or not msg_data or not msg_data[0]:
                        continue
                    raw_bytes = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                    if not raw_bytes:
                        continue
                    msg = BytesParser(policy=default).parsebytes(raw_bytes)
                    msg_id = str(msg.get("Message-ID") or "").strip()

                    from_name, from_email = parseaddr(str(msg.get("From") or ""))
                    from_email = (from_email or "").strip().lower()
                    from_name = _decode_mime_words(from_name).strip()
                    subject = _decode_mime_words(msg.get("Subject")).strip()
                    reply_to_email = _extract_reply_to(msg)

                    received_at = None
                    try:
                        dt_raw = msg.get("Date")
                        if dt_raw:
                            received_at = datetime.now(timezone.utc)
                    except Exception:
                        received_at = None

                    text_body, html_body = _extract_bodies(msg)
                    if not text_body and html_body:
                        text_body = re.sub(r"<[^>]+>", " ", html_body)
                        text_body = re.sub(r"\\s+", " ", text_body).strip()

                    # skip self-sent
                    if from_email and from_email == account_email.lower():
                        continue

                    kind_new, kind_reason_new = _classify_email(subject or "", text_body or "")
                    # Safety: algunos formularios llegan sin el marker; si parecen formulario, no crear lead.
                    if kind_new == "lead" and _looks_like_form(text_body or ""):
                        kind_new, kind_reason_new = ("form", "fields-override")
                    parsed_rid = _extract_rid(text_body or "")
                    parsed_email = _extract_email_from_body(text_body or "")
                    parsed_phone = _extract_phone_from_body(text_body or "")
                    parsed_comuna = _find_after_label(text_body or "", ["Comuna", "Ciudad", "Lugar"]) or ""
                    parsed_fecha_raw = _find_after_label(text_body or "", ["Fecha", "Fecha evento", "Fecha_evento", "Fecha Evento"]) or ""
                    parsed_fecha_iso = _parse_iso_date_any(parsed_fecha_raw) or _parse_iso_date_any(text_body or "")
                    parsed_cliente = _find_after_label(text_body or "", ["Nombre", "Cliente", "Nombre cliente", "Nombre y Apellido"]) or ""

                    parsed_fecha_dt = None
                    if parsed_fecha_iso:
                        try:
                            parsed_fecha_dt = date.fromisoformat(str(parsed_fecha_iso)[:10])
                        except Exception:
                            parsed_fecha_dt = None

                    # upsert minimal
                    try:
                        conn.execute(
                            text(
                                """
                                INSERT INTO public.gia_email_messages(
                                  id_marca, marca, account_email, inbox_type, imap_folder, imap_uid, message_id,
                                  from_email, from_name, subject, received_at, body_text, body_html,
                                  kind, kind_reason, reply_to_email, parsed_email, parsed_phone, parsed_comuna,
                                  parsed_fecha_evento, parsed_cliente, parsed_rid
                                )
                                VALUES (:mid, :marca, :acc, :it, :folder, :uid, :msgid, :fe, :fn, :sub, :ra, :bt, :bh,
                                        :k, :kr, :rt, :pe, :pp, :pc, :pf, :pcli, :prid)
                                ON CONFLICT DO NOTHING
                                """
                            ),
                            {
                                "mid": int(mid) if mid is not None else None,
                                "marca": marca,
                                "acc": account_email,
                                "it": acc_type,
                                "folder": folder,
                                "uid": int(uid),
                                "msgid": msg_id,
                                "fe": from_email or None,
                                "fn": from_name or None,
                                "sub": subject or None,
                                "ra": received_at,
                                "bt": (text_body[:50000] if text_body else None),
                                "bh": (html_body[:200000] if html_body else None),
                                "k": kind_new,
                                "kr": kind_reason_new,
                                "rt": (reply_to_email or None),
                                "pe": (parsed_email or None),
                                "pp": (parsed_phone or None),
                                "pc": (parsed_comuna or None),
                                "pf": parsed_fecha_dt,
                                "pcli": (parsed_cliente or None),
                                "prid": (parsed_rid or None),
                            },
                        )
                        stored += 1
                    except Exception:
                        pass

                    # Completar metadata si el registro ya existía (ON CONFLICT DO NOTHING).
                    try:
                        # mantener/elevar clasificación si ya existe
                        existing_kind = None
                        try:
                            existing_kind = conn.execute(
                                text("SELECT kind FROM public.gia_email_messages WHERE account_email=:acc AND imap_uid=:uid ORDER BY id_msg DESC LIMIT 1"),
                                {"acc": account_email, "uid": int(uid)},
                            ).scalar()
                        except Exception:
                            existing_kind = None
                        kind, kind_reason = _prefer_kind(existing_kind, kind_new, kind_reason_new)

                        conn.execute(
                            text(
                                """
                                UPDATE public.gia_email_messages
                                SET
                                  inbox_type=COALESCE(inbox_type,:it),
                                  imap_folder=COALESCE(imap_folder,:folder),
                                  kind=:k,
                                  kind_reason=:kr,
                                  reply_to_email=COALESCE(reply_to_email,:rt),
                                  parsed_email=COALESCE(parsed_email,:pe),
                                  parsed_phone=COALESCE(parsed_phone,:pp),
                                  parsed_comuna=COALESCE(parsed_comuna,:pc),
                                  parsed_fecha_evento=COALESCE(parsed_fecha_evento,:pf),
                                  parsed_cliente=COALESCE(parsed_cliente,:pcli),
                                  parsed_rid=COALESCE(parsed_rid,:prid),
                                  updated_at=now()
                                WHERE account_email=:acc AND imap_uid=:uid
                                """
                            ),
                            {
                                "it": acc_type,
                                "folder": folder,
                                "k": kind,
                                "kr": kind_reason,
                                "rt": (reply_to_email or None),
                                "pe": (parsed_email or None),
                                "pp": (parsed_phone or None),
                                "pc": (parsed_comuna or None),
                                "pf": parsed_fecha_dt,
                                "pcli": (parsed_cliente or None),
                                "prid": (parsed_rid or None),
                                "acc": account_email,
                                "uid": int(uid),
                            },
                        )
                    except Exception:
                        pass

                    # auto-ack (1 vez)
                    rowm = None
                    try:
                        rowm = conn.execute(
                            text(
                                """
                                SELECT id_msg, ack_sent, lead_id
                                FROM public.gia_email_messages
                                WHERE account_email=:acc AND imap_uid=:uid
                                ORDER BY id_msg DESC
                                LIMIT 1
                                """
                            ),
                            {"acc": account_email, "uid": int(uid)},
                        ).mappings().first()

                        # Vincular con lead existente (sin duplicar):
                        if rowm and rowm.get("id_msg") is not None and rowm.get("lead_id") is None:
                            lead_id = None

                            # Formularios: NO crear duplicados. Solo intentar linkear.
                            if kind == "form":
                                lead_id = _find_lead_by_rid(conn, parsed_rid)

                            contact_email = (reply_to_email or parsed_email or from_email or "").strip().lower()
                            if lead_id is None and contact_email:
                                lead_id = _find_recent_lead_id(conn, id_marca=mid, from_email=contact_email)

                            # Importante: NO creamos el lead automáticamente.
                            # La UI debe pedir confirmación al ejecutivo (botón "Crear lead").

                            if lead_id is not None:
                                conn.execute(
                                    text("UPDATE public.gia_email_messages SET lead_id=:lid, updated_at=now() WHERE id_msg=:id"),
                                    {"lid": int(lead_id), "id": int(rowm.get("id_msg"))},
                                )

                        # Auto-ack: SOLO para formularios (acuse de recibo).
                        if rowm and not bool(rowm.get("ack_sent")) and kind == "form":
                            ack_to = (reply_to_email or parsed_email or from_email or "").strip().lower()
                            if ack_to and ack_to != account_email.lower():
                                cliente_txt = (parsed_cliente or from_name or "").strip() or "PRUEBA SISTEMA"
                                fecha_txt = (str(parsed_fecha_iso)[:10] if parsed_fecha_iso else "").strip()
                                fecha_line = (
                                    f"Para ayudarte mejor, ¿me confirmas la fecha ({fecha_txt}) y la cantidad aproximada de personas?"
                                    if fecha_txt
                                    else "Para ayudarte mejor, ¿me confirmas la fecha y la cantidad aproximada de personas?"
                                )
                                ack_subject = f"Gracias por contactarnos — {marca}"
                                ack_text = (
                                    f"Hola {cliente_txt}, soy del equipo {marca}.\\n\\n"
                                    "¡Gracias por tu contacto! Recibimos tu solicitud y te contactaremos a la brevedad.\\n\\n"
                                    f"{fecha_line}\\n\\n"
                                    "Gracias, quedo atento(a) a tu confirmación.\\n\\n"
                                    "--\\n"
                                    f"{marca} · Green Diamond"
                                )
                                _smtp_send(
                                    smtp_host=a["smtp_host"],
                                    smtp_port=int(a["smtp_port"]),
                                    smtp_ssl=bool(a["smtp_ssl"]),
                                    username=a["username"],
                                    password=a["password"],
                                    from_email=account_email,
                                    to_email=ack_to,
                                    subject=ack_subject,
                                    text_body=ack_text,
                                )
                                conn.execute(
                                    text(
                                        """
                                        UPDATE public.gia_email_messages
                                        SET ack_sent=TRUE, ack_sent_at=now(), ack_error=NULL, updated_at=now()
                                        WHERE id_msg=:id
                                        """
                                    ),
                                    {"id": int(rowm.get("id_msg"))},
                                )
                                ack_sent += 1
                    except Exception as e:
                        try:
                            if rowm and rowm.get("id_msg") is not None:
                                conn.execute(
                                    text("UPDATE public.gia_email_messages SET ack_error=:e, updated_at=now() WHERE id_msg=:id"),
                                    {"e": str(e)[:200], "id": int(rowm.get("id_msg"))},
                                )
                        except Exception:
                            pass

                # state
                conn.execute(
                    text(
                        """
                        INSERT INTO public.gia_email_state(account_email, last_uid, last_sync_at)
                        VALUES (:a, :u, now())
                        ON CONFLICT(account_email) DO UPDATE SET last_uid=:u, last_sync_at=now()
                        """
                    ),
                    {"a": account_email, "u": int(max_uid)},
                )
                conn.commit()
                try:
                    im.logout()
                except Exception:
                    pass
                synced += 1
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                errors.append(f"{a.get('marca')}: {str(e)[:160]}")

        return {"ok": True, "synced": synced, "stored": stored, "ack_sent": ack_sent, "errors": errors}


@router.get("/inbox")
def inbox(
    id_marca: int | None = Query(default=None, ge=1),
    inbox_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(get_current_user),
):
    _ensure_schema()
    with get_connection() as conn:
        where = []
        params: Dict[str, Any] = {"limit": int(limit), "offset": int(offset)}

        if not _is_admin(user):
            mids = _user_marcas_ids(user)
            if not mids:
                return {"ok": True, "total": 0, "items": []}
            where.append("id_marca = ANY(:mids)")
            params["mids"] = mids

        if id_marca:
            where.append("id_marca=:mid")
            params["mid"] = int(id_marca)
        if inbox_type:
            it = str(inbox_type or "").strip().lower()
            if it == "sales":
                where.append("COALESCE(inbox_type,'sales')='sales'")
            elif it == "payments":
                # Compat: si el hosting no tiene casilla separada de pagos,
                # igual filtramos por emails clasificados como payment.
                where.append("(COALESCE(inbox_type,'sales')='payments' OR COALESCE(kind,'')='payment')")
        if q:
            qs = str(q or "").strip()
            if qs:
                where.append("(COALESCE(subject,'') ILIKE :q OR COALESCE(from_email,'') ILIKE :q OR COALESCE(from_name,'') ILIKE :q OR COALESCE(body_text,'') ILIKE :q)")
                params["q"] = f"%{qs}%"

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(text(f"SELECT COUNT(*) FROM public.gia_email_messages {where_sql}"), params).scalar() or 0
        rows = conn.execute(
            text(
                f"""
                SELECT id_msg, created_at, id_marca, marca, account_email,
                       from_email, from_name, subject, received_at,
                       COALESCE(inbox_type,'sales') AS inbox_type,
                       COALESCE(kind,'general') AS kind,
                       COALESCE(ack_sent,false) AS ack_sent,
                       COALESCE(reply_sent,false) AS reply_sent,
                       LEFT(COALESCE(body_text,''), 220) AS preview
                FROM public.gia_email_messages
                {where_sql}
                ORDER BY COALESCE(received_at, created_at) DESC, id_msg DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        ).mappings().all()
        return {"ok": True, "total": int(total), "items": list(rows)}


@router.get("/inbox/{id_msg}")
def inbox_get(id_msg: int, user: dict = Depends(get_current_user)):
    _ensure_schema()
    with get_connection() as conn:
        row = conn.execute(text("SELECT * FROM public.gia_email_messages WHERE id_msg=:id"), {"id": int(id_msg)}).mappings().first()
        if not row:
            raise HTTPException(404, "Mensaje no existe")
        if not _is_admin(user):
            mids = set(_user_marcas_ids(user))
            mid = row.get("id_marca")
            if mid is None or int(mid) not in mids:
                raise HTTPException(403, "Sin permiso")
        msg = dict(row)

        tpl = (
            conn.execute(
                text(
                    """
                    SELECT subject_tpl, body_tpl, active
                    FROM public.gia_email_templates
                    WHERE id_marca=:m AND kind=:k
                    LIMIT 1
                    """
                ),
                {"m": msg.get("id_marca"), "k": str(msg.get("kind") or "").strip()},
            )
            .mappings()
            .first()
            or {}
        )

        def _apply_placeholders(tpl_text: str, ctx: dict) -> str:
            out = tpl_text or ""
            for k, v in ctx.items():
                out = out.replace("{" + str(k) + "}", str(v or ""))
            return out

        try:
            marca = str(msg.get("marca") or "").strip() or "GD"
            cliente = str(msg.get("parsed_cliente") or msg.get("from_name") or "").strip() or "PRUEBA SISTEMA"
            fecha = msg.get("parsed_fecha_evento")
            fecha_txt = str(fecha) if fecha else ""
            kind = str(msg.get("kind") or "").strip().lower()
            body = str(msg.get("body_text") or "")
            subj = str(msg.get("subject") or "")

            oc_num = _extract_oc(subj, body)
            cot_nums = _extract_cot_nums(body)
            cot_line = ""
            if cot_nums:
                cot_line = "Cotización: " + (" y ".join(cot_nums[:2]) if len(cot_nums) <= 2 else ", ".join(cot_nums))

            has_date = bool(_parse_iso_date_any(body) or fecha_txt)
            has_time = bool(re.search(r"\\b\\d{1,2}:\\d{2}\\b", body))
            has_comuna = bool(_find_after_label(body, ["Comuna", "Ciudad", "Lugar"]))
            has_qty = bool(re.search(r"(?i)\\b(\\d{2,5})\\s*(personas|pax|asistentes)\\b", body))

            missing = []
            if not has_date:
                missing.append("Fecha del evento")
            if not has_comuna:
                missing.append("Comuna")
            if not has_qty:
                missing.append("Cantidad aproximada de personas")
            if not has_time:
                missing.append("Horario (inicio/fin)")

            ctx = {
                "cliente": cliente,
                "marca": marca,
                "fecha": fecha_txt,
                "oc": oc_num,
                "cot": (", ".join(cot_nums) if cot_nums else ""),
                "subject": subj,
            }

            if kind == "payment":
                draft = (
                    f"Hola {cliente}, soy del equipo {marca}.\n\n"
                    "Gracias por tu mensaje. Ya estamos revisando el pago/transferencia y te confirmaremos a la brevedad.\n\n"
                    "Saludos.\n"
                )
            elif kind == "purchase":
                oc_txt = f" OC {oc_num}" if oc_num else ""
                draft = (
                    f"Hola {cliente}, soy del equipo {marca}.\n\n"
                    f"¡Gracias! Confirmo recepción de la{oc_txt}.\n"
                    + (f"{cot_line}\n" if cot_line else "")
                    + "\n"
                    "Vamos a coordinar el servicio según lo indicado.\n\n"
                    "Si necesitas factura, por favor envíame:\n"
                    "- Razón social / RUT / Giro\n"
                    "- Dirección de facturación\n"
                    "- OC (si aplica)\n\n"
                    "Quedo atento(a).\n"
                )
            elif kind == "form":
                fecha_line = (
                    f"Para ayudarte mejor, ¿me confirmas la fecha ({fecha_txt}) y la cantidad aproximada de personas?"
                    if fecha_txt
                    else "Para ayudarte mejor, ¿me confirmas la fecha y la cantidad aproximada de personas?"
                )
                draft = (
                    f"Hola {cliente}, soy del equipo {marca}.\n\n"
                    "¡Gracias por tu contacto! Recibimos tu solicitud y te contactaremos a la brevedad.\n\n"
                    f"{fecha_line}\n\n"
                    "Gracias, quedo atento(a) a tu confirmación.\n"
                )
            else:
                if missing:
                    missing_lines = "\\n".join([f"- {x}" for x in missing])
                    draft = (
                        f"Hola {cliente}, soy del equipo {marca}.\\n\\n"
                        "¡Gracias por tu contacto! Para ayudarte mejor, ¿me confirmas estos datos?\\n"
                        f"{missing_lines}\\n\\n"
                        "Gracias, quedo atento(a).\\n"
                    )
                else:
                    draft = (
                        f"Hola {cliente}, soy del equipo {marca}.\\n\\n"
                        "¡Gracias por tu correo! Confirmo recepción y quedo atento(a) por cualquier ajuste.\\n\\n"
                        "Saludos.\\n"
                    )

            if tpl and bool(tpl.get("active")) and str(tpl.get("body_tpl") or "").strip():
                draft = _apply_placeholders(str(tpl.get("body_tpl") or ""), ctx).strip() + "\\n"

            msg["draft_text"] = draft
            msg["template_active"] = bool(tpl.get("active")) if tpl else False
            msg["suggest_create_lead"] = (kind in ("lead", "purchase") and msg.get("lead_id") is None)
        except Exception:
            msg["draft_text"] = ""
            msg["template_active"] = False
            msg["suggest_create_lead"] = False
        return {"ok": True, "message": msg}


@router.post("/inbox/{id_msg}/create_lead")
def create_lead_from_inbox(id_msg: int, user: dict = Depends(get_current_user)):
    """
    Crea un lead a partir de un correo (solo cuando el ejecutivo lo confirma).
    No duplica: si el mensaje ya tiene lead_id, lo devuelve.
    """
    _ensure_schema()
    with get_connection() as conn:
        row = conn.execute(text("SELECT * FROM public.gia_email_messages WHERE id_msg=:id"), {"id": int(id_msg)}).mappings().first()
        if not row:
            raise HTTPException(404, "Mensaje no existe")
        if not _is_admin(user):
            mids = set(_user_marcas_ids(user))
            mid = row.get("id_marca")
            if mid is None or int(mid) not in mids:
                raise HTTPException(403, "Sin permiso")

        if row.get("lead_id") is not None:
            return {"ok": True, "id_lead": int(row.get("lead_id"))}

        kind = str(row.get("kind") or "").strip().lower()
        if kind in ("form", "payment"):
            raise HTTPException(400, "Este correo no se puede convertir automáticamente en lead (form/pago).")

        mid = row.get("id_marca")
        marca = str(row.get("marca") or "").strip()
        contact_email = str(row.get("reply_to_email") or row.get("parsed_email") or row.get("from_email") or "").strip().lower()
        if not contact_email:
            raise HTTPException(400, "No pude detectar email de contacto")

        lead_id = _create_lead_from_email(
            conn=conn,
            id_marca=int(mid) if mid is not None else None,
            marca=marca,
            from_name=str(row.get("parsed_cliente") or row.get("from_name") or ""),
            from_email=contact_email,
            subject=str(row.get("subject") or ""),
            body_text=str(row.get("body_text") or ""),
        )
        if lead_id is None:
            raise HTTPException(500, "No pude crear el lead (verifica schema de leads).")
        conn.execute(text("UPDATE public.gia_email_messages SET lead_id=:lid, updated_at=now() WHERE id_msg=:id"), {"lid": int(lead_id), "id": int(id_msg)})
        conn.commit()
        return {"ok": True, "id_lead": int(lead_id)}


@router.post("/inbox/{id_msg}/reply")
def reply(
    id_msg: int,
    payload: dict = Body(...),
    user: dict = Depends(get_current_user),
):
    """
    Responder manualmente desde CRM (por ahora).
    """
    # compat: /reply es alias de /send (sin aprendizaje automático configurable desde UI vieja)
    payload2 = dict(payload or {})
    if "learn" not in payload2:
        payload2["learn"] = True
    return send(id_msg=id_msg, payload=payload2, user=user)


@router.post("/inbox/{id_msg}/send")
def send(
    id_msg: int,
    payload: dict = Body(...),
    user: dict = Depends(get_current_user),
):
    """
    Envía una respuesta y marca el mensaje como respondido.
    Además, opcionalmente aprende (guarda como plantilla activa por marca+kind).
    payload: {text, subject?, learn?}
    """
    text_body = str(payload.get("text") or payload.get("text_body") or "").strip()
    if not text_body:
        raise HTTPException(400, "text requerido")
    learn = bool(payload.get("learn", True))
    subject_override = str(payload.get("subject") or "").strip()

    _ensure_schema()
    accounts = _load_accounts()
    with get_connection() as conn:
        msg = conn.execute(text("SELECT * FROM public.gia_email_messages WHERE id_msg=:id"), {"id": int(id_msg)}).mappings().first()
        if not msg:
            raise HTTPException(404, "Mensaje no existe")
        if not _is_admin(user):
            mids = set(_user_marcas_ids(user))
            mid = msg.get("id_marca")
            if mid is None or int(mid) not in mids:
                raise HTTPException(403, "Sin permiso")

        acc_email = str(msg.get("account_email") or "").strip().lower()
        msg_inbox_type = str(msg.get("inbox_type") or "sales").strip().lower()
        to_email = str(msg.get("reply_to_email") or msg.get("from_email") or "").strip()
        if not to_email:
            raise HTTPException(400, "Mensaje sin from_email")

        # Find account config by from_email (+ inbox_type si existe)
        acc = next(
            (
                a
                for a in accounts
                if str(a.get("from_email") or "").strip().lower() == acc_email
                and str(a.get("inbox_type") or "sales").strip().lower() == msg_inbox_type
            ),
            None,
        )
        if not acc:
            # compat: si no hay match por inbox_type, intenta solo por email
            acc = next((a for a in accounts if str(a.get("from_email") or "").strip().lower() == acc_email), None)
        if not acc:
            raise HTTPException(400, "Cuenta no configurada en servidor (.env / accounts file)")

        subj_in = subject_override or (str(msg.get("subject") or "").strip() or "Contacto")
        subject = subj_in if subj_in.lower().startswith("re:") else ("Re: " + subj_in)
        try:
            _smtp_send(
                smtp_host=acc["smtp_host"],
                smtp_port=int(acc["smtp_port"]),
                smtp_ssl=bool(acc["smtp_ssl"]),
                username=acc["username"],
                password=acc["password"],
                from_email=str(acc.get("from_email") or acc.get("username") or ""),
                to_email=to_email,
                subject=subject,
                text_body=text_body,
            )
        except Exception as e:
            try:
                conn.execute(
                    text("UPDATE public.gia_email_messages SET reply_error=:e, updated_at=now() WHERE id_msg=:id"),
                    {"e": str(e)[:240], "id": int(id_msg)},
                )
                conn.commit()
            except Exception:
                pass
            raise HTTPException(500, detail=f"No pude enviar correo: {e}")

        try:
            conn.execute(
                text(
                    """
                    UPDATE public.gia_email_messages
                    SET reply_sent=TRUE, reply_sent_at=now(), reply_error=NULL, reply_body=:b, updated_at=now()
                    WHERE id_msg=:id
                    """
                ),
                {"b": text_body[:200000], "id": int(id_msg)},
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass

        # aprendizaje simple: guardar plantilla activa por marca+kind
        if learn:
            try:
                mid = msg.get("id_marca")
                kind = str(msg.get("kind") or "other").strip().lower() or "other"
                if mid is not None and kind not in ("other", ""):
                    marca = conn.execute(text("SELECT COALESCE(nombre,marca,'') FROM public.marcas WHERE id_marca=:m"), {"m": int(mid)}).scalar()
                    conn.execute(
                        text(
                            """
                            INSERT INTO public.gia_email_templates(id_marca, marca, kind, subject_tpl, body_tpl, active, created_by)
                            VALUES (:m, :marca, :k, :s, :b, TRUE, :by)
                            ON CONFLICT(id_marca, kind)
                            DO UPDATE SET subject_tpl=:s, body_tpl=:b, active=TRUE, updated_at=now(), created_by=:by
                            """
                        ),
                        {
                            "m": int(mid),
                            "marca": str(marca or ""),
                            "k": kind,
                            "s": None,
                            "b": text_body,
                            "by": str(user.get("username") or ""),
                        },
                    )
                    conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

        return {"ok": True}


@router.post("/compose/send")
def compose_send(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    """
    Enviar un correo nuevo (composer) desde el CRM y opcionalmente "aprender" como plantilla.
    payload:
      - id_marca (int) requerido
      - inbox_type ("sales"|"payments") opcional (default "sales")
      - to_email requerido
      - subject requerido
      - text requerido
      - learn (bool) opcional default True
      - kind (lead|purchase|payment|form|other) opcional (default other) para aprendizaje
    """
    _ensure_schema()
    id_marca = payload.get("id_marca")
    if not id_marca:
        raise HTTPException(400, "id_marca requerido")
    try:
        id_marca_int = int(id_marca)
    except Exception:
        raise HTTPException(400, "id_marca inválido")

    inbox_type = str(payload.get("inbox_type") or "sales").strip().lower()
    if inbox_type not in ("sales", "payments"):
        inbox_type = "sales"

    to_email = str(payload.get("to_email") or "").strip()
    subject = str(payload.get("subject") or "").strip()
    text_body = str(payload.get("text") or payload.get("text_body") or "").strip()
    learn = bool(payload.get("learn", True))
    kind = str(payload.get("kind") or "other").strip().lower()
    if kind not in ("lead", "purchase", "payment", "form", "other"):
        kind = "other"

    if not to_email or "@" not in to_email:
        raise HTTPException(400, "to_email inválido")
    if not subject:
        raise HTTPException(400, "subject requerido")
    if not text_body:
        raise HTTPException(400, "text requerido")

    accounts = _load_accounts()
    if not accounts:
        raise HTTPException(400, "Cuenta no configurada en servidor (.env / accounts file)")

    with get_connection() as conn:
        allowed = _filter_accounts_for_user(conn, user, accounts)
        if not allowed:
            raise HTTPException(403, "Sin permiso")

        # elegir cuenta por id_marca + inbox_type
        def _acc_mid(a: dict) -> int | None:
            try:
                return _resolve_marca_id(conn, str(a.get("marca") or ""))
            except Exception:
                return None

        cand = [a for a in allowed if (_acc_mid(a) == id_marca_int and str(a.get("inbox_type") or "sales") == inbox_type)]
        if not cand and inbox_type == "payments":
            # fallback: si no hay casilla de pagos separada, usar sales
            cand = [a for a in allowed if (_acc_mid(a) == id_marca_int and str(a.get("inbox_type") or "sales") == "sales")]
        acc = cand[0] if cand else None
        if not acc:
            raise HTTPException(403, "No hay cuenta configurada para esa marca")

        try:
            _smtp_send(
                smtp_host=acc["smtp_host"],
                smtp_port=int(acc["smtp_port"]),
                smtp_ssl=bool(acc["smtp_ssl"]),
                username=acc["username"],
                password=acc["password"],
                from_email=str(acc.get("from_email") or acc.get("username") or ""),
                to_email=to_email,
                subject=subject,
                text_body=text_body,
            )
        except Exception as e:
            raise HTTPException(500, detail=f"No pude enviar correo: {e}")

        if learn and kind not in ("other", ""):
            try:
                marca = conn.execute(text("SELECT COALESCE(nombre,marca,'') FROM public.marcas WHERE id_marca=:m"), {"m": int(id_marca_int)}).scalar()
                conn.execute(
                    text(
                        """
                        INSERT INTO public.gia_email_templates(id_marca, marca, kind, subject_tpl, body_tpl, active, created_by)
                        VALUES (:m, :marca, :k, NULL, :b, TRUE, :by)
                        ON CONFLICT(id_marca, kind)
                        DO UPDATE SET body_tpl=:b, active=TRUE, updated_at=now(), created_by=:by
                        """
                    ),
                    {
                        "m": int(id_marca_int),
                        "marca": str(marca or ""),
                        "k": kind,
                        "b": text_body,
                        "by": str(user.get("username") or ""),
                    },
                )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

        return {"ok": True}


@router.put("/templates")
def templates_upsert(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    """
    Guarda plantilla por marca + kind (admin).
    payload: {id_marca, kind, body_tpl, subject_tpl?, active?}
    """
    if not _is_admin(user):
        raise HTTPException(403, "Solo admin puede guardar plantillas")
    id_marca = payload.get("id_marca")
    kind = str(payload.get("kind") or "").strip()
    body_tpl = str(payload.get("body_tpl") or "").strip()
    subject_tpl = str(payload.get("subject_tpl") or "").strip() or None
    active = bool(payload.get("active", True))
    if not id_marca or not kind or not body_tpl:
        raise HTTPException(400, "id_marca, kind y body_tpl son requeridos")
    _ensure_schema()
    with get_connection() as conn:
        marca = conn.execute(text("SELECT COALESCE(nombre,marca,'') FROM public.marcas WHERE id_marca=:m"), {"m": int(id_marca)}).scalar()
        conn.execute(
            text(
                """
                INSERT INTO public.gia_email_templates(id_marca, marca, kind, subject_tpl, body_tpl, active, created_by)
                VALUES (:m, :marca, :k, :s, :b, :a, :by)
                ON CONFLICT(id_marca, kind)
                DO UPDATE SET subject_tpl=:s, body_tpl=:b, active=:a, updated_at=now(), created_by=:by
                """
            ),
            {
                "m": int(id_marca),
                "marca": str(marca or ""),
                "k": kind,
                "s": subject_tpl,
                "b": body_tpl,
                "a": active,
                "by": str(user.get("username") or ""),
            },
        )
        conn.commit()
    return {"ok": True}
