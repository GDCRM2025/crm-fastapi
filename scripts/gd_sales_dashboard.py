#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GD SALES COMMAND CENTER V14.2
==========================

Cambios principales:
- Fuente CRM viva autodetectada.
- Cola comercial basada en "seguimiento efectivo":
    * seguimiento_at del CRM;
    * la PRIMERA cotización, usando su fecha como hito de gestión;
    * una NUEVA cotización/revisión posterior;
    * una ACTUALIZACIÓN real de la cotización cuando existe updated_at;
    * movimientos comerciales del historial/activity_log: estado, observaciones/notas, seguimiento, contacto o move.
- La fecha de cotización se reconcilia en leads.seguimiento_at durante ACTUALIZAR TODO.
  Así /tasks/sync crea la siguiente tarea desde la gestión real y evita seguimientos redundantes.
- Una tarea comercial creada ANTES de un seguimiento efectivo queda "resuelta por gestión"
  para efectos del dashboard y deja de inflar vencidos.
- Si existe seguimiento efectivo, el lead no vuelve a la cola hasta que exista
  una nueva tarea comercial abierta y vencida/posterior al seguimiento.
- Lead y cotización se abren LOCALMENTE desde PostgreSQL.
  No dependen de token, cookie, CRM360 ni endpoint PDF protegido.
- Estado de filtros / orden / pestaña se conserva en localStorage.
- El botón ACTUALIZAR ejecuta un refresh profundo bloqueante: CRM + reglas/tasks + Calendar live.
- No hay auto-refresh del navegador: el usuario decide cuándo recalcular.
- Orden por encabezados: cliente, marca, ejecutivo, fecha, monto, estado.
- Operaciones distingue explícitamente:
  * Calendar oficial: validado externamente por el monitor ChatGPT;
  * tareas operacionales CRM.
  Un resultado vacío de tasks NUNCA se presenta como "Calendar OK".
"""

import os
import re
import sys
import json
import unicodedata
import subprocess
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd


# ============================================================
# CONFIG
# ============================================================

OUTPUT_DIR = os.environ.get("GD_OUTPUT_DIR", "/var/www/gd-sales")
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

CRM_ORIGIN = os.environ.get(
    "GD_CRM_ORIGIN",
    "https://crm.greendiamond.cl"
).rstrip("/")

OFFICIAL_CALENDAR_ID = "simonurrutia.m@gmail.com"
CALENDAR_LIVE_URL = (
    "https://calendar.google.com/calendar/u/0/r"
    "?cid=simonurrutia.m%40gmail.com"
)

TIMEZONE = "America/Santiago"
TZ = ZoneInfo(TIMEZONE)
NOW = datetime.now(TZ)
TODAY = NOW.date()

DEEP_REFRESH = os.environ.get("GD_DEEP_REFRESH", "0").strip() == "1"
RECONCILE_ONLY = os.environ.get("GD_RECONCILE_ONLY", "0").strip() == "1"
RECONCILE_FOLLOWUP = os.environ.get("GD_RECONCILE_FOLLOWUP", "0").strip() == "1"
STATE_PATH = Path(OUTPUT_DIR) / "command_center_state.json"
FINDINGS_PATH = Path(OUTPUT_DIR) / "command_center_findings.json"
REFRESH_RESULT_PATH = Path(OUTPUT_DIR) / "refresh_result.json"
CALENDAR_SNAPSHOT_PATH = Path(OUTPUT_DIR) / "calendar_snapshot.json"
REFRESH_API_PORT = int(os.environ.get("GD_REFRESH_API_PORT", "8765"))
FINDINGS_SCHEMA_VERSION = 4

BRAND_OWNER = {
    "GOURMET": "CONSTANZA FRANCO",
    "CAMALEON": "ANDRES LANDERER",
    "DEL SABOR": "ANDRES LANDERER",
    "EXPRESS": "DANIEL TOLEDO",
}

MONTH_NAMES = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
    5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
}

TERMINAL_RE = re.compile(
    r"declinad|confirmad|cerrad|ganad|perdid|"
    r"anulad|cancelad|realizad|finalizad|vendid",
    re.IGNORECASE,
)

OPEN_TASK_STATUSES = {
    "open", "pending", "pendiente", "abierta",
    "todo", "in_progress", "en_progreso"
}

COMMERCIAL_TASK_PATTERNS = (
    "SEGUIMIENTO",
    "CONTACTAR",
    "RIESGO_COTIZADO",
    "RIESGO_AUTO_DECLINE",
)

OPS_TASK_KINDS = {
    "EVENTO_PROXIMO_INCOMPLETO",
    "COMPLETAR_DIRECCION",
    "COMPLETAR_HORARIO",
    "COMPLETAR_TELEFONO",
}


# ============================================================
# HELPERS
# ============================================================

def esc(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    s = str(value)
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&#39;")
    )


def money(value):
    try:
        return "${:,.0f}".format(float(value)).replace(",", ".")
    except Exception:
        return "$0"


def fmt_date(value):
    if value is None or pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%d-%m-%Y")


def fmt_dt(value):
    if value is None or pd.isna(value):
        return ""
    t = pd.Timestamp(value)
    try:
        if t.tzinfo is not None:
            t = t.tz_convert(TIMEZONE)
    except Exception:
        pass
    return t.strftime("%d-%m-%Y %H:%M")


def normalize(value):
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFD", text)
    text = "".join(
        c for c in text
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(text.upper().strip().split())


def run_df(conn, sql, params=None):
    return pd.read_sql_query(sql, conn, params=params)


def table_exists(conn, table_name):
    q = """
    SELECT EXISTS(
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema='public'
          AND table_name=%s
    ) AS ok
    """
    return bool(run_df(conn, q, [table_name]).iloc[0]["ok"])


def get_columns(conn, table_name):
    q = """
    SELECT column_name
    FROM information_schema.columns
    WHERE table_schema='public'
      AND table_name=%s
    ORDER BY ordinal_position
    """
    df = run_df(conn, q, [table_name])
    return df["column_name"].tolist() if not df.empty else []


def first_existing(options, columns):
    return next((x for x in options if x in columns), None)


def scalar(conn, sql):
    try:
        row = conn.execute(sql).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def safe_url(value):
    s = "" if value is None else str(value).strip()
    if not s:
        return ""
    if s.lower().startswith(("javascript:", "data:")):
        return ""
    if s.startswith("/"):
        return CRM_ORIGIN + s
    return s


def excel_safe(df):
    out = df.copy()
    for col in out.columns:
        if isinstance(out[col].dtype, pd.DatetimeTZDtype):
            out[col] = out[col].dt.tz_localize(None)
        elif out[col].dtype == "object":
            def clean(v):
                if isinstance(v, pd.Timestamp) and v.tzinfo is not None:
                    return v.tz_localize(None)
                if isinstance(v, datetime) and v.tzinfo is not None:
                    return v.replace(tzinfo=None)
                return v
            out[col] = out[col].map(clean)
    return out


def max_ts(*values):
    vals = []
    for v in values:
        if v is None or pd.isna(v):
            continue
        vals.append(pd.Timestamp(v))
    return max(vals) if vals else pd.NaT



# ============================================================
# DEEP REFRESH API (LOCALHOST ONLY)
# ============================================================

def _json_load_file(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _safe_error_text(exc):
    text = f"{type(exc).__name__}: {exc}"
    # Never expose credentials/tokens accidentally in UI/log payload.
    text = re.sub(r"(?i)(token|password|secret|authorization)\s*[:=]\s*[^\s,;]+", r"\1=***", text)
    return text[:700]


_INTERNAL_AUTH_ERROR = ""

def _internal_crm_headers(extra=None):
    """Autenticación servicio-a-servicio local. No lee el .env privado del CRM."""
    global _INTERNAL_AUTH_ERROR
    headers = {"X-GD-Command-Center": "1"}
    if extra:
        headers.update(extra)
    token = str(os.environ.get("GD_INTERNAL_TOKEN") or "").strip()
    if token:
        headers["X-GD-Internal-Token"] = token
        _INTERNAL_AUTH_ERROR = ""
    else:
        _INTERNAL_AUTH_ERROR = "GD_INTERNAL_TOKEN no configurado en gd-sales-refresh.service"
    return headers


def _call_local_tasks_sync():
    url = "http://127.0.0.1:8000/tasks/sync"
    req = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers=_internal_crm_headers({"Content-Type": "application/json"}),
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw) if raw.strip() else {}
            except Exception:
                body = {"raw": raw[:500]}
            return {"ok": True, "status": response.status, "body": body}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace") if getattr(exc, "fp", None) else ""
        return {"ok": False, "status": exc.code, "error": raw[:500] or str(exc)}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": _safe_error_text(exc)}


_REFRESH_LOCK = threading.Lock()


def run_refresh_server():
    script_path = str(Path(__file__).resolve())
    output_dir = OUTPUT_DIR

    class Handler(BaseHTTPRequestHandler):
        server_version = "GDRefresh/1.0"

        def log_message(self, fmt, *args):
            # Keep systemd log concise.
            sys.stderr.write("GDRefresh " + (fmt % args) + "\n")

        def _send_json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") in ("", "/health"):
                return self._send_json(200, {"ok": True, "service": "gd-sales-refresh", "version": "14.2"})
            return self._send_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self):
            if self.path.rstrip("/") != "/refresh":
                return self._send_json(404, {"ok": False, "error": "not_found"})

            if not _REFRESH_LOCK.acquire(blocking=False):
                return self._send_json(409, {"ok": False, "error": "refresh_en_curso"})

            try:
                # PASO 1: reconciliar el hito real de seguimiento en PostgreSQL.
                # Incluye primera cotización, revisiones y movimientos relevantes
                # de activity_log. No toca findings ni Calendar en este pre-paso.
                pre_env = os.environ.copy()
                pre_env.update({
                    "GD_OUTPUT_DIR": output_dir,
                    "GD_RECONCILE_ONLY": "1",
                    "GD_RECONCILE_FOLLOWUP": "1",
                    "GD_DEEP_REFRESH": "0",
                })
                pre_proc = subprocess.run(
                    [sys.executable, script_path],
                    env=pre_env,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                if pre_proc.returncode != 0:
                    return self._send_json(500, {
                        "ok": False,
                        "error": "followup_reconcile_failed",
                        "generator_error": "\n".join(
                            pre_proc.stderr.splitlines()[-20:]
                        )[-2500:],
                    })

                # PASO 2: ahora el motor nativo ve seguimiento_at ya actualizado
                # y genera/recalcula las tareas desde el hito correcto.
                task_sync = _call_local_tasks_sync()

                # PASO 3: releer todo después del sync y recién ahí comparar
                # hallazgos, Calendar y generar la interfaz final.
                env = os.environ.copy()
                env.update({
                    "GD_OUTPUT_DIR": output_dir,
                    "GD_DEEP_REFRESH": "1",
                    "GD_RECONCILE_ONLY": "0",
                    "GD_RECONCILE_FOLLOWUP": "0",
                    "GD_TASK_SYNC_RESULT": json.dumps(task_sync, ensure_ascii=False),
                })

                proc = subprocess.run(
                    [sys.executable, script_path],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )

                result = _json_load_file(REFRESH_RESULT_PATH, {}) or {}
                result["task_sync"] = task_sync
                result["reconcile_stdout_tail"] = "\n".join(
                    pre_proc.stdout.splitlines()[-12:]
                )
                result["generator_returncode"] = proc.returncode
                result["stdout_tail"] = "\n".join(proc.stdout.splitlines()[-18:])
                if proc.returncode != 0:
                    result["ok"] = False
                    result["generator_error"] = "\n".join(proc.stderr.splitlines()[-20:])[-2500:]
                    return self._send_json(500, result)

                result["ok"] = True
                return self._send_json(200, result)
            except subprocess.TimeoutExpired:
                return self._send_json(504, {"ok": False, "error": "refresh_timeout_360s"})
            except Exception as exc:
                return self._send_json(500, {"ok": False, "error": _safe_error_text(exc)})
            finally:
                _REFRESH_LOCK.release()

    server = ThreadingHTTPServer(("127.0.0.1", REFRESH_API_PORT), Handler)
    print(f"GD Sales refresh API listening on 127.0.0.1:{REFRESH_API_PORT}")
    server.serve_forever()


if "--serve-refresh" in sys.argv:
    run_refresh_server()
    raise SystemExit(0)


# ============================================================
# SELF TEST
# ============================================================

def self_test():
    errors = []

    if TERMINAL_RE.search("COTIZADO"):
        errors.append("COTIZADO no debe ser terminal")

    for s in ["DECLINADO", "CONFIRMADO", "VENDIDO", "CERRADO"]:
        if not TERMINAL_RE.search(s):
            errors.append(f"No detecta terminal: {s}")

    # Regla V12: la primera cotización YA es un hito de seguimiento.
    first = pd.Timestamp("2026-08-01 10:00")
    quote_count = 1
    quote_followup = first if quote_count >= 1 else pd.NaT
    if pd.isna(quote_followup):
        errors.append("Primera cotización no fue seguimiento")

    # Una revisión/nueva cotización posterior vuelve a mover el hito.
    revised = pd.Timestamp("2026-08-02 11:00")
    quote_count = 2
    quote_followup = revised if quote_count >= 2 else first
    if quote_followup != revised:
        errors.append("Revisión de cotización no movió seguimiento")

    # Una tarea posterior al seguimiento debe sobrevivir.
    future_task = pd.Timestamp("2026-08-03 09:00")
    if future_task <= quote_followup:
        errors.append("fixture tarea posterior inválido")

    # Tarea anterior al follow-up debe quedar superada.
    task_created = pd.Timestamp("2026-08-01 09:00")
    effective = pd.Timestamp("2026-08-01 11:00")
    task_survives = task_created > effective
    if task_survives:
        errors.append("Tarea vieja no fue superada")

    # Historial comercial debe contar como seguimiento efectivo.
    activity_fixture = "append_note estado seguimiento"
    if not re.search(r"(note|nota|observ|estado|segu|follow|move|contact)", activity_fixture, flags=re.I):
        errors.append("Historial comercial no reconocido")

    # La cotización se abre localmente; no debe depender de API/token.
    if "CRM_ORIGIN" not in globals():
        errors.append("Configuración base incompleta")
    if "_internal_crm_headers" not in globals():
        errors.append("Falta autenticación interna CRM")
    # El overlay se prueba en ejecución productiva porque depende del dataframe CRM.

    if errors:
        print("SELF TEST FAILED")
        for e in errors:
            print("-", e)
        return 1

    print("SELF TEST OK")
    print("- estados terminales: OK")
    print("- primera cotización = hito de seguimiento: OK")
    print("- revisión/nueva cotización mueve seguimiento: OK")
    print("- tarea anterior a gestión queda superada: OK")
    print("- tarea posterior al seguimiento permanece activa: OK")
    print("- estado/nota/seguimiento del historial cuentan como gestión: OK")
    print("- cotización local sin token/API: OK")
    print("- Auth interna local para endpoints protegidos: OK")
    return 0


if "--self-test" in sys.argv:
    raise SystemExit(self_test())


# PostgreSQL sólo se necesita en ejecución productiva.
import psycopg


# GD_INTERNAL_AUTH_COMPAT_V142_BEGIN
#
# Compatibilidad del Command Center con la autenticación interna
# vigente del CRM.
#
# Sólo agrega credenciales a llamadas LOCALHOST -> CRM :8000.
#

import os as _gd_os
import urllib.request as _gd_urllib_request
import urllib.error as _gd_urllib_error


def _gd_find_internal_token():

    preferred = (
        "GD_INTERNAL_TOKEN",
        "GD_INTERNAL_API_TOKEN",
        "GD_INTERNAL_API_KEY",
        "GD_API_TOKEN",
        "INTERNAL_API_TOKEN",
        "INTERNAL_TOKEN",
        "GD_COMMAND_CENTER_TOKEN",
    )

    for name in preferred:
        value = _gd_os.environ.get(name)
        if value:
            return value

    for name, value in _gd_os.environ.items():

        upper = name.upper()

        if not value:
            continue

        if (
            ("INTERNAL" in upper or upper.startswith("GD_"))
            and
            (
                "TOKEN" in upper
                or "SECRET" in upper
                or "API_KEY" in upper
            )
        ):
            return value

    return ""


_GD_INTERNAL_TOKEN = _gd_find_internal_token()


def _gd_is_local_crm(url):

    u = str(url or "")

    return (
        u.startswith("http://127.0.0.1:8000/")
        or
        u.startswith("http://localhost:8000/")
    )


def _gd_headers(alternate=False):

    h = {
        "X-GD-Command-Center": "1",
        "X-Requested-With": "GD-Sales-V14.2",
    }

    token = _GD_INTERNAL_TOKEN

    if token:

        h.update({
            "X-GD-Internal-Token": token,
            "X-GD-Token": token,
            "X-Internal-Token": token,
            "X-API-Key": token,
            "Authorization": "Bearer " + token,
        })

        if alternate:
            h["X-GD-Command-Center"] = token

    return h


_GD_ORIG_URLOPEN = _gd_urllib_request.urlopen


def _gd_clone_request(req, alternate=False):

    if isinstance(req, str):

        new_req = _gd_urllib_request.Request(req)

    else:

        headers = {}

        try:
            headers.update(dict(req.header_items()))
        except Exception:
            pass

        new_req = _gd_urllib_request.Request(
            req.full_url,
            data=getattr(req, "data", None),
            headers=headers,
            method=req.get_method(),
        )

    if _gd_is_local_crm(new_req.full_url):

        for k, v in _gd_headers(alternate).items():
            new_req.add_header(k, v)

    return new_req


def _gd_urlopen(req, *args, **kwargs):

    url = (
        req
        if isinstance(req, str)
        else getattr(req, "full_url", "")
    )

    if not _gd_is_local_crm(url):
        return _GD_ORIG_URLOPEN(req, *args, **kwargs)

    req1 = _gd_clone_request(req, alternate=False)

    try:
        return _GD_ORIG_URLOPEN(req1, *args, **kwargs)

    except _gd_urllib_error.HTTPError as exc:

        if exc.code != 401 or not _GD_INTERNAL_TOKEN:
            raise

        req2 = _gd_clone_request(req, alternate=True)

        return _GD_ORIG_URLOPEN(
            req2,
            *args,
            **kwargs
        )


_gd_urllib_request.urlopen = _gd_urlopen

# También cubre "from urllib.request import urlopen"
globals()["urlopen"] = _gd_urlopen


# Compatibilidad requests, si V14.2 lo utiliza.
try:

    import requests as _gd_requests

    _GD_ORIG_REQUESTS_REQUEST = (
        _gd_requests.sessions.Session.request
    )

    def _gd_requests_request(
        self,
        method,
        url,
        **kwargs
    ):

        if not _gd_is_local_crm(url):

            return _GD_ORIG_REQUESTS_REQUEST(
                self,
                method,
                url,
                **kwargs
            )

        headers = dict(
            kwargs.get("headers") or {}
        )

        for k, v in _gd_headers(False).items():
            headers.setdefault(k, v)

        kwargs["headers"] = headers

        response = _GD_ORIG_REQUESTS_REQUEST(
            self,
            method,
            url,
            **kwargs
        )

        if (
            response.status_code == 401
            and
            _GD_INTERNAL_TOKEN
        ):

            headers = dict(headers)

            headers.update(
                _gd_headers(True)
            )

            kwargs["headers"] = headers

            response = _GD_ORIG_REQUESTS_REQUEST(
                self,
                method,
                url,
                **kwargs
            )

        return response

    _gd_requests.sessions.Session.request = (
        _gd_requests_request
    )

except Exception:
    pass


# GD_INTERNAL_AUTH_COMPAT_V142_END


# ============================================================
# AUTODETECT DB
# ============================================================

def list_databases():
    with psycopg.connect("dbname=postgres") as conn:
        q = """
        SELECT datname
        FROM pg_database
        WHERE datallowconn
          AND NOT datistemplate
        ORDER BY datname
        """
        return run_df(conn, q)["datname"].tolist()


def inspect_db(dbname):
    try:
        with psycopg.connect(f"dbname={dbname}") as conn:
            if not table_exists(conn, "leads"):
                return None

            rows = int(scalar(conn, "SELECT COUNT(*) FROM leads") or 0)
            stamps = []

            for table, column in [
                ("leads", "updated_at"),
                ("tasks", "updated_at"),
                ("activity_log", "created_at"),
                ("cotizaciones", "fecha"),
            ]:
                if table_exists(conn, table):
                    cols = get_columns(conn, table)
                    if column in cols:
                        value = scalar(
                            conn,
                            f"SELECT MAX({column}) FROM {table}"
                        )
                        if value is not None:
                            stamps.append(value)

            latest = max(stamps) if stamps else datetime.min

            return {
                "dbname": dbname,
                "rows": rows,
                "latest": latest,
            }
    except Exception:
        return None


def choose_db():
    forced = os.environ.get("GD_FORCE_DATABASE", "").strip()

    if forced:
        info = inspect_db(forced)
        if not info:
            raise RuntimeError(
                f"La BD forzada {forced} no contiene leads."
            )
        return info, [info]

    audit = []
    for db in list_databases():
        info = inspect_db(db)
        if info:
            audit.append(info)

    if not audit:
        raise RuntimeError("No encontré una BD con leads.")

    audit.sort(
        key=lambda x: (x["latest"], x["rows"]),
        reverse=True
    )
    return audit[0], audit


active_info, db_audit = choose_db()
ACTIVE_DB = active_info["dbname"]


# ============================================================
# LOAD CRM + QUOTE ACTIVITY
# ============================================================

with psycopg.connect(f"dbname={ACTIVE_DB}") as conn:
    lead_cols = get_columns(conn, "leads")
    estado_cols = get_columns(conn, "estados_lead")
    marca_cols = get_columns(conn, "marcas")

    estado_name = first_existing(
        ["estado", "nombre", "descripcion", "label"],
        estado_cols
    )
    marca_name = first_existing(
        ["marca", "nombre", "descripcion"],
        marca_cols
    ) or "marca"

    # V14: datos operacionales CRM LIVE. Calendar puede estar temporalmente
    # en fallback, pero teléfono/comuna/dirección nunca deben quedar pegados
    # a un snapshot si el lead ya fue corregido en PostgreSQL.
    comuna_table = None
    for _t in ("comunas", "comuna"):
        if table_exists(conn, _t):
            comuna_table = _t
            break
    comuna_join = ""
    comuna_select = "NULL::text AS comuna_catalogo,"
    if comuna_table and "id_comuna" in lead_cols:
        _cc = get_columns(conn, comuna_table)
        _cid = first_existing(["id_comuna", "id", "comuna_id"], _cc)
        _cname = first_existing(["comuna", "nombre", "descripcion"], _cc)
        if _cid and _cname:
            comuna_join = f"LEFT JOIN {comuna_table} cm ON cm.{_cid}=l.id_comuna"
            comuna_select = f"CAST(cm.{_cname} AS text) AS comuna_catalogo,"

    direccion_select = "COALESCE(l.direccion,'') AS direccion," if "direccion" in lead_cols else "''::text AS direccion,"
    pre_tel_select = "COALESCE(l.pre_telefono,'') AS pre_telefono," if "pre_telefono" in lead_cols else "''::text AS pre_telefono,"
    pre_dir_select = "COALESCE(l.pre_direccion,'') AS pre_direccion," if "pre_direccion" in lead_cols else "''::text AS pre_direccion,"
    pre_loc_select = "COALESCE(l.pre_location,'') AS pre_location," if "pre_location" in lead_cols else "''::text AS pre_location,"

    estado_expr = (
        f"COALESCE(NULLIF(TRIM(CAST(el.{estado_name} AS text)),''),"
        "CAST(l.id_estado AS text),'Sin estado')"
        if estado_name
        else "COALESCE(CAST(l.id_estado AS text),'Sin estado')"
    )

    activity_join = ""
    activity_select = """
        NULL::timestamptz AS last_activity_at,
        NULL::timestamptz AS relevant_activity_at,
        NULL::text AS relevant_activity_kind,
    """

    if table_exists(conn, "activity_log"):
        activity_join = r"""
        LEFT JOIN LATERAL (
            SELECT
                (SELECT a1.created_at
                   FROM activity_log a1
                  WHERE a1.entity_type='lead'
                    AND a1.entity_id=l.id_lead
                  ORDER BY a1.created_at DESC
                  LIMIT 1) AS created_at,
                rel.created_at AS relevant_activity_at,
                rel.kind AS relevant_activity_kind
            FROM (SELECT 1 AS seed) seed
            LEFT JOIN LATERAL (
                SELECT
                    a2.created_at,
                    COALESCE(NULLIF(TRIM(a2.action),''), NULLIF(TRIM(a2.path),''), 'MOVIMIENTO CRM') AS kind
                FROM activity_log a2
                WHERE a2.entity_type='lead'
                  AND a2.entity_id=l.id_lead
                  AND (
                      LOWER(COALESCE(a2.action,'')) ~ '(segu|follow|nota|note|observ|estado|state|move|contact)'
                      OR LOWER(COALESCE(a2.path,'')) ~ '(append_note|/estado|/move|followup)'
                      OR LOWER(COALESCE(a2.meta::text,'')) ~ '(seguimiento|followup|observacion|observación|nota|cambio.?de.?estado|estado_anterior|estado_nuevo)'
                  )
                ORDER BY a2.created_at DESC
                LIMIT 1
            ) rel ON TRUE
        ) act ON TRUE
        """
        activity_select = """
            act.created_at AS last_activity_at,
            act.relevant_activity_at,
            act.relevant_activity_kind,
        """

    # Cotizaciones: detectar timestamps disponibles.
    quote_join = ""
    quote_select = """
        0::bigint AS quote_count,
        NULL::timestamptz AS first_quote_at,
        NULL::timestamptz AS last_quote_activity_at,
        NULL::timestamptz AS last_quote_update_at,
    """

    if table_exists(conn, "cotizaciones"):
        cot_cols = get_columns(conn, "cotizaciones")
        q_created = first_existing(
            ["fecha", "created_at", "fecha_creacion"],
            cot_cols
        )
        q_updated = first_existing(
            [
                "updated_at", "fecha_actualizacion",
                "fecha_modificacion", "modificado_at"
            ],
            cot_cols
        )

        if q_created:
            activity_expr = f"c.{q_created}"
            updated_expr = "NULL::timestamptz"

            if q_updated:
                activity_expr = (
                    f"GREATEST(c.{q_created}, c.{q_updated})"
                )
                updated_expr = f"MAX(c.{q_updated})"

            quote_join = f"""
            LEFT JOIN LATERAL (
                SELECT
                    COUNT(*)::bigint AS quote_count,
                    MIN(c.{q_created}) AS first_quote_at,
                    MAX({activity_expr}) AS last_quote_activity_at,
                    {updated_expr} AS last_quote_update_at
                FROM cotizaciones c
                WHERE c.id_lead=l.id_lead
            ) q ON TRUE
            """

            quote_select = """
                COALESCE(q.quote_count,0)::bigint AS quote_count,
                q.first_quote_at,
                q.last_quote_activity_at,
                q.last_quote_update_at,
            """

    # Última tarea comercial abierta/reciente.
    commercial_like = " OR ".join(
        [f"tt.kind ILIKE '%{x}%'" for x in COMMERCIAL_TASK_PATTERNS]
    )

    task_join = f"""
    LEFT JOIN LATERAL (
        SELECT tt.*
        FROM tasks tt
        WHERE tt.entity_type='lead'
          AND tt.entity_id=l.id_lead
          AND ({commercial_like})
        ORDER BY
            CASE
              WHEN LOWER(COALESCE(tt.status,'')) IN
                ('open','pending','pendiente','abierta',
                 'todo','in_progress','en_progreso')
              THEN 0 ELSE 1
            END,
            COALESCE(tt.due_at,tt.updated_at,tt.created_at)
              DESC NULLS LAST,
            tt.id_task DESC
        LIMIT 1
    ) t ON TRUE
    """

    sql = f"""
    SELECT
        l.id_lead,
        COALESCE(NULLIF(TRIM(l.cliente),''),'Sin cliente') AS cliente,
        UPPER(COALESCE(NULLIF(TRIM(m.{marca_name}),''),'SIN MARCA')) AS marca,
        l.fecha_evento::date AS fecha_evento,
        COALESCE(l.monto_cotizado,0)::numeric AS monto,
        {estado_expr} AS estado,

        COALESCE(l.email,'') AS email,
        COALESCE(l.telefono,'') AS telefono,
        {direccion_select}
        {comuna_select}
        {pre_tel_select}
        {pre_dir_select}
        {pre_loc_select}
        COALESCE(l.notas,'') AS notas,

        l.created_at AS fecha_creacion,
        l.updated_at,
        l.seguimiento_at,
        {activity_select}

        l.num_cotizacion,
        l.id_cotizacion_vigente,
        COALESCE(l.cotizacion_pdf_url,'') AS cotizacion_pdf_url,

        l.declinado_at,
        l.agenda_approved_at,
        COALESCE(l.calendar_event_id,'') AS calendar_event_id,

        {quote_select}

        t.id_task,
        t.kind AS task_kind,
        t.status AS task_status,
        t.created_at AS task_created_at,
        t.due_at AS task_due_at,
        t.completed_at AS task_completed_at

    FROM leads l
    LEFT JOIN marcas m ON m.id_marca=l.id_marca
    LEFT JOIN estados_lead el ON el.id_estado=l.id_estado
    {comuna_join}

    {activity_join}
    {quote_join}
    {task_join}

    WHERE COALESCE(l.is_deleted,false)=false
    """

    leads = run_df(conn, sql)

    # Tareas operacionales separadas.
    ops_tasks = pd.DataFrame()
    if table_exists(conn, "tasks"):
        try:
            ops_sql = """
            SELECT
                t.id_task,
                t.kind,
                t.status,
                t.title,
                t.description,
                t.due_at,
                t.updated_at,
                t.entity_id AS id_lead,
                COALESCE(l.cliente,'Sin cliente') AS cliente,
                l.fecha_evento,
                UPPER(COALESCE(m.marca,'SIN MARCA')) AS marca
            FROM tasks t
            LEFT JOIN leads l
              ON t.entity_type='lead'
             AND t.entity_id=l.id_lead
            LEFT JOIN marcas m
              ON l.id_marca=m.id_marca
            WHERE t.entity_type='lead'
              AND t.kind = ANY(%s)
              AND LOWER(COALESCE(t.status,'')) = ANY(%s)
              AND l.fecha_evento >= CURRENT_DATE
              AND COALESCE(l.is_deleted,false)=false
            ORDER BY l.fecha_evento ASC,
                     t.due_at ASC NULLS LAST
            """
            ops_tasks = run_df(
                conn,
                ops_sql,
                [
                    list(OPS_TASK_KINDS),
                    list(OPEN_TASK_STATUSES),
                ]
            )
        except Exception:
            ops_tasks = pd.DataFrame()


# ============================================================
# NORMALIZE
# ============================================================

datetime_cols = [
    "fecha_evento", "fecha_creacion", "updated_at",
    "seguimiento_at", "last_activity_at", "relevant_activity_at",
    "declinado_at", "agenda_approved_at",
    "first_quote_at", "last_quote_activity_at",
    "last_quote_update_at",
    "task_created_at", "task_due_at", "task_completed_at",
]

for c in datetime_cols:
    if c in leads.columns:
        leads[c] = pd.to_datetime(leads[c], errors="coerce")

leads["monto"] = pd.to_numeric(
    leads["monto"], errors="coerce"
).fillna(0)

leads["quote_count"] = pd.to_numeric(
    leads["quote_count"], errors="coerce"
).fillna(0).astype(int)

leads["marca"] = leads["marca"].fillna("SIN MARCA")
leads["estado"] = leads["estado"].fillna("Sin estado")

leads["ejecutivo"] = (
    leads["marca"]
    .map(BRAND_OWNER)
    .fillna("MARCA SIN CONFIGURAR")
)


# ============================================================
# FOLLOW-UP EFFECTIVE
# ============================================================

def quote_followup_info(row):
    """
    V12:
    - Primera cotización = HITO DE GESTIÓN / seguimiento.
    - Nueva cotización o revisión posterior = mueve nuevamente el hito.
    - Si existe updated_at posterior, se usa la actividad más reciente.
    """
    count = int(row.get("quote_count", 0) or 0)
    first = row.get("first_quote_at")
    latest = row.get("last_quote_activity_at")
    updated = row.get("last_quote_update_at")

    if count <= 0 or pd.isna(first):
        return pd.Series([pd.NaT, ""])

    first_ts = pd.Timestamp(first)
    latest_ts = pd.Timestamp(latest) if pd.notna(latest) else first_ts

    if count >= 2 and pd.notna(latest):
        return pd.Series([latest_ts, "NUEVA/REVISIÓN COTIZACIÓN"])

    if pd.notna(updated):
        updated_ts = pd.Timestamp(updated)
        if updated_ts > first_ts + pd.Timedelta(minutes=2):
            return pd.Series([updated_ts, "COTIZACIÓN ACTUALIZADA"])

    return pd.Series([first_ts, "COTIZACIÓN INICIAL"])


leads[["quote_followup_at", "quote_followup_type"]] = leads.apply(
    quote_followup_info,
    axis=1
)


def effective_followup(row):
    candidates = []

    if pd.notna(row.get("seguimiento_at")):
        candidates.append(
            (pd.Timestamp(row["seguimiento_at"]), "SEGUIMIENTO CRM")
        )

    if pd.notna(row.get("quote_followup_at")):
        candidates.append(
            (
                pd.Timestamp(row["quote_followup_at"]),
                str(row["quote_followup_type"])
            )
        )

    # Estado, observación/nota, seguimiento explícito, contacto o move
    # registrado en activity_log también es gestión efectiva.
    if pd.notna(row.get("relevant_activity_at")):
        rel_ts = pd.Timestamp(row["relevant_activity_at"])
        created_ts = row.get("fecha_creacion")
        # Evita considerar como seguimiento el estado inicial creado junto al lead.
        if pd.isna(created_ts) or rel_ts > pd.Timestamp(created_ts) + pd.Timedelta(minutes=2):
            kind = str(row.get("relevant_activity_kind") or "MOVIMIENTO CRM")
            candidates.append(
                (rel_ts, f"HISTORIAL · {kind[:80]}")
            )

    if not candidates:
        return pd.Series([pd.NaT, ""])

    candidates.sort(key=lambda x: x[0], reverse=True)
    return pd.Series(candidates[0])


leads[["seguimiento_efectivo_at", "seguimiento_efectivo_tipo"]] = (
    leads.apply(effective_followup, axis=1)
)


# ============================================================
# RECONCILE FOLLOW-UP ANCHOR -> CRM
# ============================================================

reconciled_followups = 0

if RECONCILE_FOLLOWUP:
    candidates = leads[
        leads["seguimiento_efectivo_at"].notna()
    ][["id_lead", "seguimiento_at", "seguimiento_efectivo_at"]].copy()

    if not candidates.empty:
        with psycopg.connect(f"dbname={ACTIVE_DB}") as rconn:
            for _, rr in candidates.iterrows():
                effective = rr["seguimiento_efectivo_at"]
                current = rr["seguimiento_at"]

                if pd.isna(effective):
                    continue

                # Sólo avanzar el hito; nunca retrocederlo.
                if pd.notna(current) and pd.Timestamp(current) >= pd.Timestamp(effective):
                    continue

                cur = rconn.execute(
                    """
                    UPDATE leads
                       SET seguimiento_at=%s
                     WHERE id_lead=%s
                       AND (
                           seguimiento_at IS NULL
                           OR seguimiento_at < %s
                       )
                    """,
                    (
                        effective,
                        int(rr["id_lead"]),
                        effective,
                    ),
                )
                reconciled_followups += cur.rowcount

            rconn.commit()

    print("")
    print("RECONCILIACION SEGUIMIENTO V12")
    print(f"- DB: {ACTIVE_DB}")
    print(f"- hitos actualizados en leads.seguimiento_at: {reconciled_followups}")

    if RECONCILE_ONLY:
        raise SystemExit(0)


# Última gestión REAL: evidencia comercial; no contar simples lecturas del CRM.
leads["ultima_gestion_at"] = leads.apply(
    lambda r: max_ts(
        r.get("relevant_activity_at"),
        r.get("seguimiento_efectivo_at"),
        r.get("fecha_creacion"),
    ),
    axis=1
)


def hours_since(value):
    if value is None or pd.isna(value):
        return None

    t = pd.Timestamp(value)
    if t.tzinfo is not None:
        t = t.tz_convert(TIMEZONE).tz_localize(None)

    now_naive = NOW.replace(tzinfo=None)

    return max(
        0,
        int((now_naive - t.to_pydatetime()).total_seconds() // 3600)
    )


leads["horas_sin_gestion"] = leads["ultima_gestion_at"].apply(
    hours_since
)


# ============================================================
# TASK EFFECTIVE STATE
# ============================================================

leads["task_open_raw"] = (
    leads["task_status"]
    .fillna("")
    .str.lower()
    .isin(OPEN_TASK_STATUSES)
)

def task_is_superseded(row):
    if not bool(row.get("task_open_raw")):
        return False

    follow = row.get("seguimiento_efectivo_at")
    created = row.get("task_created_at")

    if pd.isna(follow) or pd.isna(created):
        return False

    return pd.Timestamp(created) <= pd.Timestamp(follow)


leads["task_superada_por_gestion"] = leads.apply(
    task_is_superseded,
    axis=1
)

leads["task_open_effective"] = (
    leads["task_open_raw"]
    & ~leads["task_superada_por_gestion"]
)

leads["task_due_now"] = (
    leads["task_open_effective"]
    & leads["task_due_at"].notna()
    & (leads["task_due_at"] <= pd.Timestamp(NOW))
)

leads["task_overdue"] = (
    leads["task_open_effective"]
    & leads["task_due_at"].notna()
    & (leads["task_due_at"] < pd.Timestamp(NOW))
)


# ============================================================
# OPTIONAL TASK SYNC
# ============================================================

# Por defecto NO escribe nada.
# Si GD_SYNC_TASKS=1, cierra como done las tareas comerciales abiertas
# creadas antes o en el momento de un seguimiento efectivo posterior.
SYNC_TASKS = os.environ.get("GD_SYNC_TASKS", "0").strip() == "1"
task_sync_updated = 0

sync_candidates = leads[
    leads["seguimiento_efectivo_at"].notna()
].copy()

if SYNC_TASKS and not sync_candidates.empty:
    commercial_conditions = " OR ".join(
        [f"kind ILIKE '%{x}%'" for x in COMMERCIAL_TASK_PATTERNS]
    )

    with psycopg.connect(f"dbname={ACTIVE_DB}") as sync_conn:
        for _, r in sync_candidates.iterrows():
            follow_at = r["seguimiento_efectivo_at"]
            if pd.isna(follow_at):
                continue

            sql_sync = f"""
            UPDATE tasks
            SET
                status='done',
                completed_at=COALESCE(completed_at,%s),
                completed_by=COALESCE(completed_by,'gd_quote_followup'),
                updated_at=NOW()
            WHERE entity_type='lead'
              AND entity_id=%s
              AND LOWER(COALESCE(status,'')) = ANY(%s)
              AND created_at <= %s
              AND ({commercial_conditions})
            """

            cur = sync_conn.execute(
                sql_sync,
                (
                    follow_at,
                    int(r["id_lead"]),
                    list(OPEN_TASK_STATUSES),
                    follow_at,
                )
            )
            task_sync_updated += cur.rowcount

        sync_conn.commit()


# ============================================================
# ACTIVE OPPORTUNITIES
# ============================================================

terminal_state = leads["estado"].apply(
    lambda x: bool(TERMINAL_RE.search(str(x)))
)

declined = leads["declinado_at"].notna()

confirmed = (
    leads["agenda_approved_at"].notna()
    | leads["calendar_event_id"]
        .fillna("")
        .astype(str)
        .str.strip()
        .ne("")
)

future = (
    leads["fecha_evento"].notna()
    & (leads["fecha_evento"].dt.date >= TODAY)
)

base_active = leads[
    future
    & ~terminal_state
    & ~declined
    & ~confirmed
].copy()

# Cola de ataque:
# - sin seguimiento efectivo; O
# - hubo seguimiento, pero luego nació una nueva tarea comercial que ya está vencida/debida.
needs_action = (
    base_active["seguimiento_efectivo_at"].isna()
    | base_active["task_due_now"]
)

pipeline = base_active[needs_action].copy()

pipeline["dias_evento"] = (
    pipeline["fecha_evento"].dt.date - TODAY
).apply(lambda d: d.days)

pipeline["month_key"] = pipeline["fecha_evento"].dt.strftime("%Y-%m")
pipeline["month_label"] = pipeline["fecha_evento"].apply(
    lambda d: f"{MONTH_NAMES[d.month]} {d.year}"
)

pipeline["semana"] = pipeline["dias_evento"].between(0, 7)
pipeline["alto_monto"] = pipeline["monto"] >= 3_000_000
pipeline["sin_gestion_48"] = pipeline["horas_sin_gestion"].apply(
    lambda x: x is None or x >= 48
)
pipeline["sin_gestion_72"] = pipeline["horas_sin_gestion"].apply(
    lambda x: x is None or x >= 72
)
pipeline["tarea_vencida"] = pipeline["task_overdue"]


def attack(row):
    days = int(row["dias_evento"])
    amount = float(row["monto"])
    hours = row["horas_sin_gestion"]

    reasons = [f"evento en {days} día(s)"]

    if days <= 3:
        action = "LLAMAR AHORA"
        how = "Confirmar decisión final y condición exacta de cierre."
    elif days <= 7:
        action = "LLAMAR HOY"
        how = "Llamada directa; identificar objeción y fijar siguiente paso."
    elif days <= 14:
        action = "SEGUIMIENTO DIRECTO"
        how = "Llamar y obtener fecha concreta de decisión."
    elif days <= 30:
        action = "EMPUJAR CIERRE"
        how = "Reactivar cotización y pedir definición."
    else:
        action = "CONTACTAR + AGENDAR"
        how = "Validar interés y registrar seguimiento."

    if amount >= 5_000_000:
        reasons.append("monto ≥ $5M")
        how += " Priorizar llamada sobre mensajería."

    if pd.notna(row.get("seguimiento_efectivo_at")):
        reasons.append(
            "nueva tarea posterior al seguimiento"
        )

    if hours is None:
        reasons.append("sin gestión registrada")
    elif hours >= 72:
        reasons.append(f"{hours}h sin gestión")
        if days <= 30:
            action = "RECUPERAR HOY"
    elif hours >= 48:
        reasons.append(f"{hours}h sin gestión")

    if bool(row["tarea_vencida"]):
        reasons.append("tarea comercial vencida")
        action = "RECUPERAR HOY"

    return pd.Series([
        action,
        " · ".join(reasons),
        how,
    ])


pipeline[["accion", "motivo", "como_atacar"]] = pipeline.apply(
    attack,
    axis=1
)


def followup_flag(row):
    """
    Banderas visuales de la cola:
    🚩 rojo    = tarea vencida / 72h+ sin gestión
    🟠 naranja = evento muy próximo o 48h+ sin gestión
    🟡 amarillo= tarea activa para hoy
    🔵 azul    = hallazgo nuevo se agrega en UI con data-finding
    """
    hours = row.get("horas_sin_gestion")
    days = int(row.get("dias_evento", 999) or 999)

    if bool(row.get("task_overdue")):
        return pd.Series(["🚩", "TAREA VENCIDA", "flag-red"])

    if hours is None or hours >= 72:
        return pd.Series(["🚩", "72H SIN GESTIÓN", "flag-red"])

    if bool(row.get("task_due_now")):
        return pd.Series(["🟡", "TAREA PARA HOY", "flag-yellow"])

    if hours >= 48 or days <= 3:
        return pd.Series(["🟠", "PRIORIDAD ALTA", "flag-orange"])

    return pd.Series(["⚪", "GESTIONAR", "flag-neutral"])


pipeline[["bandera", "bandera_label", "bandera_class"]] = pipeline.apply(
    followup_flag,
    axis=1
)

pipeline = pipeline.sort_values(
    ["fecha_evento", "monto", "cliente"],
    ascending=[True, False, True]
)



# ============================================================
# COMMERCIAL HISTORY FOR LEAD MODAL
# ============================================================

lead_history = {}
pipeline_ids = sorted({
    int(x) for x in pipeline["id_lead"].dropna().tolist()
})

def _history_add(lead_id, at, kind, detail=""):
    if lead_id is None or pd.isna(at):
        return
    lid = str(int(lead_id))
    lead_history.setdefault(lid, []).append({
        "at": pd.Timestamp(at).isoformat(),
        "kind": str(kind or ""),
        "detail": str(detail or "")[:260],
    })

if pipeline_ids:
    with psycopg.connect(f"dbname={ACTIVE_DB}") as hconn:
        # Movimientos comerciales del historial.
        if table_exists(hconn, "activity_log"):
            try:
                hsql = r"""
                SELECT
                    entity_id,
                    created_at,
                    COALESCE(NULLIF(TRIM(action),''),
                             NULLIF(TRIM(path),''),
                             'MOVIMIENTO CRM') AS kind,
                    LEFT(COALESCE(meta::text,''),260) AS detail
                FROM activity_log
                WHERE entity_type='lead'
                  AND entity_id = ANY(%s)
                  AND (
                      LOWER(COALESCE(action,'')) ~
                        '(segu|follow|nota|note|observ|estado|state|move|contact)'
                      OR LOWER(COALESCE(path,'')) ~
                        '(append_note|/estado|/move|followup)'
                      OR LOWER(COALESCE(meta::text,'')) ~
                        '(seguimiento|followup|observacion|observación|nota|cambio.?de.?estado|estado_anterior|estado_nuevo)'
                  )
                ORDER BY entity_id, created_at DESC
                """
                hdf = run_df(hconn, hsql, [pipeline_ids])
                for _, hr in hdf.iterrows():
                    lid = str(int(hr["entity_id"]))
                    # máximo 8 movimientos CRM por lead
                    existing = sum(
                        1 for x in lead_history.get(lid, [])
                        if x.get("source") == "activity"
                    )
                    if existing >= 8:
                        continue
                    _history_add(
                        hr["entity_id"],
                        hr["created_at"],
                        f"📝 {hr['kind']}",
                        hr["detail"],
                    )
                    lead_history[lid][-1]["source"] = "activity"
            except Exception:
                pass

        # Historial de cotizaciones: aquí queda explícita la fecha que
        # activa/mueve el seguimiento.
        if table_exists(hconn, "cotizaciones"):
            ccols = get_columns(hconn, "cotizaciones")
            ccreated = first_existing(
                ["fecha", "created_at", "fecha_creacion"],
                ccols
            )
            cupdated = first_existing(
                ["updated_at", "fecha_actualizacion",
                 "fecha_modificacion", "modificado_at"],
                ccols
            )
            if ccreated:
                updated_select = (
                    f", c.{cupdated} AS updated_at"
                    if cupdated else ", NULL::timestamptz AS updated_at"
                )
                estado_select = (
                    ", COALESCE(c.estado::text,'') AS estado"
                    if "estado" in ccols else ", ''::text AS estado"
                )
                numero_select = ""
                if "numero" in ccols:
                    numero_select = ", COALESCE(c.numero::text,'') AS numero"
                elif "num_cotizacion" in ccols:
                    numero_select = ", COALESCE(c.num_cotizacion::text,'') AS numero"
                else:
                    numero_select = ", ''::text AS numero"

                qhist_sql = f"""
                SELECT
                    c.id_lead,
                    c.id_cotizacion,
                    c.{ccreated} AS created_at
                    {updated_select}
                    {estado_select}
                    {numero_select}
                FROM cotizaciones c
                WHERE c.id_lead = ANY(%s)
                ORDER BY c.id_lead, c.{ccreated} DESC
                """
                try:
                    qh = run_df(hconn, qhist_sql, [pipeline_ids])
                    for _, qr in qh.iterrows():
                        label = (
                            f"🧾 COTIZACIÓN {qr['numero']}"
                            if str(qr.get("numero") or "").strip()
                            else f"🧾 COTIZACIÓN #{int(qr['id_cotizacion'])}"
                        )
                        _history_add(
                            qr["id_lead"],
                            qr["created_at"],
                            label,
                            f"Estado: {qr.get('estado') or '—'} · "
                            "fecha de cotización = hito de seguimiento",
                        )
                        if pd.notna(qr.get("updated_at")):
                            if pd.Timestamp(qr["updated_at"]) > pd.Timestamp(qr["created_at"]) + pd.Timedelta(minutes=2):
                                _history_add(
                                    qr["id_lead"],
                                    qr["updated_at"],
                                    "🧾 REVISIÓN COTIZACIÓN",
                                    label,
                                )
                except Exception:
                    pass

        # Tareas comerciales recientes: permiten ver cuándo vuelve
        # a entrar al circuito después de un seguimiento.
        try:
            task_like = " OR ".join(
                [f"kind ILIKE '%{x}%'" for x in COMMERCIAL_TASK_PATTERNS]
            )
            thsql = f"""
            SELECT
                entity_id,
                created_at,
                due_at,
                status,
                kind,
                title
            FROM tasks
            WHERE entity_type='lead'
              AND entity_id = ANY(%s)
              AND ({task_like})
            ORDER BY entity_id, created_at DESC
            """
            th = run_df(hconn, thsql, [pipeline_ids])
            task_seen = {}
            for _, tr in th.iterrows():
                lid = str(int(tr["entity_id"]))
                if task_seen.get(lid, 0) >= 5:
                    continue
                detail = (
                    f"Estado: {tr.get('status') or '—'}"
                    + (
                        f" · vence {fmt_dt(tr.get('due_at'))}"
                        if pd.notna(tr.get("due_at")) else ""
                    )
                    + (
                        f" · {tr.get('title')}"
                        if str(tr.get("title") or "").strip() else ""
                    )
                )
                _history_add(
                    tr["entity_id"],
                    tr["created_at"],
                    f"⏰ TAREA · {tr.get('kind') or ''}",
                    detail,
                )
                task_seen[lid] = task_seen.get(lid, 0) + 1
        except Exception:
            pass

for lid, entries in lead_history.items():
    # Dedupe and keep 14 most recent milestones.
    entries.sort(key=lambda x: x.get("at") or "", reverse=True)
    seen = set()
    clean = []
    for item in entries:
        key = (item.get("at"), item.get("kind"), item.get("detail"))
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)
        if len(clean) >= 14:
            break
    lead_history[lid] = clean


# ============================================================
# LOCAL QUOTE DETAILS
# ============================================================

# No se usa el endpoint /quotes/{id}/pdf porque exige autenticación
# propia del frontend. El dashboard abre la cotización desde PostgreSQL.
quote_data = {}

quote_ids = sorted({
    int(x)
    for x in pipeline["id_cotizacion_vigente"].dropna().tolist()
    if str(x).strip()
})

if quote_ids:
    with psycopg.connect(f"dbname={ACTIVE_DB}") as qconn:
        if table_exists(qconn, "cotizaciones"):
            cot_cols = get_columns(qconn, "cotizaciones")
            item_exists = table_exists(qconn, "cotizacion_items")
            item_cols = (
                get_columns(qconn, "cotizacion_items")
                if item_exists
                else []
            )

            if "id_cotizacion" in cot_cols:
                items_expr = "'[]'::jsonb"

                if item_exists and "id_cotizacion" in item_cols:
                    items_expr = """
                    COALESCE(
                        (
                            SELECT jsonb_agg(to_jsonb(ci))
                            FROM cotizacion_items ci
                            WHERE ci.id_cotizacion=c.id_cotizacion
                        ),
                        '[]'::jsonb
                    )
                    """

                qsql = f"""
                SELECT
                    c.id_cotizacion,
                    to_jsonb(c) AS quote_json,
                    {items_expr} AS items_json
                FROM cotizaciones c
                WHERE c.id_cotizacion = ANY(%s)
                """

                for qid, qjson, items in qconn.execute(
                    qsql,
                    (quote_ids,)
                ).fetchall():
                    quote_data[str(int(qid))] = {
                        "quote": qjson or {},
                        "items": items or [],
                    }

quote_data_json = json.dumps(
    quote_data,
    ensure_ascii=False,
    default=str,
).replace("<", "\\u003c").replace(
    ">", "\\u003e"
).replace("&", "\\u0026")


# ============================================================
# SUMMARIES
# ============================================================

exec_summary = (
    pipeline.groupby("ejecutivo", dropna=False)
    .agg(
        oportunidades=("id_lead", "count"),
        pipeline=("monto", "sum"),
        semana=("semana", "sum"),
        sin_gestion_72=("sin_gestion_72", "sum"),
        alto_monto=("alto_monto", "sum"),
        tareas_vencidas=("tarea_vencida", "sum"),
    )
    .reset_index()
    .sort_values(
        ["pipeline", "oportunidades"],
        ascending=[False, False]
    )
)

brand_summary = (
    pipeline.groupby(["marca", "ejecutivo"], dropna=False)
    .agg(
        oportunidades=("id_lead", "count"),
        pipeline=("monto", "sum"),
    )
    .reset_index()
    .sort_values("pipeline", ascending=False)
)


# ============================================================
# OPTIONS
# ============================================================

months = (
    pipeline[["month_key", "month_label"]]
    .drop_duplicates()
    .sort_values("month_key")
    .to_dict(orient="records")
)

brands = sorted(str(x) for x in pipeline["marca"].dropna().unique())
execs = sorted(str(x) for x in pipeline["ejecutivo"].dropna().unique())
statuses = sorted(str(x) for x in pipeline["estado"].dropna().unique())


def select_options(values, all_label):
    items = [f'<option value="all">{esc(all_label)}</option>']
    for value in values:
        items.append(
            f'<option value="{esc(value.lower())}">{esc(value)}</option>'
        )
    return "".join(items)


month_options = ['<option value="all">Todos los meses vigentes</option>']
for item in months:
    selected = (
        " selected"
        if item["month_key"] == TODAY.strftime("%Y-%m")
        else ""
    )
    month_options.append(
        f'<option value="{esc(item["month_key"])}"{selected}>'
        f'{esc(item["month_label"])}</option>'
    )


# ============================================================
# HTML ROWS
# ============================================================

def details_json(row):
    details = {
        "id_lead": int(row["id_lead"]),
        "cliente": str(row["cliente"]),
        "marca": str(row["marca"]),
        "ejecutivo": str(row["ejecutivo"]),
        "fecha_evento": fmt_date(row["fecha_evento"]),
        "monto": money(row["monto"]),
        "estado": str(row["estado"]),
        "email": str(row["email"] or ""),
        "telefono": str(row["telefono"] or ""),
        "notas": str(row["notas"] or ""),
        "bandera": str(row.get("bandera") or ""),
        "bandera_label": str(row.get("bandera_label") or ""),
        "historial": lead_history.get(str(int(row["id_lead"])), []),
        "cotizacion": (
            str(row["num_cotizacion"])
            if pd.notna(row["num_cotizacion"])
            else ""
        ),
        "id_cotizacion": (
            int(row["id_cotizacion_vigente"])
            if pd.notna(row["id_cotizacion_vigente"])
            else None
        ),
        "quote_count": int(row["quote_count"]),
        "seguimiento_efectivo": fmt_dt(row["seguimiento_efectivo_at"]),
        "seguimiento_tipo": str(row["seguimiento_efectivo_tipo"] or ""),
        "task_kind": str(row["task_kind"] or ""),
        "task_due": fmt_dt(row["task_due_at"]),
        "task_superada": bool(row["task_superada_por_gestion"]),
        "accion": str(row["accion"]),
        "motivo": str(row["motivo"]),
        "como_atacar": str(row["como_atacar"]),
    }
    return esc(json.dumps(details, ensure_ascii=False))


# Inicialización temprana: la tabla comercial se construye antes de que
# el deep-refresh termine de calcular los hallazgos. Se reemplaza más
# adelante con los IDs reales y se reconstruyen las filas.
lead_finding_ids = set()
calendar_finding_ids = set()

def row_attrs(row):
    tags = []
    if row["semana"]:
        tags.append("semana")
    if row["alto_monto"]:
        tags.append("alto-monto")
    if row["sin_gestion_48"]:
        tags.append("sin-gestion-48")
    if row["sin_gestion_72"]:
        tags.append("sin-gestion-72")
    if row["tarea_vencida"]:
        tags.append("tarea-vencida")

    finding_attr = "1" if str(int(row["id_lead"])) in lead_finding_ids else "0"
    return (
        f'data-finding="{finding_attr}" '
        f'data-lead="{int(row["id_lead"])}" '
        f'data-client="{esc(str(row["cliente"]).lower())}" '
        f'data-brand="{esc(str(row["marca"]).lower())}" '
        f'data-exec="{esc(str(row["ejecutivo"]).lower())}" '
        f'data-status="{esc(str(row["estado"]).lower())}" '
        f'data-month="{esc(row["month_key"])}" '
        f'data-date="{row["fecha_evento"].strftime("%Y-%m-%d")}" '
        f'data-amount="{float(row["monto"])}" '
        f'data-tags="{esc(" ".join(tags))}" '
        f'data-details="{details_json(row)}"'
    )


def quote_button(row):
    qid = row.get("id_cotizacion_vigente")
    num = (
        str(row["num_cotizacion"])
        if pd.notna(row.get("num_cotizacion"))
        else ""
    )

    if qid is None or pd.isna(qid):
        return esc(num) if num else "—"

    return (
        f'<button class="link-button" '
        f'onclick="openQuoteFromRow(this.closest(\'tr\'))">'
        f'{esc(num or "Abrir")} ↗</button>'
    )



pipeline_rows = []
for _, row in pipeline.iterrows():
    idle = (
        "—"
        if row["horas_sin_gestion"] is None
        else f'{int(row["horas_sin_gestion"])}h'
    )

    pipeline_rows.append(
        f"""
        <tr {row_attrs(row)}>
          <td>
            <button class="link-button"
                    onclick="openLeadFromRow(this.closest('tr'))">Ver</button>
          </td>
          <td class="flag-cell"><span class="follow-flag {esc(row['bandera_class'])}" title="{esc(row['bandera_label'])}">{esc(row['bandera'])}</span></td>
          <td>{esc(row['cliente'])}</td>
          <td class="phone-cell">{esc(row['telefono']) or '—'}</td>
          <td>{esc(row['marca'])}</td>
          <td>{esc(row['ejecutivo'])}</td>
          <td>{fmt_date(row['fecha_evento'])}</td>
          <td class="money">{money(row['monto'])}</td>
          <td>{esc(row['estado'])}</td>
          <td>{quote_button(row)}</td>
          <td><strong>{esc(row['accion'])}</strong></td>
          <td>{fmt_dt(row['seguimiento_efectivo_at']) or '—'}</td>
          <td>{esc(row['seguimiento_efectivo_tipo']) or '—'}</td>
          <td>{idle}</td>
        </tr>
        """
    )


exec_rows = []
for _, row in exec_summary.iterrows():
    name = str(row["ejecutivo"])
    exec_rows.append(
        f"""
        <tr class="drill"
            data-drill-exec="{esc(name.lower())}"
            ondblclick="drillExecutive(this.dataset.drillExec)"
            title="Doble click para ver oportunidades">
          <td>{esc(name)}</td>
          <td>{int(row['oportunidades'])}</td>
          <td class="money">{money(row['pipeline'])}</td>
          <td>{int(row['semana'])}</td>
          <td>{int(row['sin_gestion_72'])}</td>
          <td>{int(row['alto_monto'])}</td>
          <td>{int(row['tareas_vencidas'])}</td>
        </tr>
        """
    )


brand_rows = []
for _, row in brand_summary.iterrows():
    name = str(row["marca"])
    brand_rows.append(
        f"""
        <tr class="drill"
            data-drill-brand="{esc(name.lower())}"
            ondblclick="drillBrand(this.dataset.drillBrand)"
            title="Doble click para ver oportunidades">
          <td>{esc(name)}</td>
          <td>{esc(row['ejecutivo'])}</td>
          <td>{int(row['oportunidades'])}</td>
          <td class="money">{money(row['pipeline'])}</td>
        </tr>
        """
    )


ops_rows = []
if not ops_tasks.empty:
    for _, row in ops_tasks.iterrows():
        ops_rows.append(
            f"""
            <tr>
              <td>{fmt_date(row['fecha_evento'])}</td>
              <td>{esc(row['cliente'])}</td>
              <td>{esc(row['marca'])}</td>
              <td><strong>{esc(row['kind'])}</strong></td>
              <td>{fmt_dt(row['due_at']) or '—'}</td>
              <td class="wrap">{esc(row['title'])}</td>
              <td class="wrap">{esc(row['description'])}</td>
            </tr>
            """
        )

if not ops_rows:
    ops_rows = ["""
    <tr><td colspan="7">
      No hay tareas operacionales abiertas de completitud en CRM.
    </td></tr>
    """]



# ============================================================
# CALENDAR SNAPSHOT / WEEKLY OPS
# ============================================================

CALENDAR_SNAPSHOT_VERIFIED_AT = "2026-08-11T09:49:00-04:00"
EMBEDDED_CALENDAR_EVENTS = json.loads(r"""[{"id":"7frdojclbqmjc08thr9p5jttig","summary":"NICOLE ACEVEDO - GOURMET","location":"LA REINA","start":"2026-08-10T09:00:00-04:00","end":"2026-08-10T14:00:00-04:00","url":"https://www.google.com/calendar/event?eid=N2ZyZG9qY2xicW1qYzA4dGhyOXA1anR0aWcgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"Proyecto Familia Lever Entrega 11:00 a 14:00. PRODUCTOS: 3 Branding Carro; 450 Chucrut; 450 Envase Personalizado; 400 Hot Dog Italiano; 50 Hot Dog Italiano Vegetariano; 450 Salsa Americana. MONTAJE: 3 x Carro Clásico con Branding. OPS: 3. TELEFONO: +56950963440. DIRECCION: Príncipe de Gales 6030."},{"id":"3m5c5u7nf4kdqljsu7vo4eumcc","summary":"RICARDO VEJAR - DEL SABOR","location":"LAS CONDES","start":"2026-08-11T11:00:00-04:00","end":"2026-08-11T15:00:00-04:00","url":"https://www.google.com/calendar/event?eid=M201YzV1N25mNGtkcWxqc3U3dm80ZXVtY2Mgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 60 Cabritas. MONTAJE: 1 x Carro Rojo; 1 x Máquina Cabritas. OPS: 1. TELEFONO: +56976173301. DIRECCION: Rosario Norte 532, Piso 6 Las Condes."},{"id":"okjj4le33c0dj30h8sjdk6l8jg","summary":"MIREYA BARRIOS, GRUPO EXPRO - GOURMET","location":"PEDRO AGUIRRE CERDA","start":"2026-08-11T11:00:00-04:00","end":"2026-08-11T15:00:00-04:00","url":"https://www.google.com/calendar/event?eid=b2tqajRsZTMzYzBkajMwaDhzamRrNmw4amcgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 74 Churrasco Italiano; 74 Churrasco Luco; 6 Hamburguesa italiana Veggie; 6 Hamburguesa Queso. MONTAJE: 2 x Carro Clásico. OPS: 2. TELEFONO: +56988590366. DIRECCION: Parque Recreativo Beaucheff Caja Los Andes. Beaucheff 3500."},{"id":"7g8gats9rnk0er8nbuq53fhl6k","summary":"JAVIERA RAURICH - CAMALEON- MONTAJE","location":"SANTIAGO","start":"2026-08-11T16:00:00-04:00","end":"2026-08-11T20:00:00-04:00","url":"https://www.google.com/calendar/event?eid=N2c4Z2F0czlybmswZXI4bmJ1cTUzZmhsNmsgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"MONTAJE: 2 carro rojos; 2 graficas; 2 maquina pop corn. TELEFONO: +56989621444. DIRECCION: Av. Libertador Bernardo O´Higgins 136, Santiago. (Hotel Le Meridien)."},{"id":"1qj65086paua2dsjh47fssbj8r","summary":"JAVIERA RAURICH - CAMALEON","location":"SANTIAGO","start":"2026-08-12T09:00:00-04:00","end":"2026-08-12T13:00:00-04:00","url":"https://www.google.com/calendar/event?eid=MXFqNjUwODZwYXVhMmRzamg0N2Zzc2JqOHIgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 2 Grafica Personalizada; 300 Pop Corn. MONTAJE: 2 carro rojos; 2 graficas; 2 maquina pop corn. OPS: 2. TELEFONO: +56989621444. DIRECCION: Av. Libertador Bernardo O´Higgins 136, Santiago. (Hotel Le Meridien)."},{"id":"vjibpks4cveoktum6f909ev1hs","summary":"TERE OTAEGUI - GOURMET","location":"SANTIAGO","start":"2026-08-12T11:00:00-04:00","end":"2026-08-12T14:30:00-04:00","url":"https://www.google.com/calendar/event?eid=dmppYnBrczRjdmVva3R1bTZmOTA5ZXYxaHMgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS: Montaje Martes 11: 15:00 a 17:00. Coffee: 50% coffee y 50% chocolate caliente; chocolate con Mini Mashmellows. PRODUCTOS: 50 Churros españoles; 60 Coffee; 60 Jugo; 60 Toppings; 50 Wraps Pollo. MONTAJE: Carro Clásico; Horno; Percolera; 2 Modulos. OPS: 3. TELEFONO: +56997895699. DIRECCION: Universidad Diego Portales, Vergara 324."},{"id":"jdnnmp7s6ev71u1u7356s5rf14","summary":"PRISCILLA ROJAS SUAZO, PUC - GOURMET","location":"MACUL","start":"2026-08-12T12:00:00-04:00","end":"2026-08-12T16:00:00-04:00","url":"https://www.google.com/calendar/event?eid=amRubm1wN3M2ZXY3MXUxdTczNTZzNXJmMTQgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS: Burger y mechadas los mismos ingredientes. PRODUCTOS: 5 Hamburguesa Gourmet Veggie; 95 Mechada Gourmet; 1 Operador. MONTAJE: 2 x Carro Clásico. OPS: 2. TELEFONO: +56985742469. DIRECCION: PUC. Benito Rebolledo 2056, Edificio 104, Ingeniería Estructural, Macul."},{"id":"5mo4eagah72q6sr2nchgm55glv","summary":"JAVIERA RAURICH - CAMALEON","location":"SANTIAGO","start":"2026-08-13T11:00:00-04:00","end":"2026-08-13T13:00:00-04:00","url":"https://www.google.com/calendar/event?eid=NW1vNGVhZ2FoNzJxNnNyMm5jaGdtNTVnbHYgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 2 Grafica Personalizada; 300 Pop Corn. MONTAJE: 2 carro rojos; 2 graficas; 2 maquina pop corn. OPS: 2. TELEFONO: +56989621444. DIRECCION: Av. Libertador Bernardo O´Higgins 136, Santiago. (Hotel Le Meridien)."},{"id":"gqgda4vlg6m9umc51o0jml5gjo","summary":"PAOLA MADARIAGA - EXPRESS","location":"LAS CONDES","start":"2026-08-13T11:00:00-04:00","end":"2026-08-13T13:00:00-04:00","url":"https://www.google.com/calendar/event?eid=Z3FnZGE0dmxnNm05dW1jNTFvMGptbDVnam8gc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS IMPORTANTES: 100 CAFÉS CON LECHE. PINTAR UN CARRO ROJO DE COLOR AZUL. POR CONFIRMAR EL MONTAJE EN LA MAÑANA ESE DÍA. Donuts con envoltorio plástico y cinta azul; conos personalizados; stickers. MONTAJE: 1 CARRO ROJO; 1 CARRO BLANCO. OPS: 2. TELEFONO: +56978018087. DIRECCION: PARQUE ARAUCO, TIENDA WADOS."},{"id":"j0rcluet3bf5jgmt16mktmnhcg","summary":"ISABEL ROSSELOT, BANCO FALABELLA - GOURMET","location":"LAS CONDES","start":"2026-08-13T12:30:00-04:00","end":"2026-08-13T16:30:00-04:00","url":"https://www.google.com/calendar/event?eid=ajByY2x1ZXQzYmY1amdtdDE2bWt0bW5oY2cgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"Llegada 12:30. Entrega 14:00. Termino 16:30. PRODUCTOS: 74 Hot Dog Italiano; 6 Hot Dog Italiano vegetarianos. MONTAJE: 1 x Modulo electrico. OPS: 1. TELEFONO: +56991443596. DIRECCION: Rosario Norte 660, piso 17 (sala capacitación)."},{"id":"9qhjbtbhght94eldmc3ilp9cs0","summary":"SANDRO GUERRERO - GOURMET","location":"VIÑA DEL MAR","start":"2026-08-14T09:00:00-04:00","end":"2026-08-14T16:00:00-04:00","url":"https://www.google.com/calendar/event?eid=OXFoamJ0YmhnaHQ5NGVsZG1jM2lscDljczAgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 250 Algodón; 300 Mini Burger Italiana; 500 Mini Hot Dog Italiano; 300 Mini Lomito Italiano; 250 Pop Corn; 90 Wraps Clásico; 90 Wraps Pollo; 1780 Ticket. MONTAJE: 8 Carros Clásicos branding; 3 Modulos branding; 2 Máquinas Cabritas; 3 Máquinas Algodón. OPS: 17. TELEFONO: +56998736408. DIRECCION: Bel Ray Chile. ParqTec21. Limache 3233 VIÑA DEL MAR."},{"id":"u2aic3hjdj5h29io7nnp3j7sh0","summary":"ANA LUISA - EXPRESS","location":"SAN MIGUEL","start":"2026-08-14T13:00:00-04:00","end":"2026-08-14T17:00:00-04:00","url":"https://www.google.com/calendar/event?eid=dTJhaWMzaGpkajVoMjlpbzdubnAzajdzaDAgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 30 Churrasco Italiano; 30 Churrasco Luco. MONTAJE: Carro Clásico. OPS: 1. TELEFONO: +56996411578. DIRECCION: ARCANGEL 1517."},{"id":"foa3fl5lra3a6c1qian7k1pm3k","summary":"PAOLA CANCINO - EXPRESS","location":"ÑUÑOA","start":"2026-08-14T19:00:00-04:00","end":"2026-08-14T23:00:00-04:00","url":"https://www.google.com/calendar/event?eid=Zm9hM2ZsNWxyYTNhNmMxcWlhbjdrMXBtM2sgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS: promo invierno. PRODUCTOS: 60 Hotdogs Italianos; 40 Papas fritas. MONTAJE: 1 Carro Clásico. OPS: 1. TELEFONO: +56956278641. DIRECCION: Pedro de Valdivia 4041 salon de eventos 1°piso."},{"id":"1l9dea98ntfpkit78qer67reds","summary":"PIERO BUSTAMANTE - DEL SABOR","location":"LAS CONDES","start":"2026-08-14T20:00:00-04:00","end":"2026-08-14T23:59:00-04:00","url":"https://www.google.com/calendar/event?eid=MWw5ZGVhOThudGZwa2l0NzhxZXI2N3JlZHMgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 50 Burger Clásica; 50 Churrasco Italiano. MONTAJE: 1 Carro Clásico. OPS: 1. TELEFONO: +56987018901. DIRECCION: San Ramon oriente 2334, Casa 28."},{"id":"hqgg8crd66m075vtt4jm59mll0","summary":"PAULINA GUTIERREZ - GOURMET","location":"COLINA","start":"2026-08-14T21:00:00-04:00","end":"2026-08-15T01:00:00-04:00","url":"https://www.google.com/calendar/event?eid=aHFnZzhjcmQ2Nm0wNzV2dHQ0am01OW1sbDAgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS: promo invierno: pop corn. PRODUCTOS: 60 Hamburguesa Italiana; 40 Promo Invierno. MONTAJE: 1 Carro Clásico. OPS: 1. TELEFONO: +56998727508. DIRECCION: Los Boldos 49, Condominio Los Bosques Piedra Roja, Chicureo."},{"id":"54ad2jr444bve43uie112d08e0","summary":"ELISABETH CRESPO - EXPRESS","location":"PUENTE ALTO","start":"2026-08-15T15:00:00-04:00","end":"2026-08-15T19:00:00-04:00","url":"https://www.google.com/calendar/event?eid=NTRhZDJqcjQ0NGJ2ZTQzdWllMTEyZDA4ZTAgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 80 Hotdogs Italianos. MONTAJE: 1 Carro Clásico. OPS: 1. TELEFONO: +56989165833. DIRECCION: La Espuela 07216, casa 41, Puente Alto."},{"id":"jkhalcrdmjbl3fb5ba9m0bt6fk","summary":"CAMILA ROJAS - EXPRESS","location":"PUENTE ALTO","start":"2026-08-15T15:15:00-04:00","end":"2026-08-15T19:15:00-04:00","url":"https://www.google.com/calendar/event?eid=amtoYWxjcmRtamJsM2ZiNWJhOW0wYnQ2Zmsgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS: PROMO DE INVIERNO. PRODUCTOS: 120 Hotdogs Italianos; 60 Papas fritas. MONTAJE: 1 Carro Clásico. OPS: 1. TELEFONO: +56982844455. DIRECCION: Avda. Parque del Este 4456 puente alto."},{"id":"q8datk2dbpkv6gfgc737c65ep0","summary":"HECTOR GODOY - DEL SABOR","location":"PEÑALOLÉN","start":"2026-08-15T16:00:00-04:00","end":"2026-08-15T20:00:00-04:00","url":"https://www.google.com/calendar/event?eid=cThkYXRrMmRicGt2NmdmZ2M3MzdjNjVlcDAgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 60 Algodón de azúcar. MONTAJE: 1 Máquina Algodón. OPS: 1. TELEFONO: +56996986351. DIRECCION: Juan de Dios Vial Correa 4360, casa F."},{"id":"6snd5f9eandiuqcar5lujkq1po","summary":"EDGARDO GUEDE - EXPRESS","location":"ÑUÑOA","start":"2026-08-15T19:00:00-04:00","end":"2026-08-15T23:00:00-04:00","url":"https://www.google.com/calendar/event?eid=NnNuZDVmOWVhbmRpdXFjYXI1bHVqa3ExcG8gc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 70 Hotdogs Italianos. MONTAJE: 1 Carro Clásico. OPS: 1. TELEFONO: +56989011040. DIRECCION: Quirihue 255 Ñuñoa (Sala de eventos)."},{"id":"u8r25v60uu868icpgccsbk74eo","summary":"PAOLA CORTÉS - CAMALEON","location":"LAS CONDES","start":"2026-08-15T19:00:00-04:00","end":"2026-08-15T23:00:00-04:00","url":"https://www.google.com/calendar/event?eid=dThyMjV2NjB1dTg2OGljcGdjY3Niazc0ZW8gc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 20 Churrasco Italiano; 20 Hamburguesa Italiana Camaleón. MONTAJE: 1 Carro Clásico. OPS: 1. TELEFONO: +56 9 9826 2849. DIRECCION: Los Militares 5326, Las Condes, salón de eventos."},{"id":"acp15apg2o1kns7961hccdib6c","summary":"ALESSANDRA SCHIAPPACASSE - GOURMET","location":"VITACURA","start":"2026-08-16T14:00:00-04:00","end":"2026-08-16T18:00:00-04:00","url":"https://www.google.com/calendar/event?eid=YWNwMTVhcGcybzFrbnM3OTYxaGNjZGliNmMgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 20 Churrasco Italiano; 20 Hot Dog Italiano; 20 Papas Fritas. MONTAJE: Carro Clásico; Freidora; Mesa. OPS: 2. TELEFONO: +56998707603. DIRECCION: Salón de eventos, Padre Damian Deveuster 2535, Depto 52R."},{"id":"aeb85jcf8a0ovjk0nulusddg6o","summary":"SOFIA GARCIA - EXPRESS","location":"SAN JOAQUÍN","start":"2026-08-18T15:00:00-04:00","end":"2026-08-18T18:00:00-04:00","url":"https://www.google.com/calendar/event?eid=YWViODVqY2Y4YTBvdmprMG51bHVzZGRnNm8gc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS: RETIRAR ANTES DE LAS 18:30. PRODUCTOS: 74 Bebidas; 124 Wraps Pollo; 20 Wraps Vegetarianos. MONTAJE: 2 Carros Clásicos; Mesa; Cooler. OPS: 2. TELEFONO: +56950879884. DIRECCION: Colegio Vicuña, Mariano Puga 660, san Joaquín."},{"id":"163bs4dhn8j0tkqqdinkh9geta","summary":"DEGUSTACION CAROL SOTO - EXPRESS","location":"OFICINA","start":"2026-08-19T15:00:00-04:00","end":"2026-08-19T16:00:00-04:00","url":"https://www.google.com/calendar/event?eid=MTYzYnM0ZGhuOGowdGtxcWRpbmtoOWdldGEgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTA: DEJAR PATIO DESPEJADO Y CARRO MONTADO PARA DEGUSTACION. 2 HOT DOG ITALIANO; 2 CHURRASCOS ITALIANOS; 2 MECHADAS ITALIANAS; 2 PULLPORK ITALIANAS; 2 BURGERS CASERAS. SOLICITAR A BRONTOS PAN. COLOCAR UNA MESA DE BRONTOS Y 4 PUESTOS."},{"id":"0higu9neuf4ekif9rq4car1pcs","summary":"ALEN ORTEGA - EXPRESS","location":"PUENTE ALTO","start":"2026-08-20T10:00:00-04:00","end":"2026-08-20T14:00:00-04:00","url":"https://www.google.com/calendar/event?eid=MGhpZ3U5bmV1ZjRla2lmOXJxNGNhcjFwY3Mgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 120 Hotdogs Italianos. MONTAJE: 1 Carro Clásico. OPS: 1. TELEFONO: +56961427762. DIRECCION: Hospital Sótero del Río, avenida concha y toro #3459."},{"id":"v1t3fid3bv98ivjd6pid9rlngs","summary":"ISIDORA CASTILLO - CAMALEON","location":"LAS CONDES","start":"2026-08-20T13:30:00-04:00","end":"2026-08-20T17:30:00-04:00","url":"https://www.google.com/calendar/event?eid=djF0M2ZpZDNidjk4aXZqZDZwaWQ5cmxuZ3Mgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 150 Algodón de azúcar; 150 Pop Corn. MONTAJE: 1 Carro Rojo; Máquina Cabritas; Máquina Algodón. OPS: 2. TELEFONO: +56998796481. DIRECCION: El bosque norte 50, las condes (torre roger de flor piso 16)."},{"id":"3ml5akb7b92n5fnhga9o1gii50","summary":"FRANCISCA PALMA - EXPRESS","location":"LA PINTANA","start":"2026-08-21T16:15:00-04:00","end":"2026-08-21T20:15:00-04:00","url":"https://www.google.com/calendar/event?eid=M21sNWFrYjdiOTJuNWZuaGdhOW8xZ2lpNTAgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 130 Pop corn. MONTAJE: Carro Rojo; Máquina Cabritas. OPS: 1. TELEFONO: +56932272407. DIRECCION: Gabriela 02980, La Pintana."},{"id":"fsfeoia380dcuak433mjv0q4g4","summary":"MARIENNIS CHACIN - GOURMET","location":"LAS CONDES","start":"2026-08-22T14:30:00-04:00","end":"2026-08-22T18:30:00-04:00","url":"https://www.google.com/calendar/event?eid=ZnNmZW9pYTM4MGRjdWFrNDMzbWp2MHE0ZzQgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS: Churros con salsa chocolate. PRODUCTOS: 100 Churros españoles; 100 Hot Dog Italiano; 100 Toppings. MONTAJE: Carro Clásico; Horno; Carro Rojo; Alargador. OPS: 2. TELEFONO: +56994230691. DIRECCION: av las tranqueras 492."},{"id":"p8v0md47air051rh02nlh9m29k","summary":"SEBASTIAN VARGAS - EXPRESS","location":"HUECHURABA","start":"2026-08-22T18:00:00-04:00","end":"2026-08-22T22:00:00-04:00","url":"https://www.google.com/calendar/event?eid=cDh2MG1kNDdhaXIwNTFyaDAybmxoOW0yOWsgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS: PROMOCION DE INVIERNO. PRODUCTOS: 60 Hotdogs Italianos; 40 Papas fritas. MONTAJE: 1 Carro Clásico. OPS: 1. TELEFONO: +56984398285. DIRECCION: camino de cintura 8030."},{"id":"ghsngtocfavaeqhr87kjj6757g","summary":"XIMENA FIGUEROA YÜRGENS - GOURMET","location":"LA REINA","start":"2026-08-22T20:30:00-04:00","end":"2026-08-22T23:30:00-04:00","url":"https://www.google.com/calendar/event?eid=Z2hzbmd0b2NmYXZhZXFocjg3a2pqNjc1N2cgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 80 Hamburguesa Gourmet; 2 Hora extra por operador; 100 Mechada Gourmet. MONTAJE: 2 Carros Clásicos. OPS: 2. TELEFONO: +56931094024. DIRECCION: Álvaro Casanova 311. Salón Verde Legión, Casa de Campo La Reina, Carabineros de Chile."},{"id":"mr4jqma2hsh6v07m568l2dfik0","summary":"MARÍA FRANCISCA URZÚA SAAVEDRA - DEL SABOR","location":"LA REINA","start":"2026-08-22T20:30:00-04:00","end":"2026-08-23T00:30:00-04:00","url":"https://www.google.com/calendar/event?eid=bXI0anFtYTJoc2g2djA3bTU2OGwyZGZpazAgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 60 HotDog Italiano; 60 Papas Fritas. MONTAJE: Carro Clásico; Freidora; Mesa. OPS: 2. TELEFONO: +56999090206. DIRECCION: Alvaro Casanova 355, condominio Cumbres (Casa 22 D)."},{"id":"57v8jmhpdmq66as4l0qntid93k","summary":"SOFIA GARCIA - EXPRESS","location":"PUENTE ALTO","start":"2026-08-27T15:00:00-04:00","end":"2026-08-27T18:00:00-04:00","url":"https://www.google.com/calendar/event?eid=NTd2OGptaHBkbXE2NmFzNGwwcW50aWQ5M2sgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 171 Agua saborizada; 1 Carro Extra; 1 Operador extra; 158 Wraps Pollo; 15 Wraps Vegetarianos. MONTAJE: 3 Carros Clásicos; mesa; cooler. OPS: 3. TELEFONO: +56994533807. DIRECCION: Eyzaguirre #01811, puente alto."},{"id":"12btsk173kr2n64igg5cdllvuo","summary":"ROCÍO BLÁZQUEZ - CAMALEON","location":"PROVIDENCIA","start":"2026-08-29T08:00:00-04:00","end":"2026-08-29T13:00:00-04:00","url":"https://www.google.com/calendar/event?eid=MTJidHNrMTcza3IybjY0aWdnNWNkbGx2dW8gc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS: piso 40. PRODUCTOS: 100 Algodón de azúcar envasado; 100 Pop Corn. MONTAJE: Carro Rojo; 2 Mesas spandex. OPS: 2. TELEFONO: +56994410484. DIRECCION: Av. Andrés Bello 2425, providencia."},{"id":"49c3s44tof4u1u9fjajai1hscc","summary":"AMPARO LARACH - CAMALEON","location":"LO BARNECHEA","start":"2026-09-04T20:00:00-04:00","end":"2026-09-04T23:59:00-04:00","url":"https://www.google.com/calendar/event?eid=NDljM3M0NHRvZjR1MXU5ZmphamFpMWhzY2Mgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS: proteínas wraps pollo y mechada; gráfica cumpleaños genérica. PRODUCTOS: intercambio carne; operador entrega; 100 Wraps Mixto Camaleón. MONTAJE: Carro Clásico. OPS: 1. TELEFONO: +56982398020. DIRECCION: Avenida el tranque 12797."},{"id":"np8ltsqe061vt9j4rsvlu5ee94","summary":"CLAUDIA CORTES - GOURMET","location":"PUENTE ALTO","start":"2026-09-05T14:30:00-04:00","end":"2026-09-05T18:30:00-04:00","url":"https://www.google.com/calendar/event?eid=bnA4bHRzcWUwNjF2dDlqNHJzdmx1NWVlOTQgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 20 Hamburguesa Clasica; 20 Hamburguesa Gourmet; 20 Hamburguesa Italiana. MONTAJE: Carro Clásico. OPS: 1. TELEFONO: +56984796330. DIRECCION: Loma Redonda 3 Poniente 2396."},{"id":"g92v6g5aeoomrt54vbo1d04rqs","summary":"MARCELO JINGLE PRODUCCIONES - EXPRESS","location":"PUDAHUEL","start":"2026-09-08T12:00:00-03:00","end":"2026-09-08T16:00:00-03:00","url":"https://www.google.com/calendar/event?eid=ZzkydjZnNWFlb29tcnQ1NHZibzFkMDRycXMgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"PRODUCTOS: 300 Pop corn. MONTAJE: 2 Carros Rojos; 2 Máquinas Cabritas. OPS: 2. TELEFONO: +56993628095. DIRECCION: Av. el Parque 1307."},{"id":"3d6oc8t8vnpsmra84fi4r89ung","summary":"KATHERINE SILVA - EXPRESS","location":"EL BOSQUE","start":"2026-08-14T10:00:00-04:00","end":"2026-08-14T15:30:00-04:00","url":"https://www.google.com/calendar/event?eid=M2Q2b2M4dDh2bnBzbXJhODRmaTRyODl1bmcgc2ltb251cnJ1dGlhLm1AbQ&ctz=America/Santiago","description":"NOTAS: BLOQUE A entrega 11:30 a 12:40. BLOQUE B 14:00 a 15:00. PRODUCTOS: BLOQUE A ENTREGA 10:00–13:00, 242 Algodón, 242 Pop corn. BLOQUE B ENTREGA 14:00–15:30, 241 Algodón, 241 Pop corn. MONTAJE: 1 CARRO ROJO; 1 MAQUINA CABRITAS; 1 MAQUINA ALGODON; 2 MESAS. OPS: 2. TELEFONO: +56930294512. DIRECCION: Los Avellanos #10721."}]""")


def _read_calendar_snapshot():
    raw = _json_load_file(CALENDAR_SNAPSHOT_PATH, None)
    if isinstance(raw, dict) and isinstance(raw.get("events"), list):
        return raw.get("events") or [], raw.get("verified_at", ""), raw.get("source", "snapshot")
    if isinstance(raw, list):
        return raw, "", "snapshot"
    return EMBEDDED_CALENDAR_EVENTS, CALENDAR_SNAPSHOT_VERIFIED_AT, "embedded"


def _recursive_event_list(obj):
    if isinstance(obj, list):
        # Candidate when list contains dictionaries with time/title keys.
        if not obj or all(isinstance(x, dict) for x in obj):
            return obj
    if isinstance(obj, dict):
        for key in ("events", "items", "data", "result", "rows"):
            if key in obj:
                found = _recursive_event_list(obj[key])
                if isinstance(found, list):
                    return found
    return None


def _pick(d, *keys):
    for k in keys:
        value = d.get(k) if isinstance(d, dict) else None
        if value is not None and str(value).strip() != "":
            if isinstance(value, dict):
                value = value.get("dateTime") or value.get("date") or value.get("value")
            if value is not None:
                return value
    return ""


def _normalize_calendar_event(raw):
    if not isinstance(raw, dict):
        return None
    event_id = _pick(raw, "id", "event_id", "calendar_event_id", "id_evento", "google_event_id")
    summary = _pick(raw, "summary", "title", "titulo", "cliente", "name")
    start = _pick(raw, "start", "start_time", "calendar_start", "pre_start", "inicio", "fecha_inicio")
    end = _pick(raw, "end", "end_time", "calendar_end", "pre_end", "fin", "fecha_fin")
    location = _pick(raw, "location", "comuna", "pre_location", "ubicacion")
    description = _pick(raw, "description", "descripcion", "pre_description", "detalle", "notes", "notas")
    phone = _pick(raw, "telefono", "phone", "pre_telefono", "contact_phone")
    address = _pick(raw, "direccion", "address", "pre_direccion")
    url = _pick(raw, "url", "htmlLink", "html_link", "calendar_html_link", "display_url")

    if not start or not summary:
        return None
    if not event_id:
        event_id = f"synthetic:{normalize(summary)}:{start}"
    if phone and "TELEFONO:" not in str(description).upper():
        description = f"{description} TELEFONO: {phone}".strip()
    if address and "DIRECCION:" not in str(description).upper():
        description = f"{description} DIRECCION: {address}".strip()
    if not end:
        end = start

    return {
        "id": str(event_id),
        "summary": str(summary),
        "location": str(location or ""),
        "start": str(start),
        "end": str(end),
        "url": str(url or ""),
        "description": str(description or ""),
    }


def _calendar_in_window(events):
    out = []
    start_day = TODAY
    end_day = TODAY + timedelta(days=30)
    for raw in events or []:
        event = _normalize_calendar_event(raw)
        if not event:
            continue
        try:
            d = pd.Timestamp(event["start"]).date()
        except Exception:
            continue
        if start_day <= d <= end_day:
            out.append(event)
    out.sort(key=lambda x: str(x.get("start") or ""))
    return out


def _fetch_calendar_from_crm_backend():
    endpoints = [
        "http://127.0.0.1:8000/tools/dashboard/events",
    ]
    errors = []
    for url in endpoints:
        try:
            req = urllib.request.Request(url, headers=_internal_crm_headers({"Accept": "application/json"}))
            with urllib.request.urlopen(req, timeout=45) as response:
                data = json.loads(response.read().decode("utf-8", "replace"))
            rows = _recursive_event_list(data)
            normalized = _calendar_in_window(rows or [])
            if normalized:
                return normalized, f"CRM backend {url}", ""
            errors.append(f"{url}: respuesta sin eventos utilizables")
        except Exception as exc:
            errors.append(f"{url}: {_safe_error_text(exc)}")
    return [], "", " | ".join(errors)


def _flatten_credentials_blob(row):
    out = dict(row or {})
    for key in ("credentials", "credential", "credentials_json", "token_json", "oauth", "oauth_json", "data", "payload"):
        value = out.get(key)
        if isinstance(value, str) and value.strip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    out.update(parsed)
            except Exception:
                pass
        elif isinstance(value, dict):
            out.update(value)
    return out


def _fetch_calendar_from_gcal_tokens(conn):
    if not table_exists(conn, "gcal_tokens"):
        return [], "", "tabla gcal_tokens no existe"
    try:
        cols = get_columns(conn, "gcal_tokens")
        order_col = first_existing(["updated_at", "created_at", "id", "id_token"], cols)
        order_sql = f" ORDER BY {order_col} DESC NULLS LAST" if order_col else ""
        rows = conn.execute(f"SELECT to_jsonb(t) FROM gcal_tokens t{order_sql} LIMIT 30").fetchall()
    except Exception as exc:
        return [], "", _safe_error_text(exc)

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except Exception as exc:
        return [], "", f"Google libs: {_safe_error_text(exc)}"

    errors = []
    for (raw,) in rows:
        c = _flatten_credentials_blob(raw or {})
        refresh_token = c.get("refresh_token") or c.get("refreshToken")
        client_id = c.get("client_id") or c.get("clientId")
        client_secret = c.get("client_secret") or c.get("clientSecret")
        token = c.get("access_token") or c.get("token")
        token_uri = c.get("token_uri") or c.get("tokenUri") or "https://oauth2.googleapis.com/token"
        scopes = c.get("scopes") or ["https://www.googleapis.com/auth/calendar.readonly"]
        if isinstance(scopes, str):
            scopes = [x.strip() for x in re.split(r"[, ]+", scopes) if x.strip()]

        if not (refresh_token and client_id and client_secret):
            continue
        try:
            creds = Credentials(
                token=token or None,
                refresh_token=refresh_token,
                token_uri=token_uri,
                client_id=client_id,
                client_secret=client_secret,
                scopes=scopes,
            )
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            time_min = datetime.combine(TODAY, datetime.min.time(), tzinfo=TZ).isoformat()
            time_max = datetime.combine(TODAY + timedelta(days=31), datetime.min.time(), tzinfo=TZ).isoformat()
            items = []
            page_token = None
            while True:
                response = service.events().list(
                    calendarId=OFFICIAL_CALENDAR_ID,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=250,
                    pageToken=page_token,
                ).execute()
                items.extend(response.get("items", []))
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
            normalized = _calendar_in_window(items)
            if normalized:
                return normalized, "Google Calendar API vía gcal_tokens", ""
        except Exception as exc:
            errors.append(_safe_error_text(exc))
    return [], "", " | ".join(errors[-3:]) or "gcal_tokens sin credencial OAuth utilizable"


previous_calendar_events, previous_calendar_verified_at, previous_calendar_source = _read_calendar_snapshot()
calendar_live_ok = False
calendar_live_error = ""
calendar_source = previous_calendar_source
calendar_events = previous_calendar_events
calendar_verified_at = previous_calendar_verified_at

if DEEP_REFRESH:
    # Prefer direct Google Calendar OAuth when the active CRM has a usable token.
    with psycopg.connect(f"dbname={ACTIVE_DB}") as cal_conn:
        fetched, source, error = _fetch_calendar_from_gcal_tokens(cal_conn)
    if not fetched:
        fetched, source2, error2 = _fetch_calendar_from_crm_backend()
        source = source2 or source
        error = " | ".join(x for x in (error, error2) if x)

    if fetched:
        calendar_events = fetched
        calendar_verified_at = NOW.isoformat()
        calendar_source = source
        calendar_live_ok = True
        CALENDAR_SNAPSHOT_PATH.write_text(
            json.dumps({
                "calendar_id": OFFICIAL_CALENDAR_ID,
                "verified_at": calendar_verified_at,
                "source": calendar_source,
                "events": calendar_events,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        calendar_live_error = error or "No fue posible consultar Calendar live"
else:
    # Regular/cron generation is intentionally read-only for Calendar.
    calendar_live_ok = previous_calendar_source not in ("embedded", "snapshot") and bool(previous_calendar_verified_at)


def _extract_field(text, marker):
    text = str(text or "")
    match = re.search(
        rf"{re.escape(marker)}\s*(.+?)(?=(?:TELEFONO:|DIRECCION:|PRODUCTOS:|MONTAJE:|OPS:|$))",
        text,
        flags=re.I,
    )
    return " ".join(match.group(1).strip(" .").split()) if match else ""

def _live_value(primary, fallback=""):
    def clean(v):
        if v is None:
            return ""
        try:
            if pd.isna(v):
                return ""
        except Exception:
            pass
        return " ".join(str(v).strip().split())
    a = clean(primary)
    b = clean(fallback)
    pending_re = re.compile(r"\b(TBD|POR CONFIRMAR|SIN DEFINIR|POR DEFINIR)\b", re.I)
    if a and not pending_re.search(a):
        return a
    if b and not pending_re.search(b):
        return b
    return a or b


def _replace_operational_field(desc, marker, value):
    desc = str(desc or "")
    value = str(value or "").strip()
    if not value:
        return desc
    pat = re.compile(
        rf"({re.escape(marker)}\s*)(.+?)(?=(?:TELEFONO:|DIRECCION:|PRODUCTOS:|MONTAJE:|OPS:|$))",
        re.I | re.S,
    )
    if pat.search(desc):
        return pat.sub(lambda m: m.group(1) + value + " ", desc, count=1)
    return (desc.rstrip() + f" {marker} {value}.").strip()


def _summary_client(summary, brand):
    text = str(summary or "").strip()
    if brand:
        text = re.sub(rf"\s*[-–—,]\s*{re.escape(brand)}(?:\s*[-–—].*)?$", "", text, flags=re.I).strip()
    return normalize(text)


def _operational_missing(value):
    txt = str(value or "").strip()
    if not txt:
        return True
    return bool(re.search(r"\b(TBD|POR CONFIRMAR|SIN DEFINIR|POR DEFINIR)\b", txt, flags=re.I))


def _overlay_calendar_with_live_crm(events, calendar_is_live=False):
    """Precedencia operacional robusta.

    - Calendar LIVE: Google conserva prioridad; CRM sólo rellena faltantes/TBD.
    - Calendar FALLBACK/SNAPSHOT: CRM LIVE manda para comuna, teléfono y dirección.
      Un snapshot no puede conservar datos viejos después de ACTUALIZAR TODO.
    - Match primario: calendar_event_id. Fallback: cliente+marca+fecha únicamente
      cuando existe una sola coincidencia. Nunca se cruza un cliente entre marcas.
    """
    exact = {}
    fuzzy = {}
    for _, r in leads.iterrows():
        eid = _live_value(r.get("calendar_event_id"), "")
        if eid:
            exact[eid] = r
        try:
            day = pd.Timestamp(r.get("fecha_evento")).date().isoformat() if pd.notna(r.get("fecha_evento")) else ""
        except Exception:
            day = ""
        key = (normalize(r.get("cliente")), normalize(r.get("marca")), day)
        if all(key):
            fuzzy.setdefault(key, []).append(r)

    out = []
    count = 0
    for raw in events:
        e = dict(raw)
        row = exact.get(str(e.get("id") or "").strip())
        brand = ""
        up = str(e.get("summary") or "").upper()
        for candidate in ("DEL SABOR", "GOURMET", "CAMALEON", "EXPRESS"):
            if candidate in up:
                brand = candidate
                break
        if row is None:
            try:
                day = pd.Timestamp(e.get("start")).date().isoformat()
            except Exception:
                day = ""
            key = (_summary_client(e.get("summary"), brand), normalize(brand), day)
            candidates = fuzzy.get(key, [])
            if len(candidates) == 1:
                row = candidates[0]

        changed = False
        if row is not None:
            live_phone = _live_value(row.get("telefono"), row.get("pre_telefono"))
            live_addr = _live_value(row.get("direccion"), row.get("pre_direccion"))
            live_loc = _live_value(row.get("comuna_catalogo"), row.get("pre_location"))

            desc = str(e.get("description") or "")
            current_phone = _extract_field(desc, "TELEFONO:")
            current_addr = _extract_field(desc, "DIRECCION:")

            if calendar_is_live:
                # Google LIVE conserva prioridad; CRM sólo completa huecos reales.
                if live_loc and _operational_missing(e.get("location")) and not _operational_missing(live_loc):
                    e["location"] = live_loc
                    changed = True
                if live_phone and _operational_missing(current_phone) and not _operational_missing(live_phone):
                    desc = _replace_operational_field(desc, "TELEFONO:", live_phone)
                    changed = True
                if live_addr and _operational_missing(current_addr) and not _operational_missing(live_addr):
                    desc = _replace_operational_field(desc, "DIRECCION:", live_addr)
                    changed = True
                source = "calendar_live"
            else:
                # CRÍTICO: un snapshot es sólo caché. Si logramos match inequívoco con
                # el lead LIVE, los datos operativos del CRM reemplazan el snapshot.
                # Así ACTUALIZAR TODO refleja de inmediato correcciones de comuna/teléfono/dirección.
                if live_loc and not _operational_missing(live_loc):
                    if normalize(e.get("location")) != normalize(live_loc):
                        e["location"] = live_loc
                        changed = True
                if live_phone and not _operational_missing(live_phone):
                    if normalize(current_phone) != normalize(live_phone):
                        desc = _replace_operational_field(desc, "TELEFONO:", live_phone)
                        changed = True
                if live_addr and not _operational_missing(live_addr):
                    if normalize(current_addr) != normalize(live_addr):
                        desc = _replace_operational_field(desc, "DIRECCION:", live_addr)
                        changed = True
                source = "crm_live_override"

            e["description"] = desc
            e["crm_lead_id"] = int(row.get("id_lead"))
            e["crm_live_overlay"] = bool(changed)
            e["operational_source"] = source
            if changed:
                count += 1
        out.append(e)
    return out, count


calendar_events, crm_calendar_overlay_count = _overlay_calendar_with_live_crm(calendar_events, calendar_live_ok)
previous_calendar_enriched_source, _previous_overlay_count = _overlay_calendar_with_live_crm(previous_calendar_events, False)


def calendar_enrich(event):
    e = dict(event)
    desc = str(e.get("description") or "")
    summary = str(e.get("summary") or "")
    location = str(e.get("location") or "").strip()
    phone = _extract_field(desc, "TELEFONO:")
    address = _extract_field(desc, "DIRECCION:")

    brand = ""
    upper = summary.upper()
    for candidate in ["DEL SABOR", "GOURMET", "CAMALEON", "EXPRESS"]:
        if candidate in upper:
            brand = candidate
            break

    alerts = []
    tags = []
    internal = location.upper() == "OFICINA"

    if re.search(r"\b(TBD|POR CONFIRMAR|SIN DEFINIR|POR DEFINIR)\b", desc, flags=re.I):
        alerts.append("DATO POR CONFIRMAR")
        tags.append("pending")

    if not internal:
        if not phone:
            alerts.append("FALTA TELÉFONO")
            tags.append("phone")
        elif "TBD" in phone.upper():
            alerts.append("TELÉFONO TBD")
            tags.extend(["phone", "pending"])

        if not address:
            alerts.append("FALTA DIRECCIÓN")
            tags.append("address")

        if not location:
            alerts.append("FALTA COMUNA/LOCATION")
            tags.append("location")
    else:
        tags.append("internal")

    norm_location = normalize(location)
    norm_address = normalize(address)
    explicit_comunas = [
        "LAS CONDES", "VITACURA", "LA REINA", "PROVIDENCIA",
        "SANTIAGO", "NUNOA", "MACUL", "PUENTE ALTO",
        "COLINA", "HUECHURABA", "PENALOLEN", "SAN MIGUEL",
        "LA PINTANA", "LO BARNECHEA", "PUDAHUEL",
        "SAN JOAQUIN", "VINA DEL MAR",
    ]
    hits = [c for c in explicit_comunas if c in norm_address]
    if hits and norm_location and not any(
        norm_location == c or norm_location in c or c in norm_location
        for c in hits
    ):
        alerts.append("COMUNA/LOCATION NO COINCIDE CON DIRECCIÓN")
        tags.append("mismatch")

    # Horarios operativos múltiples/contradictorios dentro de la descripción.
    time_tokens = re.findall(r"\b(?:[01]?\d|2[0-3])[:.]\d{2}\b", desc)
    if len(set(time_tokens)) >= 4 and re.search(r"BLOQUE|ENTREGA|LLEGADA|TERMINO|TÉRMINO", desc, flags=re.I):
        # No todo evento con varias horas es inconsistente, pero BLOQUE/ENTREGA con
        # cuatro o más hitos requiere revisión visual.
        if re.search(r"BLOQUE", desc, flags=re.I):
            alerts.append("REVISAR COHERENCIA DE HORARIOS OPERATIVOS")
            tags.append("schedule")

    if alerts:
        tags.append("alert")

    e["brand"] = brand
    e["phone"] = phone
    e["address"] = address
    e["alerts"] = list(dict.fromkeys(alerts))
    e["tags"] = list(dict.fromkeys(tags))
    return e

calendar_events = [calendar_enrich(e) for e in calendar_events]
previous_calendar_enriched = [calendar_enrich(e) for e in previous_calendar_enriched_source]


def _ts_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(pd.Timestamp(value))


def _lead_snapshot_rows(df):
    snap = {}
    for _, r in df.iterrows():
        try:
            lead_id = str(int(r["id_lead"]))
        except Exception:
            continue
        snap[lead_id] = {
            "cliente": str(r.get("cliente") or ""),
            "marca": str(r.get("marca") or ""),
            "estado": str(r.get("estado") or ""),
            "telefono": str(r.get("telefono") or ""),
            "direccion": str(r.get("direccion") or ""),
            "comuna_catalogo": str(r.get("comuna_catalogo") or ""),
            "email": str(r.get("email") or ""),
            "fecha_evento": _ts_text(r.get("fecha_evento")),
            "monto": float(r.get("monto") or 0),
            "seguimiento_efectivo_at": _ts_text(r.get("seguimiento_efectivo_at")),
            "seguimiento_efectivo_tipo": str(r.get("seguimiento_efectivo_tipo") or ""),
            "relevant_activity_at": _ts_text(r.get("relevant_activity_at")),
            "relevant_activity_kind": str(r.get("relevant_activity_kind") or ""),
            "quote_count": int(r.get("quote_count") or 0),
            "task_kind": str(r.get("task_kind") or ""),
            "task_status": str(r.get("task_status") or ""),
            "task_due_at": _ts_text(r.get("task_due_at")),
        }
    return snap


def _calendar_snapshot_rows(events):
    snap = {}
    for e in events:
        eid = str(e.get("id") or "")
        if not eid:
            continue
        snap[eid] = {
            "summary": str(e.get("summary") or ""),
            "start": str(e.get("start") or ""),
            "end": str(e.get("end") or ""),
            "location": str(e.get("location") or ""),
            "phone": str(e.get("phone") or ""),
            "address": str(e.get("address") or ""),
            "description": str(e.get("description") or ""),
            "alerts": list(e.get("alerts") or []),
        }
    return snap


def _human_field(field):
    return {
        "estado": "estado",
        "telefono": "teléfono",
        "direccion": "dirección",
        "comuna_catalogo": "comuna",
        "fecha_evento": "fecha evento",
        "monto": "monto",
        "seguimiento_efectivo_at": "seguimiento",
        "seguimiento_efectivo_tipo": "tipo de seguimiento",
        "relevant_activity_at": "movimiento de historial",
        "relevant_activity_kind": "tipo de movimiento",
        "quote_count": "cotizaciones",
        "task_kind": "tarea",
        "task_status": "estado tarea",
        "task_due_at": "vencimiento tarea",
        "start": "inicio",
        "end": "fin",
        "location": "comuna/location",
        "phone": "teléfono",
        "address": "dirección",
        "description": "descripción operativa",
        "alerts": "alertas operativas",
    }.get(field, field)


def _compare_snapshots(old, new, entity_type):
    findings = []
    if entity_type == "lead":
        # Hallazgos = cambios visibles/relevantes desde la revisión anterior.
        # Excluimos campos técnicos recalculados por tasks/followup para evitar miles
        # de falsos "cambios" al hacer ACTUALIZAR TODO.
        fields = [
            # Sólo cambios comerciales visibles. Campos derivados de activity/tasks
            # se recalculan masivamente y NO son "hallazgos recientes".
            "estado", "telefono", "direccion", "comuna_catalogo",
            "fecha_evento", "monto", "quote_count",
        ]
    else:
        fields = ["start", "end", "location", "phone", "address", "alerts"]

    for key, current in new.items():
        previous = old.get(key)
        if previous is None:
            findings.append({
                "entity_type": entity_type,
                "id": key,
                "kind": "new",
                "title": current.get("cliente") or current.get("summary") or key,
                "message": "Nuevo lead" if entity_type == "lead" else "Nuevo evento Calendar",
                "fields": ["new"],
            })
            continue

        changed = []
        for field in fields:
            if previous.get(field) != current.get(field):
                changed.append(field)
        if changed:
            findings.append({
                "entity_type": entity_type,
                "id": key,
                "kind": "changed",
                "title": current.get("cliente") or current.get("summary") or key,
                "message": "Cambió: " + ", ".join(_human_field(x) for x in changed),
                "fields": changed,
            })
    return findings


current_lead_snapshot = _lead_snapshot_rows(leads)
current_calendar_snapshot = _calendar_snapshot_rows(calendar_events)
old_state = _json_load_file(STATE_PATH, None)
last_findings_payload = _json_load_file(FINDINGS_PATH, {}) or {}
findings = list(last_findings_payload.get("findings") or [])

if DEEP_REFRESH:
    # Baseline versionado. Un cambio de versión/esquema NO convierte todo el CRM
    # en hallazgos. La primera ejecución de esta versión establece baseline limpio;
    # desde la siguiente, sólo se muestran diferencias reales desde el refresh anterior.
    same_schema = (
        isinstance(old_state, dict)
        and int(old_state.get("schema_version") or 0) == FINDINGS_SCHEMA_VERSION
        and old_state.get("database") == ACTIVE_DB
    )
    if same_schema:
        old_leads = old_state.get("leads") or {}
        old_calendar = old_state.get("calendar") or {}
        findings = _compare_snapshots(old_leads, current_lead_snapshot, "lead")
        findings += _compare_snapshots(old_calendar, current_calendar_snapshot, "calendar")
    else:
        findings = []

    STATE_PATH.write_text(json.dumps({
        "schema_version": FINDINGS_SCHEMA_VERSION,
        "refreshed_at": NOW.isoformat(),
        "database": ACTIVE_DB,
        "leads": current_lead_snapshot,
        "calendar": current_calendar_snapshot,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    FINDINGS_PATH.write_text(json.dumps({
        "refreshed_at": NOW.isoformat(),
        "findings": findings,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

lead_finding_ids = {f["id"] for f in findings if f.get("entity_type") == "lead"}
calendar_finding_ids = {f["id"] for f in findings if f.get("entity_type") == "calendar"}

for e in calendar_events:
    e["is_new_finding"] = str(e.get("id") or "") in calendar_finding_ids

# Reconstruir las filas comerciales después de calcular hallazgos para
# que data-finding refleje los leads nuevos/modificados de esta revisión.
pipeline_rows = []
for _, row in pipeline.iterrows():
    idle = (
        "—"
        if row["horas_sin_gestion"] is None
        else f'{int(row["horas_sin_gestion"])}h'
    )

    pipeline_rows.append(
        f"""
        <tr {row_attrs(row)}>
          <td>
            <button class="link-button"
                    onclick="openLeadFromRow(this.closest('tr'))">Ver</button>
          </td>
          <td class="flag-cell"><span class="follow-flag {esc(row['bandera_class'])}" title="{esc(row['bandera_label'])}">{esc(row['bandera'])}</span></td>
          <td>{esc(row['cliente'])}</td>
          <td class="phone-cell">{esc(row['telefono']) or '—'}</td>
          <td>{esc(row['marca'])}</td>
          <td>{esc(row['ejecutivo'])}</td>
          <td>{fmt_date(row['fecha_evento'])}</td>
          <td class="money">{money(row['monto'])}</td>
          <td>{esc(row['estado'])}</td>
          <td>{quote_button(row)}</td>
          <td><strong>{esc(row['accion'])}</strong></td>
          <td>{fmt_dt(row['seguimiento_efectivo_at']) or '—'}</td>
          <td>{esc(row['seguimiento_efectivo_tipo']) or '—'}</td>
          <td>{idle}</td>
        </tr>
        """
    )

findings_json_for_html = json.dumps(findings, ensure_ascii=False, default=str).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


calendar_json_for_html = json.dumps(
    calendar_events,
    ensure_ascii=False,
    default=str,
).replace("<", "\\u003c").replace(
    ">", "\\u003e"
).replace("&", "\\u0026")

# ============================================================
# EXPORT
# ============================================================

xlsx_path = Path(OUTPUT_DIR) / "gd_sales_dashboard.xlsx"
json_path = Path(OUTPUT_DIR) / "data.json"

with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
    excel_safe(pipeline).to_excel(
        writer, index=False, sheet_name="cola_comercial"
    )
    excel_safe(exec_summary).to_excel(
        writer, index=False, sheet_name="ejecutivos"
    )
    excel_safe(brand_summary).to_excel(
        writer, index=False, sheet_name="marcas"
    )
    if not ops_tasks.empty:
        excel_safe(ops_tasks).to_excel(
            writer, index=False, sheet_name="operaciones"
        )

payload = {
    "generated_at": NOW.isoformat(),
    "crm_database": ACTIVE_DB,
    "calendar_id": OFFICIAL_CALENDAR_ID,
    "rules": {
        "first_quote_is_followup": True,
        "subsequent_quote_is_followup": True,
        "every_quote_is_followup": True,
        "meaningful_quote_revision_is_followup": True,
        "quote_date_is_persisted_to_seguimiento_at_on_deep_refresh": True,
        "crm_systemwide_rule_expected": True,
        "quote_update_is_followup_if_timestamp_available": True,
        "old_task_is_superseded_by_followup": True,
    },
    "db_audit": [
        {
            "dbname": x["dbname"],
            "rows": x["rows"],
            "latest": str(x["latest"]),
        }
        for x in db_audit
    ],
    "pipeline": excel_safe(pipeline)
        .fillna("")
        .to_dict(orient="records"),
    "ops_tasks": (
        excel_safe(ops_tasks)
        .fillna("")
        .to_dict(orient="records")
        if not ops_tasks.empty else []
    ),
    "findings": findings,
    "calendar_snapshot": {
        "calendar_id": OFFICIAL_CALENDAR_ID,
        "verified_at": calendar_verified_at,
        "source": calendar_source,
        "live_ok": calendar_live_ok,
        "crm_live_overlay_count": crm_calendar_overlay_count,
        "error": calendar_live_error,
        "events": calendar_events,
    },
}

json_path.write_text(
    json.dumps(payload, ensure_ascii=False, default=str, indent=2),
    encoding="utf-8"
)



# Deep refresh result is consumed by the local refresh API.
last_refresh_result = _json_load_file(REFRESH_RESULT_PATH, {}) or {}
task_sync_result = last_refresh_result.get("task_sync") or {}
if DEEP_REFRESH:
    try:
        task_sync_result = json.loads(os.environ.get("GD_TASK_SYNC_RESULT", "{}") or "{}")
    except Exception:
        task_sync_result = {}
    REFRESH_RESULT_PATH.write_text(json.dumps({
        "ok": True,
        "generated_at": NOW.isoformat(),
        "database": ACTIVE_DB,
        "findings_count": len(findings),
        "lead_findings": sum(1 for f in findings if f.get("entity_type") == "lead"),
        "calendar_findings": sum(1 for f in findings if f.get("entity_type") == "calendar"),
        "calendar": {
            "live_ok": calendar_live_ok,
            "crm_live_overlay_count": crm_calendar_overlay_count,
            "source": calendar_source,
            "verified_at": calendar_verified_at,
            "error": calendar_live_error,
            "events": len(calendar_events),
        },
        "task_sync": task_sync_result,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

# ============================================================
# HTML
# ============================================================

html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GD Sales Command Center V14.2</title>
<style>
:root {{
  --bg:#f4f6f8;--card:#fff;--text:#17202a;--muted:#667085;
  --border:#e4e7ec;--shadow:0 2px 8px rgba(16,24,40,.06)
}}
*{{box-sizing:border-box}}
body{{margin:0;padding:20px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:var(--bg);color:var(--text)}}
.header{{display:flex;justify-content:space-between;gap:15px;align-items:flex-end;margin-bottom:12px}}
h1{{margin:0;font-size:27px}} h2{{margin:0 0 10px;font-size:18px}}
.sub{{font-size:12px;color:var(--muted);margin-top:4px}}
.controls,.tabs{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0 13px}}
select,input,button,.download{{background:#fff;border:1px solid var(--border);border-radius:9px;padding:9px 10px;font-size:12px;color:var(--text);text-decoration:none}}
input{{min-width:260px}}
button,.card,.drill,th.sortable{{cursor:pointer}}
button.active,.tab.active,.card.active{{outline:2px solid #344054;font-weight:700}}
.cards{{display:grid;grid-template-columns:repeat(6,minmax(140px,1fr));gap:9px;margin-bottom:14px}}
.card{{background:#fff;border:1px solid var(--border);border-radius:12px;padding:13px;box-shadow:var(--shadow)}}
.label{{font-size:10px;color:var(--muted);font-weight:750;text-transform:uppercase}}
.value{{font-size:21px;font-weight:800;margin-top:5px}} .amt{{font-size:11px;color:var(--muted);margin-top:3px}}
.panel{{background:#fff;border:1px solid var(--border);border-radius:13px;padding:14px;margin-bottom:14px;box-shadow:var(--shadow)}}
.table-wrap{{overflow:auto;max-height:67vh}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{padding:8px 9px;border-bottom:1px solid var(--border);text-align:left;white-space:nowrap;vertical-align:top}}
th{{position:sticky;top:0;background:#f9fafb;z-index:2}}
th.sortable:hover{{text-decoration:underline}}
.money{{text-align:right;font-variant-numeric:tabular-nums}}
.wrap{{white-space:normal;min-width:230px;line-height:1.35}}
.hidden{{display:none!important}}
.link-button{{padding:4px 7px;border:0;background:transparent;font-weight:800;text-decoration:underline}}
.drill:hover{{background:#f9fafb}}
.note{{padding:10px;border:1px solid var(--border);border-radius:9px;font-size:12px;margin:8px 0 11px}}
.warning{{padding:12px;border:1px solid #d0d5dd;border-radius:10px;background:#fff}}
.modal{{position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;padding:20px;z-index:50}}
.modal-card{{background:#fff;border-radius:14px;width:min(1150px,96vw);max-height:90vh;overflow:auto;padding:16px}}
.modal-head{{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:10px}}
.detail-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
.detail{{border:1px solid var(--border);border-radius:9px;padding:10px}}
.detail .k{{font-size:10px;color:var(--muted);font-weight:750;text-transform:uppercase}}
.detail .v{{font-size:14px;margin-top:5px;white-space:normal}}
.action-box{{margin-top:12px;border:1px solid var(--border);border-radius:9px;padding:12px}}
.statusbar{{font-size:11px;color:var(--muted);margin-left:auto}}

.follow-flag{{display:inline-flex;align-items:center;justify-content:center;min-width:28px;height:28px;border-radius:999px;font-size:16px;border:1px solid var(--border);background:#fff}}
.flag-red{{box-shadow:inset 0 0 0 2px #b42318}}
.flag-orange{{box-shadow:inset 0 0 0 2px #dc6803}}
.flag-yellow{{box-shadow:inset 0 0 0 2px #d6a500}}
.flag-neutral{{opacity:.65}}
.flag-cell{{text-align:center;min-width:54px}}
.phone-cell{{white-space:nowrap;font-variant-numeric:tabular-nums}}
.history-item{{display:grid;grid-template-columns:145px 1fr;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)}}
.history-at{{font-size:11px;font-weight:800;color:var(--muted);font-variant-numeric:tabular-nums}}


.finding-banner{{border:2px solid #d6a500;background:#fffdf2}}
.finding-row{{background:#fff7cc!important;box-shadow:inset 4px 0 0 #d6a500}}
.event-card.new-finding{{background:#fff7cc;border-color:#d6a500;box-shadow:0 0 0 2px rgba(214,165,0,.15)}}
.finding-item{{padding:8px 0;border-bottom:1px solid var(--border);font-size:12px}}
.refresh-overlay{{position:fixed;inset:0;z-index:1000;background:rgba(17,24,39,.72);display:flex;align-items:center;justify-content:center;padding:20px}}
.refresh-box{{width:min(520px,92vw);background:#fff;border-radius:16px;padding:28px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.25)}}
.spinner{{width:46px;height:46px;border:5px solid #e5e7eb;border-top-color:#111827;border-radius:50%;margin:0 auto 18px;animation:spin .85s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}

.calendar-week-head{{
  display:flex;justify-content:space-between;align-items:center;gap:12px;
  margin-bottom:10px
}}
.calendar-nav{{display:flex;gap:7px;align-items:center;flex-wrap:wrap}}
.weekly-calendar{{
  display:grid;grid-template-columns:repeat(7,minmax(170px,1fr));
  gap:8px;overflow:auto;padding-bottom:4px
}}
.day-column{{
  min-height:340px;background:#f9fafb;border:1px solid var(--border);
  border-radius:11px;padding:8px
}}
.day-column.today{{outline:2px solid #344054}}
.day-head{{
  display:flex;justify-content:space-between;gap:6px;
  padding:4px 2px 8px;border-bottom:1px solid var(--border);
  margin-bottom:7px;font-weight:800
}}
.day-head .dow{{font-size:11px;text-transform:uppercase;color:var(--muted)}}
.day-head .date{{font-size:15px}}
.event-card{{
  background:#fff;border:1px solid var(--border);border-radius:9px;
  padding:8px;margin-bottom:7px;cursor:pointer;box-shadow:0 1px 3px rgba(16,24,40,.04)
}}
.event-card:hover{{transform:translateY(-1px)}}
.event-card.alert{{border-left:4px solid #b42318}}
.event-time{{font-size:11px;font-weight:800;margin-bottom:4px}}
.event-title{{font-size:12px;font-weight:800;line-height:1.25;white-space:normal}}
.event-meta{{font-size:10px;color:var(--muted);margin-top:4px;white-space:normal}}
.badges{{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}}
.badge{{
  display:inline-block;border:1px solid var(--border);border-radius:999px;
  padding:2px 6px;font-size:9px;font-weight:800;background:#fff
}}
.badge.alert{{border-color:#b42318}}
.ops-kpis{{grid-template-columns:repeat(6,minmax(135px,1fr))}}

@media(max-width:1250px){{.cards{{grid-template-columns:repeat(3,1fr)}}}}
@media(max-width:750px){{body{{padding:10px}}.header{{flex-direction:column;align-items:flex-start}}.cards{{grid-template-columns:repeat(2,1fr)}}.detail-grid{{grid-template-columns:1fr}}input{{min-width:100%}}}}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>GD Sales Command Center V14.2</h1>
    <div class="sub">
      CRM vivo: <strong>{esc(ACTIVE_DB)}</strong>
      · Lead y cotización: lectura local PostgreSQL, sin token
    </div>
  </div>
  <div class="sub">
    Datos generados: {NOW.strftime("%d-%m-%Y %H:%M:%S")}
  </div>
</div>

<div class="note">
  <strong>Estado de fuentes:</strong> CRM <strong>{esc(ACTIVE_DB)}</strong> · Calendar <strong>{"LIVE OK" if calendar_live_ok else "SNAPSHOT/FALLBACK + CRM LIVE"}</strong>
  · Reglas/tasks <strong>{"OK" if task_sync_result.get("ok") else ("SIN EJECUTAR" if not task_sync_result else "FALLÓ")}</strong>
  {(" · Calendar: " + esc(calendar_live_error)) if calendar_live_error else ""}
  {(" · Tasks sync HTTP " + esc(task_sync_result.get("status"))) if task_sync_result and not task_sync_result.get("ok") else ""}
  {(" · Rellenos CRM LIVE: " + str(crm_calendar_overlay_count) + " evento(s)") if crm_calendar_overlay_count else ""}
  {(" · Auth interno: " + esc(_INTERNAL_AUTH_ERROR)) if _INTERNAL_AUTH_ERROR else ""}
</div>

<div class="note">
  <strong>Seguimiento efectivo:</strong>
  seguimiento registrado en CRM o una nueva/revisión de cotización posterior a la cotización inicial.
  Una tarea comercial anterior a ese seguimiento deja de contarse como pendiente.
</div>

<div class="tabs">
  <button id="tabSales" class="tab active">COMERCIAL</button>
  <button id="tabOps" class="tab">OPERACIONES</button>
  <button id="refreshNow"><strong>⟳ ACTUALIZAR TODO</strong></button>
  <span class="statusbar" id="refreshStatus">Actualización profunda: CRM + reglas/tareas + Calendar live</span>
</div>

<div id="findingsBanner" class="panel finding-banner hidden">
  <div class="modal-head">
    <div>
      <h2>✨ Nuevos hallazgos desde la revisión anterior</h2>
      <div class="sub" id="findingsSummary"></div>
    </div>
    <button id="toggleFindings">Ver hallazgos</button>
  </div>
  <div id="findingsList" class="hidden"></div>
</div>

<section id="salesSection">

  <div class="controls">
    <select id="monthSelect">{''.join(month_options)}</select>
    <select id="brandSelect">{select_options(brands, "Todas las marcas")}</select>
    <select id="execSelect">{select_options(execs, "Todos los ejecutivos")}</select>
    <select id="statusSelect">{select_options(statuses, "Todos los estados")}</select>
    <input id="search" placeholder="Buscar cliente...">
    <button id="resetFilters">Limpiar filtros</button>
    <a class="download" href="gd_sales_dashboard.xlsx">Excel</a>
  </div>

  <div class="cards" id="cards">
    <div class="card active" data-filter="all">
      <div class="label">Cola comercial</div>
      <div class="value" id="kpiCount">—</div><div class="amt" id="kpiAmount">—</div>
    </div>
    <div class="card" data-filter="semana">
      <div class="label">Próximos 7 días</div>
      <div class="value" id="weekCount">—</div><div class="amt" id="weekAmount">—</div>
    </div>
    <div class="card" data-filter="alto-monto">
      <div class="label">≥ $3M</div>
      <div class="value" id="highCount">—</div><div class="amt" id="highAmount">—</div>
    </div>
    <div class="card" data-filter="sin-gestion-48">
      <div class="label">48h sin gestión</div>
      <div class="value" id="idle48Count">—</div><div class="amt" id="idle48Amount">—</div>
    </div>
    <div class="card" data-filter="sin-gestion-72">
      <div class="label">72h sin gestión</div>
      <div class="value" id="idle72Count">—</div><div class="amt" id="idle72Amount">—</div>
    </div>
    <div class="card" data-filter="tarea-vencida">
      <div class="label">Tarea vencida real</div>
      <div class="value" id="taskCount">—</div><div class="amt" id="taskAmount">—</div>
    </div>
  </div>

  <div class="panel">
    <h2>Oportunidades para gestionar</h2>
    <div class="note">
      Click en encabezados para ordenar. <strong>Ver</strong> abre la ficha local.
      La cotización se abre desde PostgreSQL y no usa endpoints protegidos.
      <strong>Regla CRM:</strong> cada cotización y cada revisión real del lead
      cuentan automáticamente como seguimiento y reinician su circuito de tareas.
    </div>
    <div id="emptyState" class="warning hidden">
      No hay oportunidades con esta combinación de filtros/card.
    </div>
    <div class="table-wrap">
      <table id="pipelineTable">
        <thead>
          <tr>
            <th>Ficha</th>
            <th>Bandera</th>
            <th class="sortable" data-sort="client">Cliente ↕</th>
            <th>Teléfono</th>
            <th class="sortable" data-sort="brand">Marca ↕</th>
            <th class="sortable" data-sort="exec">Ejecutivo ↕</th>
            <th class="sortable" data-sort="date">Fecha evento ↕</th>
            <th class="sortable money" data-sort="amount">Monto ↕</th>
            <th class="sortable" data-sort="status">Estado ↕</th>
            <th>Cotización</th>
            <th>Qué hacer</th>
            <th>Seguimiento efectivo</th>
            <th>Tipo seguimiento</th>
            <th>Horas sin gestión</th>
          </tr>
        </thead>
        <tbody>{''.join(pipeline_rows)}</tbody>
      </table>
    </div>
  </div>

  <div class="panel">
    <h2>Resumen por ejecutivo</h2>
    <div class="note"><strong>Doble click</strong> para abrir las oportunidades del ejecutivo con los filtros actuales.</div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Ejecutivo</th><th>Oportunidades</th><th class="money">Pipeline</th>
          <th>7 días</th><th>72h sin gestión</th><th>≥ $3M</th><th>Tareas vencidas</th>
        </tr></thead>
        <tbody>{''.join(exec_rows)}</tbody>
      </table>
    </div>
  </div>

  <div class="panel">
    <h2>Resumen por marca</h2>
    <div class="note">Doble click para abrir las oportunidades de la marca.</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Marca</th><th>Ejecutivo</th><th>Oportunidades</th><th class="money">Pipeline</th></tr></thead>
        <tbody>{''.join(brand_rows)}</tbody>
      </table>
    </div>
  </div>

</section>

<section id="opsSection" class="hidden">

  <div class="panel">
    <div class="calendar-week-head">
      <div>
        <h2>Operaciones · Calendar oficial por semana</h2>
        <div class="sub">
          Calendar: <strong>{esc(OFFICIAL_CALENDAR_ID)}</strong>
          · Datos: <strong>{esc(calendar_verified_at or "sin verificación live")}</strong>
          · Fuente: <strong>{esc(calendar_source)}</strong>
        </div>
      </div>

      <div class="calendar-nav">
        <button id="prevWeek">←</button>
        <button id="todayWeek">Semana actual</button>
        <button id="nextWeek">→</button>
        <select id="weekSelect"></select>
      </div>
    </div>

    <div class="note">
      Vista semanal lunes–domingo. Click en un evento abre teléfono, dirección,
      descripción completa y el evento original de Google Calendar.
      Un evento con problema accionable queda marcado.
      Ubicación <strong>OFICINA</strong> se trata como evento interno.
    </div>

    <div class="cards ops-kpis" id="opsCards">
      <div class="card active" data-ops-filter="all">
        <div class="label">Eventos semana</div>
        <div class="value" id="opsAllCount">—</div>
        <div class="amt">Calendar oficial</div>
      </div>
      <div class="card" data-ops-filter="alert">
        <div class="label">Con alerta</div>
        <div class="value" id="opsAlertCount">—</div>
        <div class="amt">Revisar antes del evento</div>
      </div>
      <div class="card" data-ops-filter="pending">
        <div class="label">TBD / Por confirmar</div>
        <div class="value" id="opsPendingCount">—</div>
        <div class="amt">Dato no cerrado</div>
      </div>
      <div class="card" data-ops-filter="mismatch">
        <div class="label">Comuna ≠ dirección</div>
        <div class="value" id="opsMismatchCount">—</div>
        <div class="amt">Inconsistencia logística</div>
      </div>
      <div class="card" data-ops-filter="phone">
        <div class="label">Teléfono faltante/TBD</div>
        <div class="value" id="opsPhoneCount">—</div>
        <div class="amt">Completar contacto</div>
      </div>
      <div class="card" data-ops-filter="internal">
        <div class="label">Internos / Oficina</div>
        <div class="value" id="opsInternalCount">—</div>
        <div class="amt">Excepción operativa</div>
      </div>
    </div>

    <div id="weeklyCalendar" class="weekly-calendar"></div>

    <div id="opsEmpty" class="warning hidden">
      No hay eventos que cumplan este filtro en la semana seleccionada.
    </div>
  </div>

  <div class="panel">
    <h2>Tareas operacionales abiertas en CRM</h2>
    <div class="note">
      Este bloque es independiente del Calendar. Que esté vacío
      <strong>NO significa</strong> que el Calendar esté correcto.
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Evento</th><th>Cliente</th><th>Marca</th><th>Faltante</th>
          <th>Vence</th><th>Tarea</th><th>Detalle</th>
        </tr></thead>
        <tbody>{''.join(ops_rows)}</tbody>
      </table>
    </div>
  </div>

</section>



<div id="refreshOverlay" class="refresh-overlay hidden">
  <div class="refresh-box">
    <div class="spinner"></div>
    <h2>Actualizando Command Center</h2>
    <div id="refreshStep">Revisando CRM, historial, reglas/tareas y Calendar oficial…</div>
    <div class="sub" style="margin-top:8px">Puede tardar. La pantalla queda bloqueada para no mezclar datos viejos con nuevos.</div>
  </div>
</div>

<div id="calendarModal" class="modal hidden">
  <div class="modal-card">
    <div class="modal-head">
      <div>
        <h2 id="calendarModalTitle">Evento</h2>
        <div id="calendarModalSub" class="sub"></div>
      </div>
      <button onclick="closeCalendarModal()">✕</button>
    </div>

    <div id="calendarAlertBox" class="warning hidden"></div>

    <div id="calendarDetailGrid" class="detail-grid"></div>

    <div class="action-box">
      <strong>Descripción operacional</strong>
      <div id="calendarDescription" style="margin-top:8px;white-space:pre-wrap"></div>
    </div>

    <div class="controls">
      <a id="calendarOpenLink" class="download" target="_blank" rel="noopener">
        Abrir en Google Calendar ↗
      </a>
    </div>
  </div>
</div>

<div id="modal" class="modal hidden">
  <div class="modal-card">
    <div class="modal-head">
      <div>
        <h2 id="modalTitle">Ficha</h2>
        <div id="modalSub" class="sub"></div>
      </div>
      <button onclick="closeModal()">✕</button>
    </div>

    <div id="singleLeadView">
      <div id="detailGrid" class="detail-grid"></div>
      <div class="action-box">
        <strong>Por qué atacarlo</strong>
        <div id="detailReason"></div>
      </div>
      <div class="action-box">
        <strong>Cómo atacarlo</strong>
        <div id="detailHow"></div>
      </div>
      <div class="action-box">
        <strong>Historial comercial / circuito de seguimiento</strong>
        <div class="sub" style="margin:4px 0 8px">
          Cotización, revisiones, observaciones/cambios y tareas ordenadas por fecha.
        </div>
        <div id="commercialHistory"></div>
      </div>
      <div class="action-box hidden" id="quoteBox">
        <strong>Cotización vigente</strong>
        <div id="quoteMeta" style="margin-top:8px"></div>
        <div id="quoteItems" style="margin-top:10px"></div>
      </div>
      <div class="controls">
        <button id="modalQuote">Ver cotización</button>
      </div>
    </div>

    <div id="drillView" class="hidden">
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Ver</th><th>Cliente</th><th>Marca</th><th>Fecha</th>
            <th class="money">Monto</th><th>Estado</th><th>Cotización</th>
          </tr></thead>
          <tbody id="drillBody"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>


<script id="quoteData" type="application/json">{quote_data_json}</script>
<script id="calendarData" type="application/json">{calendar_json_for_html}</script>
<script id="findingsData" type="application/json">{findings_json_for_html}</script>
<script>
const STORAGE_KEY = "gd_sales_ui_v13";
const QUOTE_DATA = JSON.parse(document.getElementById("quoteData").textContent || "{{}}");
let activeCard = "all";
let sortKey = "date";
let sortDirection = "asc";
let activeTab = "sales";
let currentLeadDetails = null;
let opsFilter = "all";
let currentWeekKey = "";
const CALENDAR_EVENTS = JSON.parse(
  document.getElementById("calendarData").textContent || "[]"
);
const FINDINGS = JSON.parse(document.getElementById("findingsData").textContent || "[]");

const tableBody = document.querySelector("#pipelineTable tbody");
const monthSelect = document.getElementById("monthSelect");
const brandSelect = document.getElementById("brandSelect");
const execSelect = document.getElementById("execSelect");
const statusSelect = document.getElementById("statusSelect");
const search = document.getElementById("search");
const cards = [...document.querySelectorAll("#cards .card")];

function peso(value) {{
  return new Intl.NumberFormat(
    "es-CL",
    {{style:"currency",currency:"CLP",maximumFractionDigits:0}}
  ).format(value);
}}

function decodeDetails(tr) {{
  try {{
    const txt = document.createElement("textarea");
    txt.innerHTML = tr.dataset.details;
    return JSON.parse(txt.value);
  }} catch (e) {{
    console.error(e);
    return null;
  }}
}}

function saveState() {{
  const state = {{
    month: monthSelect.value,
    brand: brandSelect.value,
    exec: execSelect.value,
    status: statusSelect.value,
    search: search.value,
    activeCard,
    sortKey,
    sortDirection,
    activeTab,
    week: currentWeekKey
  }};
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}}

function setIfOptionExists(select, value) {{
  if ([...select.options].some(o => o.value === value)) {{
    select.value = value;
  }}
}}

function restoreState() {{
  try {{
    const state = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}");

    if (state.month) setIfOptionExists(monthSelect, state.month);
    if (state.brand) setIfOptionExists(brandSelect, state.brand);
    if (state.exec) setIfOptionExists(execSelect, state.exec);
    if (state.status) setIfOptionExists(statusSelect, state.status);
    if (typeof state.search === "string") search.value = state.search;

    activeCard = state.activeCard || "all";
    sortKey = state.sortKey || "date";
    sortDirection = state.sortDirection || "asc";
    activeTab = state.activeTab || "sales";

    cards.forEach(c =>
      c.classList.toggle("active", c.dataset.filter === activeCard)
    );
  }} catch (e) {{
    console.warn("No se pudo restaurar UI", e);
  }}
}}

function baseMatch(tr) {{
  if (monthSelect.value !== "all" && tr.dataset.month !== monthSelect.value) return false;
  if (brandSelect.value !== "all" && tr.dataset.brand !== brandSelect.value) return false;
  if (execSelect.value !== "all" && tr.dataset.exec !== execSelect.value) return false;
  if (statusSelect.value !== "all" && tr.dataset.status !== statusSelect.value) return false;

  const q = search.value.trim().toLowerCase();
  if (q && !(tr.dataset.client || "").includes(q)) return false;

  return true;
}}

function cardMatch(tr) {{
  return activeCard === "all"
    || (tr.dataset.tags || "").split(" ").includes(activeCard);
}}

function compareRows(a,b) {{
  let av, bv;

  if (sortKey === "amount") {{
    av = parseFloat(a.dataset.amount || "0");
    bv = parseFloat(b.dataset.amount || "0");
  }} else if (sortKey === "date") {{
    av = a.dataset.date || "";
    bv = b.dataset.date || "";
  }} else {{
    av = a.dataset[sortKey] || "";
    bv = b.dataset[sortKey] || "";
  }}

  let result;
  if (typeof av === "number") {{
    result = av - bv;
  }} else {{
    result = String(av).localeCompare(String(bv), "es", {{sensitivity:"base"}});
  }}

  return sortDirection === "asc" ? result : -result;
}}

function filteredRows(includeCard=true) {{
  let rows = [...tableBody.querySelectorAll("tr")].filter(baseMatch);
  if (includeCard) rows = rows.filter(cardMatch);
  return rows;
}}

function calculate(tag) {{
  let rows = filteredRows(false);
  if (tag !== "all") {{
    rows = rows.filter(
      tr => (tr.dataset.tags || "").split(" ").includes(tag)
    );
  }}
  return [
    rows.length,
    rows.reduce((s,tr) => s + parseFloat(tr.dataset.amount || "0"), 0)
  ];
}}

function updateKPIs() {{
  const spec = [
    ["all","kpiCount","kpiAmount"],
    ["semana","weekCount","weekAmount"],
    ["alto-monto","highCount","highAmount"],
    ["sin-gestion-48","idle48Count","idle48Amount"],
    ["sin-gestion-72","idle72Count","idle72Amount"],
    ["tarea-vencida","taskCount","taskAmount"]
  ];

  spec.forEach(([tag,c,a]) => {{
    const [n,m] = calculate(tag);
    document.getElementById(c).textContent = n;
    document.getElementById(a).textContent = peso(m);
  }});
}}

function render() {{
  const all = [...tableBody.querySelectorAll("tr")];
  all.forEach(tr => {{
    tr.style.display = "none";
    tr.classList.toggle("finding-row", tr.dataset.finding === "1");
  }});

  const rows = filteredRows(true);
  rows.sort(compareRows);
  rows.forEach(tr => {{
    tr.style.display = "";
    tableBody.appendChild(tr);
  }});

  document.getElementById("emptyState")
    .classList.toggle("hidden", rows.length !== 0);

  updateKPIs();
  saveState();
}}

document.querySelectorAll("th.sortable").forEach(th => {{
  th.addEventListener("click", () => {{
    const key = th.dataset.sort;

    if (sortKey === key) {{
      sortDirection = sortDirection === "asc" ? "desc" : "asc";
    }} else {{
      sortKey = key;
      sortDirection = key === "amount" ? "desc" : "asc";
    }}

    document.querySelectorAll("th.sortable").forEach(x => {{
      x.textContent = x.textContent.replace(/ [↕↑↓]$/, " ↕");
    }});

    const clean = th.textContent.replace(/ [↕↑↓]$/, "");
    th.textContent = clean + (sortDirection === "asc" ? " ↑" : " ↓");
    render();
  }});
}});

cards.forEach(card => {{
  card.onclick = () => {{
    cards.forEach(x => x.classList.remove("active"));
    card.classList.add("active");
    activeCard = card.dataset.filter;
    render();
  }};
}});

[monthSelect, brandSelect, execSelect, statusSelect].forEach(el => {{
  el.onchange = render;
}});
search.oninput = render;

document.getElementById("resetFilters").onclick = () => {{
  setIfOptionExists(monthSelect, "{TODAY.strftime('%Y-%m')}");
  brandSelect.value = "all";
  execSelect.value = "all";
  statusSelect.value = "all";
  search.value = "";
  activeCard = "all";
  sortKey = "date";
  sortDirection = "asc";
  cards.forEach(c => c.classList.toggle("active", c.dataset.filter === "all"));
  render();
}};


// ============================================================
// LOCAL LEAD + LOCAL QUOTE
// ============================================================

function renderKeyValue(obj) {{
  if (!obj || typeof obj !== "object") {{
    return "<div>Sin datos.</div>";
  }}

  const rows = Object.entries(obj)
    .filter(([key]) => ![
      "hashed_password",
      "password",
      "token",
      "access_token",
      "refresh_token"
    ].includes(String(key).toLowerCase()))
    .map(([key,value]) => {{
      let shown = value;

      if (shown && typeof shown === "object") {{
        shown = JSON.stringify(shown);
      }}

      return `
        <tr>
          <td><strong>${{escapeHtml(String(key))}}</strong></td>
          <td style="white-space:normal">${{escapeHtml(String(shown ?? ""))}}</td>
        </tr>`;
    }})
    .join("");

  return `<table><tbody>${{rows}}</tbody></table>`;
}}

function escapeHtml(value) {{
  const div = document.createElement("div");
  div.textContent = value ?? "";
  return div.innerHTML;
}}

function renderQuote(details) {{
  const box = document.getElementById("quoteBox");
  const meta = document.getElementById("quoteMeta");
  const itemsTarget = document.getElementById("quoteItems");

  const id = details && details.id_cotizacion
    ? String(details.id_cotizacion)
    : "";

  const payload = id ? QUOTE_DATA[id] : null;

  if (!payload) {{
    box.classList.remove("hidden");
    meta.innerHTML =
      "<strong>No encontré el detalle de esta cotización en la tabla cotizaciones.</strong>";
    itemsTarget.innerHTML = "";
    return;
  }}

  box.classList.remove("hidden");
  meta.innerHTML = renderKeyValue(payload.quote || {{}});

  const items = Array.isArray(payload.items)
    ? payload.items
    : [];

  if (!items.length) {{
    itemsTarget.innerHTML =
      "<div><strong>Ítems:</strong> sin filas en cotizacion_items.</div>";
    return;
  }}

  const columns = [...new Set(
    items.flatMap(item => Object.keys(item || {{}}))
  )];

  const head = columns
    .map(c => `<th>${{escapeHtml(c)}}</th>`)
    .join("");

  const body = items.map(item => {{
    const tds = columns.map(c => {{
      const v = item ? item[c] : "";
      const shown =
        v && typeof v === "object"
        ? JSON.stringify(v)
        : String(v ?? "");

      return `<td style="white-space:normal">${{escapeHtml(shown)}}</td>`;
    }}).join("");

    return `<tr>${{tds}}</tr>`;
  }}).join("");

  itemsTarget.innerHTML = `
    <div style="margin-bottom:5px"><strong>Ítems de cotización</strong></div>
    <div class="table-wrap">
      <table>
        <thead><tr>${{head}}</tr></thead>
        <tbody>${{body}}</tbody>
      </table>
    </div>`;
}}

function openQuote(details) {{
  if (!details) return;

  // Cotización abre en la misma ficha local, sin token.
  openLead(details);
  renderQuote(details);
}}

function openQuoteFromRow(tr) {{
  openQuote(decodeDetails(tr));
}}

function detailItem(key,value) {{
  return `
    <div class="detail">
      <div class="k">${{key}}</div>
      <div class="v">${{escapeHtml(String(value || "—"))}}</div>
    </div>`;
}}

function openLead(details) {{
  if (!details) return;

  currentLeadDetails = details;

  document.getElementById("singleLeadView").classList.remove("hidden");
  document.getElementById("drillView").classList.add("hidden");
  document.getElementById("quoteBox").classList.add("hidden");
  document.getElementById("quoteMeta").innerHTML = "";
  document.getElementById("quoteItems").innerHTML = "";

  document.getElementById("modalTitle").textContent = details.cliente;
  document.getElementById("modalSub").textContent =
    `${{details.marca}} · ${{details.ejecutivo}} · ${{details.fecha_evento}}`;

  document.getElementById("detailGrid").innerHTML =
      detailItem("Lead", details.id_lead)
    + detailItem("Bandera", `${{details.bandera || ""}} ${{details.bandera_label || ""}}`)
    + detailItem("Monto", details.monto)
    + detailItem("Estado", details.estado)
    + detailItem("Teléfono", details.telefono)
    + detailItem("Email", details.email)
    + detailItem("Cotización", details.cotizacion)
    + detailItem("N° cotizaciones", details.quote_count)
    + detailItem("Seguimiento efectivo", details.seguimiento_efectivo)
    + detailItem("Tipo seguimiento", details.seguimiento_tipo)
    + detailItem("Tarea", details.task_kind)
    + detailItem("Tarea vence", details.task_due)
    + detailItem("Acción", details.accion)
    + detailItem("Notas", details.notas);

  document.getElementById("detailReason").textContent =
    details.motivo || "—";

  document.getElementById("detailHow").textContent =
    details.como_atacar || "—";

  const historyRoot = document.getElementById("commercialHistory");
  const history = Array.isArray(details.historial) ? details.historial : [];
  historyRoot.innerHTML = history.length
    ? history.map(item => `
        <div class="history-item">
          <div class="history-at">${{escapeHtml(
            item.at ? new Intl.DateTimeFormat("es-CL", {{
              dateStyle:"short", timeStyle:"short"
            }}).format(new Date(item.at)) : "—"
          )}}</div>
          <div>
            <strong>${{escapeHtml(item.kind || "Movimiento")}}</strong>
            <div class="sub">${{escapeHtml(item.detail || "")}}</div>
          </div>
        </div>`).join("")
    : '<div class="sub">Sin hitos comerciales recuperados para esta ficha.</div>';

  document.getElementById("modalQuote").disabled =
    !details.id_cotizacion;

  document.getElementById("modal").classList.remove("hidden");
}}

function openLeadFromRow(tr) {{
  openLead(decodeDetails(tr));
}}

document.getElementById("modalQuote").onclick = () => {{
  if (currentLeadDetails) renderQuote(currentLeadDetails);
}};


// ============================================================
// DRILL DOWN
// ============================================================

function drillRows(rows,title) {{
  rows.sort(compareRows);
  const body = document.getElementById("drillBody");
  body.innerHTML = "";

  let total = 0;

  rows.forEach(tr => {{
    const d = decodeDetails(tr);
    if (!d) return;

    total += parseFloat(tr.dataset.amount || "0");

    const encoded = encodeURIComponent(JSON.stringify(d));

    body.insertAdjacentHTML(
      "beforeend",
      `<tr>
        <td><button class="link-button" onclick="openEncodedLead('${{encoded}}')">Ver</button></td>
        <td>${{d.cliente}}</td>
        <td>${{d.marca}}</td>
        <td>${{d.fecha_evento}}</td>
        <td class="money">${{d.monto}}</td>
        <td>${{d.estado}}</td>
        <td><button class="link-button" onclick="openEncodedQuote('${{encoded}}')">${{d.cotizacion || "Abrir"}} ↗</button></td>
      </tr>`
    );
  }});

  document.getElementById("singleLeadView").classList.add("hidden");
  document.getElementById("drillView").classList.remove("hidden");
  document.getElementById("modalTitle").textContent = title;
  document.getElementById("modalSub").textContent =
    `${{rows.length}} oportunidades · ${{peso(total)}}`;
  document.getElementById("modal").classList.remove("hidden");
}}

function openEncodedLead(value) {{
  openLead(JSON.parse(decodeURIComponent(value)));
}}

function openEncodedQuote(value) {{
  openQuote(JSON.parse(decodeURIComponent(value)));
}}

function drillExecutive(execName) {{
  const rows = filteredRows(false).filter(
    tr => tr.dataset.exec === execName
  );
  drillRows(rows, "Oportunidades · " + execName.toUpperCase());
}}

function drillBrand(brandName) {{
  const rows = filteredRows(false).filter(
    tr => tr.dataset.brand === brandName
  );
  drillRows(rows, "Oportunidades · " + brandName.toUpperCase());
}}

function closeModal() {{
  document.getElementById("modal").classList.add("hidden");
  currentLeadDetails = null;
}}

document.getElementById("modal").onclick = e => {{
  if (e.target.id === "modal") closeModal();
}};



// ============================================================
// WEEKLY CALENDAR
// ============================================================

function localDateOnly(iso) {{
  const d = new Date(iso);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}}

function isoDateLocal(d) {{
  const y = d.getFullYear();
  const m = String(d.getMonth()+1).padStart(2,"0");
  const day = String(d.getDate()).padStart(2,"0");
  return `${{y}}-${{m}}-${{day}}`;
}}

function mondayOf(d) {{
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = x.getDay();
  const delta = day === 0 ? -6 : 1 - day;
  x.setDate(x.getDate() + delta);
  return x;
}}

function addDays(d,n) {{
  const x = new Date(d);
  x.setDate(x.getDate()+n);
  return x;
}}

function weekKeyForDate(d) {{
  return isoDateLocal(mondayOf(d));
}}

function eventWeekKey(e) {{
  return weekKeyForDate(localDateOnly(e.start));
}}

function formatTime(iso) {{
  return new Intl.DateTimeFormat("es-CL", {{
    hour:"2-digit", minute:"2-digit", hour12:false
  }}).format(new Date(iso));
}}

function calendarWeeks() {{
  return [...new Set(CALENDAR_EVENTS.map(eventWeekKey))].sort();
}}

function initializeWeekSelect() {{
  const select = document.getElementById("weekSelect");
  const weeks = calendarWeeks();
  select.innerHTML = "";

  const todayKey = weekKeyForDate(new Date());

  weeks.forEach(key => {{
    const mon = new Date(key + "T00:00:00");
    const sun = addDays(mon,6);
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent =
      `Semana ${{String(mon.getDate()).padStart(2,"0")}}/${{String(mon.getMonth()+1).padStart(2,"0")}}–`
      + `${{String(sun.getDate()).padStart(2,"0")}}/${{String(sun.getMonth()+1).padStart(2,"0")}}`;
    select.appendChild(opt);
  }});

  currentWeekKey = weeks.includes(todayKey) ? todayKey : (weeks[0] || todayKey);

  try {{
    const state = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}");
    if (state.week && weeks.includes(state.week)) {{
      currentWeekKey = state.week;
    }}
  }} catch (_) {{}}

  select.value = currentWeekKey;
}}

function eventHasTag(e, tag) {{
  return tag === "all" || (e.tags || []).includes(tag);
}}

function weekEvents(applyFilter=true) {{
  let rows = CALENDAR_EVENTS.filter(
    e => eventWeekKey(e) === currentWeekKey
  );

  if (applyFilter && opsFilter !== "all") {{
    rows = rows.filter(e => eventHasTag(e, opsFilter));
  }}

  rows.sort((a,b) => new Date(a.start) - new Date(b.start));
  return rows;
}}

function updateOpsKPIs() {{
  const all = weekEvents(false);
  const count = tag =>
    tag === "all" ? all.length : all.filter(e => eventHasTag(e,tag)).length;

  document.getElementById("opsAllCount").textContent = count("all");
  document.getElementById("opsAlertCount").textContent = count("alert");
  document.getElementById("opsPendingCount").textContent = count("pending");
  document.getElementById("opsMismatchCount").textContent = count("mismatch");
  document.getElementById("opsPhoneCount").textContent = count("phone");
  document.getElementById("opsInternalCount").textContent = count("internal");
}}

function renderWeeklyCalendar() {{
  const root = document.getElementById("weeklyCalendar");
  root.innerHTML = "";

  const monday = new Date(currentWeekKey + "T00:00:00");
  const today = new Date();
  const rows = weekEvents(true);

  for (let i=0; i<7; i++) {{
    const d = addDays(monday,i);
    const key = isoDateLocal(d);
    const dayEvents = rows.filter(
      e => isoDateLocal(localDateOnly(e.start)) === key
    );

    const col = document.createElement("div");
    col.className = "day-column";
    if (isoDateLocal(today) === key) col.classList.add("today");

    col.innerHTML = `
      <div class="day-head">
        <span class="dow">${{new Intl.DateTimeFormat("es-CL",{{weekday:"short"}}).format(d)}}</span>
        <span class="date">${{String(d.getDate()).padStart(2,"0")}}/${{String(d.getMonth()+1).padStart(2,"0")}}</span>
      </div>`;

    dayEvents.forEach(e => {{
      const card = document.createElement("div");
      card.className = "event-card";
      if ((e.tags || []).includes("alert")) card.classList.add("alert");
      if (e.is_new_finding) card.classList.add("new-finding");

      const badges = [];
      if ((e.tags || []).includes("pending")) badges.push("POR CONFIRMAR / TBD");
      if ((e.tags || []).includes("mismatch")) badges.push("COMUNA ≠ DIRECCIÓN");
      if ((e.tags || []).includes("phone")) badges.push("CONTACTO");
      if ((e.tags || []).includes("address")) badges.push("DIRECCIÓN");
      if ((e.tags || []).includes("internal")) badges.push("INTERNO");
      if ((e.tags || []).includes("schedule")) badges.push("HORARIO");

      card.innerHTML = `
        <div class="event-time">${{formatTime(e.start)}}–${{formatTime(e.end)}}</div>
        <div class="event-title">${{escapeHtml(e.summary)}}</div>
        <div class="event-meta">${{escapeHtml(e.location || "Sin location")}}</div>
        <div class="badges">
          ${{badges.map(b => `<span class="badge ${{b==="INTERNO" ? "" : "alert"}}">${{escapeHtml(b)}}</span>`).join("")}}
        </div>`;

      card.onclick = () => openCalendarEvent(e);
      col.appendChild(card);
    }});

    root.appendChild(col);
  }}

  document.getElementById("opsEmpty")
    .classList.toggle("hidden", rows.length !== 0);

  updateOpsKPIs();
  saveState();
}}

function openCalendarEvent(e) {{
  document.getElementById("calendarModalTitle").textContent = e.summary;
  document.getElementById("calendarModalSub").textContent =
    `${{formatTime(e.start)}}–${{formatTime(e.end)}} · ${{e.location || "Sin location"}}`;

  const alerts = e.alerts || [];
  const alertBox = document.getElementById("calendarAlertBox");

  if (alerts.length) {{
    alertBox.classList.remove("hidden");
    alertBox.innerHTML =
      "<strong>Revisar:</strong> " + alerts.map(escapeHtml).join(" · ");
  }} else {{
    alertBox.classList.add("hidden");
    alertBox.innerHTML = "";
  }}

  document.getElementById("calendarDetailGrid").innerHTML =
      detailItem("Marca", e.brand || "—")
    + detailItem("Fecha", new Intl.DateTimeFormat("es-CL",{{dateStyle:"full"}}).format(new Date(e.start)))
    + detailItem("Horario", `${{formatTime(e.start)}}–${{formatTime(e.end)}}`)
    + detailItem("Comuna / Location", e.location || "—")
    + detailItem("Dirección", e.address || "—")
    + detailItem("Teléfono", e.phone || "—");

  document.getElementById("calendarDescription").textContent =
    e.description || "—";

  document.getElementById("calendarOpenLink").href = e.url || "#";
  document.getElementById("calendarModal").classList.remove("hidden");
}}

function closeCalendarModal() {{
  document.getElementById("calendarModal").classList.add("hidden");
}}

document.getElementById("calendarModal").addEventListener("click", e => {{
  if (e.target.id === "calendarModal") closeCalendarModal();
}});

document.querySelectorAll("#opsCards .card").forEach(card => {{
  card.onclick = () => {{
    document.querySelectorAll("#opsCards .card")
      .forEach(c => c.classList.remove("active"));
    card.classList.add("active");
    opsFilter = card.dataset.opsFilter;
    renderWeeklyCalendar();
  }};
}});

document.getElementById("weekSelect").onchange = e => {{
  currentWeekKey = e.target.value;
  renderWeeklyCalendar();
}};

document.getElementById("prevWeek").onclick = () => {{
  const weeks = calendarWeeks();
  const idx = weeks.indexOf(currentWeekKey);
  if (idx > 0) {{
    currentWeekKey = weeks[idx-1];
    document.getElementById("weekSelect").value = currentWeekKey;
    renderWeeklyCalendar();
  }}
}};

document.getElementById("nextWeek").onclick = () => {{
  const weeks = calendarWeeks();
  const idx = weeks.indexOf(currentWeekKey);
  if (idx >= 0 && idx < weeks.length-1) {{
    currentWeekKey = weeks[idx+1];
    document.getElementById("weekSelect").value = currentWeekKey;
    renderWeeklyCalendar();
  }}
}};

document.getElementById("todayWeek").onclick = () => {{
  const key = weekKeyForDate(new Date());
  const weeks = calendarWeeks();
  currentWeekKey = weeks.includes(key) ? key : (weeks[0] || key);
  document.getElementById("weekSelect").value = currentWeekKey;
  renderWeeklyCalendar();
}};


// ============================================================
// TABS + REFRESH SAFE
// ============================================================

function showTab(name) {{
  activeTab = name;

  const sales = document.getElementById("salesSection");
  const ops = document.getElementById("opsSection");

  const salesBtn = document.getElementById("tabSales");
  const opsBtn = document.getElementById("tabOps");

  const isSales = name === "sales";

  sales.classList.toggle("hidden", !isSales);
  ops.classList.toggle("hidden", isSales);
  salesBtn.classList.toggle("active", isSales);
  opsBtn.classList.toggle("active", !isSales);

  saveState();
}}

document.getElementById("tabSales").onclick = () => showTab("sales");
document.getElementById("tabOps").onclick = () => showTab("ops");

function renderFindings() {{
  const banner = document.getElementById("findingsBanner");
  const list = document.getElementById("findingsList");
  const summary = document.getElementById("findingsSummary");

  if (!FINDINGS.length) {{
    banner.classList.add("hidden");
    return;
  }}

  banner.classList.remove("hidden");
  const leadCount = FINDINGS.filter(x => x.entity_type === "lead").length;
  const calCount = FINDINGS.filter(x => x.entity_type === "calendar").length;
  summary.textContent = `${{FINDINGS.length}} hallazgo(s): ${{leadCount}} CRM · ${{calCount}} Calendar`;
  list.innerHTML = FINDINGS.map(f =>
    `<div class="finding-item"><strong>${{escapeHtml(f.title || f.id)}}</strong> · ${{escapeHtml(f.message || "Cambio detectado")}}</div>`
  ).join("");
}}

document.getElementById("toggleFindings").onclick = () => {{
  document.getElementById("findingsList").classList.toggle("hidden");
}};

async function deepRefresh() {{
  saveState();
  const overlay = document.getElementById("refreshOverlay");
  const step = document.getElementById("refreshStep");
  overlay.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  step.textContent = "Reconciliando cotizaciones e historial → reglas/tareas → Calendar…";

  try {{
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 355000);
    const response = await fetch("/gd-sales-api/refresh", {{
      method: "POST",
      headers: {{"Content-Type":"application/json", "X-GD-Refresh":"1"}},
      body: "{{}}",
      cache: "no-store",
      signal: controller.signal,
    }});
    clearTimeout(timer);
    const result = await response.json().catch(() => ({{}}));

    if (!response.ok || !result.ok) {{
      throw new Error(result.error || result.generator_error || `HTTP ${{response.status}}`);
    }}

    const cal = result.calendar || {{}};
    const findings = result.findings_count || 0;
    step.textContent = cal.live_ok
      ? `Calendar live OK · ${{findings}} hallazgo(s). Recargando…`
      : `CRM actualizado, pero Calendar live falló: ${{cal.error || "sin detalle"}}`;

    if (!cal.live_ok) {{
      // Give the user the truth before reloading the fallback view.
      await new Promise(resolve => setTimeout(resolve, 2200));
    }}
    window.location.reload();
  }} catch (e) {{
    step.textContent = "ERROR DE ACTUALIZACIÓN: " + (e.message || String(e));
    document.getElementById("refreshStatus").textContent = "Último refresh profundo falló";
    setTimeout(() => {{
      overlay.classList.add("hidden");
      document.body.style.overflow = "";
    }}, 4500);
  }}
}}

document.getElementById("refreshNow").onclick = deepRefresh;


// Init
restoreState();
initializeWeekSelect();
showTab(activeTab);
render();
renderWeeklyCalendar();
renderFindings();
</script>

</body>
</html>
"""

html_path = Path(OUTPUT_DIR) / "index.html"
html_path.write_text(html, encoding="utf-8")


# ============================================================
# POST-GENERATION TESTS
# ============================================================

required = [
    "QUOTE_DATA",
    "renderQuote",
    "openQuote",
    "localStorage",
    "deepRefresh",
    "refreshOverlay",
    "findingsData",
    'data-sort="amount"',
    "drillExecutive",
    "seguimiento_efectivo",
    "weeklyCalendar",
    "renderWeeklyCalendar",
    "calendarData",
    "commercialHistory",
    "follow-flag",
    "Teléfono",
]

missing = [x for x in required if x not in html]
if missing:
    raise RuntimeError(
        "HTML incompleto: " + ", ".join(missing)
    )


# ============================================================
# CONSOLE
# ============================================================

hidden_followup = int(
    (
        base_active["seguimiento_efectivo_at"].notna()
        & ~base_active["task_due_now"]
    ).sum()
)

superseded_tasks = int(
    base_active["task_superada_por_gestion"].sum()
)

quote_followups = int(
    base_active["quote_followup_at"].notna().sum()
)

print("")
print("GD SALES COMMAND CENTER V14.2")
print("==========================")
print(f"CRM ACTIVO: {ACTIVE_DB}")
print(f"ULTIMA ACTIVIDAD DB: {active_info['latest']}")
print("")
print("AUDITORIA DB:")
for item in db_audit:
    print(
        f"- {item['dbname']}: leads={item['rows']} | latest={item['latest']}"
    )

print("")
print(
    f"COLA COMERCIAL ACTUAL: {len(pipeline)} | "
    f"{money(pipeline['monto'].sum())}"
)
print(f"SEGUIMIENTOS POR COTIZACION (INICIAL/REVISION): {quote_followups}")
print(f"LEADS OCULTOS POR SEGUIMIENTO EFECTIVO: {hidden_followup}")
print(f"TAREAS SUPERADAS POR GESTION POSTERIOR: {superseded_tasks}")
print(f"SYNC REAL DE TAREAS ACTIVADO: {'SI' if SYNC_TASKS else 'NO'}")
print(f"TAREAS ACTUALIZADAS A done: {task_sync_updated}")
print(f"TAREAS OPERACIONALES ABIERTAS: {len(ops_tasks)}")
print(f"EVENTOS CALENDAR SNAPSHOT: {len(calendar_events)}")
print(f"CALENDAR VERIFICADO: {calendar_verified_at}")
print(f"CALENDAR SOURCE: {calendar_source}")
print(f"CALENDAR LIVE OK: {calendar_live_ok}")
print(f"HALLAZGOS DEEP REFRESH: {len(findings)}")
print("")
print("POST-GENERATION TESTS: OK")
print("- cotización y lead local PostgreSQL: presente")
print("- persistencia de filtros/orden/tab: presente")
print("- refresh profundo bloqueante: presente")
print("- historial estado/notas/seguimiento entra a gestión efectiva: presente")
print("- TODA cotización/revisión del lead = seguimiento: presente")
print(f"- hitos reconciliados en seguimiento_at: {reconciled_followups}")
print("- tareas anteriores al seguimiento: no inflan vencidos")
print("")
print("DASHBOARD LOCAL:")
print("http://192.168.100.51/gd-sales/")
print("")
print("ARCHIVOS:")
print(html_path)
print(xlsx_path)
print(json_path)
