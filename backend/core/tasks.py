from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import text
from sqlalchemy.orm import Session


TASK_STATUS_OPEN = "open"
TASK_STATUS_DONE = "done"
TASK_STATUS_SKIPPED = "skipped"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_tasks_table(db: Session) -> None:
    """Idempotente: crea tabla + índices mínimos.

    Importante: en algunos entornos el usuario DB puede no tener permisos para DDL.
    Esta función NO debe provocar 500 en producción.
    """
    try:
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.tasks (
                  id_task BIGSERIAL PRIMARY KEY,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  status TEXT NOT NULL DEFAULT 'open',
                  priority INTEGER NOT NULL DEFAULT 50,

                  kind TEXT NOT NULL,
                  title TEXT NOT NULL,
                  description TEXT,

                  entity_type TEXT,
                  entity_id BIGINT,

                  assigned_user_id BIGINT,
                  assigned_username TEXT,

                  due_at TIMESTAMPTZ,
                  completed_at TIMESTAMPTZ,
                  completed_by TEXT,

                  meta JSONB NOT NULL DEFAULT '{}'::jsonb
                )
                """
            )
        )

        # Índices
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_assigned_status_due ON public.tasks(assigned_user_id, status, due_at)"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_entity ON public.tasks(entity_type, entity_id)"))

        # Evita duplicar tareas abiertas del mismo tipo para el mismo lead
        db.execute(
            text(
                """
                DO $$
                BEGIN
                  IF NOT EXISTS (
                    SELECT 1
                    FROM pg_indexes
                    WHERE schemaname='public'
                      AND indexname='ux_tasks_open_unique'
                  ) THEN
                    CREATE UNIQUE INDEX ux_tasks_open_unique
                      ON public.tasks(kind, entity_type, entity_id, assigned_user_id)
                      WHERE status='open';
                  END IF;
                END $$;
                """
            )
        )
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _tasks_table_exists(db: Session) -> bool:
    try:
        return bool(db.execute(text("SELECT to_regclass('public.tasks') IS NOT NULL")).scalar())
    except Exception:
        return False


def _is_admin_role(role: str) -> bool:
    r = (role or "").strip().upper()
    return r in ("ADMIN", "SUPERADMIN", "SUPER_ADMIN", "SUPER ADMIN", "1")

def _is_superadmin_role(role: str) -> bool:
    r = (role or "").strip().upper()
    return r in ("SUPERADMIN", "SUPER_ADMIN", "SUPER ADMIN")


def _lead_name_expr() -> str:
    # NOTA: esta versión "string-only" asume columnas; úsala solo cuando sabes que existen.
    return "COALESCE(NULLIF(btrim(l.nombre_cliente),''), NULLIF(btrim(l.cliente),''), '—')"


def _table_exists(db: Session, table: str) -> bool:
    try:
        return bool(db.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{table}"}).scalar())
    except Exception:
        return False


def _col_exists(db: Session, table: str, col: str) -> bool:
    try:
        return bool(
            db.execute(
                text(
                    """
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name=:t AND column_name=:c
                    LIMIT 1
                    """
                ),
                {"t": table, "c": col},
            ).scalar()
        )
    except Exception:
        return False


def _lead_name_expr_db(db: Session) -> str:
    # Compat: distintos deploys han usado 'nombre_cliente' o 'cliente'.
    has_nombre = _col_exists(db, "leads", "nombre_cliente")
    has_cliente = _col_exists(db, "leads", "cliente")
    if has_nombre and has_cliente:
        return "COALESCE(NULLIF(btrim(l.nombre_cliente),''), NULLIF(btrim(l.cliente),''), '—')"
    if has_nombre:
        return "COALESCE(NULLIF(btrim(l.nombre_cliente),''), '—')"
    if has_cliente:
        return "COALESCE(NULLIF(btrim(l.cliente),''), '—')"
    return "'—'"


def _estado_id_like(db: Session, pattern: str, default: int) -> int:
    try:
        v = db.execute(
            text("SELECT id_estado FROM public.estados_lead WHERE UPPER(nombre) LIKE :p ORDER BY id_estado LIMIT 1"),
            {"p": pattern},
        ).scalar()
        if v is None:
            return default
        return int(v)
    except Exception:
        return default


def _lead_notes_expr_db(db: Session) -> str:
    """
    Expresión SQL que retorna el "texto de historial/seguimiento" del lead.
    Compat con esquemas que usan `notas` o `seguimiento`.
    """
    cols: list[str] = []
    try:
        if _col_exists(db, "leads", "notas"):
            cols.append("l.notas")
        if _col_exists(db, "leads", "seguimiento"):
            cols.append("l.seguimiento")
    except Exception:
        cols = ["l.notas"]
    if not cols:
        return "''"
    if len(cols) == 1:
        return f"COALESCE({cols[0]},'')"
    return "COALESCE(%s,'')" % ",".join(cols)

def _lead_event_date_expr_db(db: Session, *, alias: str = "l") -> str:
    """
    Expresión SQL (DATE) robusta para `fecha_evento`.

    En distintos deploys `fecha_evento` ha sido:
    - DATE / TIMESTAMPTZ
    - TEXT 'YYYY-MM-DD' (a veces con hora)

    Importante: esta función NO debe romper la query si el texto viene con otro formato.
    Si no calza, retorna NULL.
    """
    try:
        if not _col_exists(db, "leads", "fecha_evento"):
            return "NULL::date"
    except Exception:
        return "NULL::date"

    fe_txt = f"NULLIF(btrim(COALESCE({alias}.fecha_evento::text,'')),'')"
    # Extrae la primera fecha que aparezca en el string (no necesariamente al inicio),
    # para soportar valores como "sábado 30/03/2026" o "30/03/2026 10:55".
    d_any = f"substring({fe_txt} from '([0-9]{{4}}[-/.][0-9]{{2}}[-/.][0-9]{{2}}|[0-9]{{2}}[-/.][0-9]{{2}}[-/.][0-9]{{4}})')"
    # Normalizamos separadores para evitar que se nos cuelen formatos con "." o "/".
    # Importante: todo esto debe ser "safe" (sin to_date sobre strings con formato dudoso).
    norm = f"regexp_replace({d_any}, '[./]', '-', 'g')"
    # Parseo tolerante (no debe romper la query) con guardias de rango:
    # - YYYY-MM-DD
    # - DD-MM-YYYY
    return (
        f"(CASE"
        f" WHEN {fe_txt} IS NULL THEN NULL"
        f" WHEN {norm} ~ '^[0-9]{{4}}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$' THEN to_date({norm}, 'YYYY-MM-DD')"
        f" WHEN {norm} ~ '^(0[1-9]|[12][0-9]|3[01])-(0[1-9]|1[0-2])-[0-9]{{4}}$' THEN to_date({norm}, 'DD-MM-YYYY')"
        f" ELSE NULL END)"
    )


def _lead_created_ts_expr_db(db: Session, *, alias: str = "l") -> str:
    """
    Expresión SQL (TIMESTAMP) para "fecha de creación" del lead.
    Usa columnas existentes (created_at / fecha_ingreso / fecha_creacion) sin romper por esquemas legacy.
    """
    cols: list[str] = []
    try:
        if _col_exists(db, "leads", "created_at"):
            cols.append(f"{alias}.created_at")
        if _col_exists(db, "leads", "fecha_ingreso"):
            cols.append(f"{alias}.fecha_ingreso::timestamp")
        if _col_exists(db, "leads", "fecha_creacion"):
            cols.append(f"{alias}.fecha_creacion::timestamp")
        if _col_exists(db, "leads", "creado_at"):
            cols.append(f"{alias}.creado_at")
        if _col_exists(db, "leads", "created"):
            cols.append(f"{alias}.created")
    except Exception:
        cols = [f"{alias}.created_at"]
    if not cols:
        return "now()"
    return "COALESCE(" + ", ".join(cols + ["now()"]) + ")"


def _has_contact_sql_db(db: Session) -> str:
    """
    MVP: consideramos contacto si en historial/seguimiento existe evidencia de WSP/CALL/EMAIL.
    Nota: NO depende de una sola columna para evitar 0 tareas por esquemas legacy.
    """
    txt = _lead_notes_expr_db(db)
    # Tags + variantes (evita depender de un formato exacto).
    return (
        "("
        f"{txt} ILIKE '%[WSP]%' OR {txt} ILIKE '%WHATSAPP%' OR {txt} ILIKE '% VIA WSP%' OR {txt} ILIKE '%WSP %' OR "
        f"{txt} ILIKE '%[CALL]%' OR {txt} ILIKE '%LLAMAD%' OR {txt} ILIKE '% VIA TEL%' OR {txt} ILIKE '%TEL%:%' OR "
        f"{txt} ILIKE '%[EMAIL]%' OR {txt} ILIKE '%CORREO%' OR {txt} ILIKE '%MAIL%' OR "
        f"{txt} ILIKE '%[NOTE]%' OR {txt} ILIKE '%NOTA%'"
        ")"
    )


def _has_contact_sql() -> str:
    """
    Back-compat: se mantiene para usos antiguos, pero es preferible `_has_contact_sql_db(db)`.
    """
    return "(COALESCE(l.notas,'') ILIKE '%[WSP]%' OR COALESCE(l.notas,'') ILIKE '%[CALL]%' OR COALESCE(l.notas,'') ILIKE '%[EMAIL]%')"


def _assigned_to_user_sql() -> str:
    """
    Compat con `leads.id_usuario` bigint o texto.
    Matchea contra múltiples llaves del usuario (uid/email/username/etc) usando array.

    Requiere param:
      - :user_keys  (text[])
    """
    return "(lower(NULLIF(btrim(COALESCE(l.id_usuario::text,'')) ,'')) = ANY(CAST(:user_keys AS text[])))"


def _unassigned_sql() -> str:
    return "(NULLIF(btrim(COALESCE(l.id_usuario::text,'')),'') IS NULL)"


def _user_match_keys(db: Session, *, user_id: int, username: str) -> list[str]:
    keys: list[str] = []
    try:
        keys.append(str(int(user_id)))
    except Exception:
        pass
    if username:
        keys.append(str(username))
    # Enrich: email + username desde tabla usuarios (si existe)
    try:
        if _table_exists(db, "usuarios"):
            row = db.execute(
                text("SELECT email, username FROM public.usuarios WHERE id_usuario=:id LIMIT 1"),
                {"id": int(user_id)},
            ).mappings().first()
            if row:
                if row.get("email"):
                    keys.append(str(row.get("email")))
                if row.get("username"):
                    keys.append(str(row.get("username")))
    except Exception:
        pass
    # Normaliza: trim, lower, de-dup
    out: list[str] = []
    seen = set()
    for k in keys:
        v = str(k or "").strip().lower()
        if not v:
            continue
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _brand_filter_sql(db: Session) -> str:
    """
    Scope por marcas del usuario (para ejecutivos).
    - Si leads tiene id_marca: usamos IDs.
    - Si leads tiene marca (texto): usamos nombres.
    Requiere params (según caso):
      - :marcas_ids (int[])
      - :marcas_upper (text[])
    Devuelve SQL (string) o "FALSE" si no aplicable.
    """
    # Si existen ambos campos, usar OR para cubrir datos legacy (id_marca NULL pero marca texto llena).
    try:
        has_id = _col_exists(db, "leads", "id_marca")
        has_txt = _col_exists(db, "leads", "marca")
        if has_id and has_txt:
            # Nota: comparamos `id_marca` como texto para evitar problemas de tipos (algunas BD lo guardan como TEXT).
            return "((NULLIF(btrim(COALESCE(l.id_marca::text,'')),'') IS NOT NULL AND btrim(COALESCE(l.id_marca::text,'')) = ANY(CAST(:marcas_ids_text AS text[]))) OR (upper(COALESCE(l.marca,'')) = ANY(CAST(:marcas_upper AS text[]))))"
        if has_id:
            return "(btrim(COALESCE(l.id_marca::text,'')) = ANY(CAST(:marcas_ids_text AS text[])))"
        if has_txt:
            return "(upper(COALESCE(l.marca,'')) = ANY(CAST(:marcas_upper AS text[])))"
    except Exception:
        pass
    return "FALSE"


def upsert_mvp_tasks_for_user(db: Session, *, user_id: int, username: str, role: str, marcas: list[int] | None = None) -> Dict[str, Any]:
    """Genera tareas mínimas (idempotente) y devuelve un resumen.

    MVP (Ejecutivos):
    - CONTACTAR: leads NUEVO asignados al usuario sin contacto registrado.
    - RIESGOS / SEGUIMIENTO: leads no-confirmados/no-declinados sin movimiento o cerca de reglas de auto-decline.
    """
    ensure_tasks_table(db)
    if not _tasks_table_exists(db):
        return {"ok": True, "created": 0, "skipped": 0, "disabled": True}
    # IMPORTANTE:
    # - SUPERADMIN ve todo
    # - ADMIN queda acotado por marcas (NO es global)
    is_admin = _is_superadmin_role(role)
    r_up = (role or "").strip().upper()
    is_finanzas = r_up in ("FINANZAS", "11")
    is_exec = ("EJECUTIVO" in r_up) or (r_up == "2")
    # "Mis tareas" es para ejecutar gestión (ejecutivos). Otros roles no deben autogenerar tareas masivas.
    enable_lead_tasks = bool(is_exec)
    # Nota negocio (2026-03): confirmados/declinados NO deben generar tareas.
    enable_calendar_tasks = False

    nuevo_id = _estado_id_like(db, "%NUEV%", 1)
    confirmado_id = _estado_id_like(db, "CONFIRM%", 4)
    declinado_id = _estado_id_like(db, "%DECLIN%", 5)
    contactado_id = _estado_id_like(db, "%CONTACT%", 2)
    cotizado_id = _estado_id_like(db, "%COTIZ%", 3)
    user_keys = _user_match_keys(db, user_id=int(user_id), username=(username or ""))

    marcas = list(marcas or [])
    marcas_ids: list[int] = []
    for m in marcas:
        try:
            marcas_ids.append(int(m))
        except Exception:
            pass
    # Comparación robusta: varios deploys guardan `id_marca` como TEXT, por eso también armamos una lista string.
    marcas_ids_text: list[str] = []
    seen_mid = set()
    for m in (marcas or []):
        s = str(m or "").strip()
        if not s:
            continue
        if s.isdigit():
            if s not in seen_mid:
                seen_mid.add(s)
                marcas_ids_text.append(s)
    for x in marcas_ids:
        s = str(x).strip()
        if s and s not in seen_mid:
            seen_mid.add(s)
            marcas_ids_text.append(s)
    marcas_upper: list[str] = []
    try:
        if marcas_ids and _table_exists(db, "marcas"):
            rows = db.execute(
                text("SELECT nombre FROM public.marcas WHERE id_marca = ANY(CAST(:mids AS int[]))"),
                {"mids": marcas_ids},
            ).fetchall()
            marcas_upper = [str(r[0] or "").strip().upper() for r in rows if r and str(r[0] or "").strip()]
    except Exception:
        marcas_upper = []

    # Compat: algunos esquemas legacy no tienen `leads.id_usuario`.
    # En ese caso, no podemos filtrar por "asignado" y debemos caer a:
    # - Ejecutivos: solo por marcas (si existen).
    # - Admin: todos los leads.
    has_id_usuario = _col_exists(db, "leads", "id_usuario")
    if has_id_usuario:
        assigned_sql = _assigned_to_user_sql()
        unassigned_sql = _unassigned_sql()
    else:
        assigned_sql = "FALSE"
        unassigned_sql = "TRUE"
    brand_sql = _brand_filter_sql(db)
    # Scope: asignado al usuario o (si es ejecutivo con marcas) leads sin asignar de sus marcas.
    scope_sql = assigned_sql
    if marcas_ids or marcas_upper:
        scope_sql = f"({assigned_sql} OR ({unassigned_sql} AND {brand_sql}))"
    elif is_admin and not has_id_usuario:
        # Admin sin id_usuario: no podemos asignar por usuario, así que mostramos todo.
        scope_sql = "TRUE"

    has_contact_sql = _has_contact_sql_db(db)
    notes_expr = _lead_notes_expr_db(db)
    # Algunos deploys no tienen `pre_start/pre_end`; no debemos romper la transacción.
    has_pre_start = _col_exists(db, "leads", "pre_start")
    has_pre_end = _col_exists(db, "leads", "pre_end")
    hr_missing_sql = "(l.pre_start IS NULL OR l.pre_end IS NULL)" if (has_pre_start and has_pre_end) else "FALSE"

    # Si el usuario no debe recibir tareas automáticas, limpiamos backlog previo (best-effort).
    if (not enable_lead_tasks) and (not enable_calendar_tasks):
        try:
            db.execute(
                text(
                    """
                    UPDATE public.tasks
                    SET status='skipped',
                        updated_at=now(),
                        completed_at=now(),
                        completed_by=:uname,
                        meta = meta || jsonb_build_object('auto_cleanup', true, 'reason', 'role_no_lead_tasks')
                    WHERE assigned_user_id=:uid
                      AND status='open'
                      AND (
                        (entity_type='lead' AND kind IN (
                          'CONTACTAR_LEAD',
                          'SEGUIMIENTO_PENDIENTE',
                          'RIESGO_AUTO_DECLINE_NUEVO',
                          'RIESGO_AUTO_DECLINE_CONTACTADO_SIN_FECHA',
                          'RIESGO_AUTO_DECLINE_CONTACTADO_CON_FECHA',
                          'RIESGO_COTIZADO_EVENTO_CERCA',
                          'COMPLETAR_TELEFONO',
                          'COMPLETAR_DIRECCION',
                          'COMPLETAR_HORARIO',
                          'EVENTO_PROXIMO_INCOMPLETO'
                        ))
                        OR (entity_type='gia_email' AND kind='RESPONDER_CORREO')
                      )
                    """
                ),
                {"uid": int(user_id), "uname": (username or "").strip()[:200]},
            )
        except Exception:
            pass
    else:
        # Limpieza específica: tarea legacy que ya no usamos.
        try:
            db.execute(
                text(
                    """
                    UPDATE public.tasks
                    SET status='skipped',
                        updated_at=now(),
                        completed_at=now(),
                        completed_by=:uname,
                        meta = meta || jsonb_build_object('auto_cleanup', true, 'reason', 'deprecated_kind')
                    WHERE assigned_user_id=:uid
                      AND status='open'
                      AND kind='EVENTO_PROXIMO_INCOMPLETO'
                    """
                ),
                {"uid": int(user_id), "uname": (username or "").strip()[:200]},
            )
        except Exception:
            pass

    # CONTACTAR (ventas): lead NUEVO asignado al usuario (id_usuario)
    # due_at = created_at + 24h
    try:
        db.execute(
            text(
                f"""
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'CONTACTAR_LEAD' AS kind,
                  'Contactar lead' AS title,
                  'Lead nuevo sin contacto registrado (WhatsApp/Llamada/Email).' AS description,
                  'lead' AS entity_type,
                  l.id_lead AS entity_id,
                  :uid AS assigned_user_id,
                  :uname AS assigned_username,
                  (COALESCE(l.created_at, now()) + INTERVAL '24 hours') AS due_at,
                  10 AS priority,
                  jsonb_build_object('rule','mvp_contactar','estado_id',l.id_estado)
                FROM public.leads l
                WHERE l.id_estado = :nuevo
                  AND l.fecha_evento IS NOT NULL
                  AND {scope_sql}
                  AND ({has_contact_sql}) IS FALSE
                  AND :enable_lead_tasks
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "nuevo": int(nuevo_id),
                "enable_lead_tasks": bool(enable_lead_tasks),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_ids_text": marcas_ids_text or ["0"],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    # RIESGO (ventas): leads que están a punto de caer en reglas de estancamiento/auto-declinación.
    # Consecuencia real: si no se registra seguimiento, los jobs/reglas pueden declinar automáticamente.
    # - NUEVO: a partir de 5 días sin contacto => warning (declina al día 7).
    # - CONTACTADO sin fecha_evento: a partir de 3 días sin movimiento => warning (declina al día 5).
    # - CONTACTADO con fecha_evento del mes: con comentarios, a partir de 5 días sin movimiento => warning (declina al día 7).
    # - COTIZADO con evento cercano (<= 4 días): warning inmediato.
    try:
        db.execute(
            text(
                f"""
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'RIESGO_AUTO_DECLINE_NUEVO' AS kind,
                  'Riesgo: lead se declinará' AS title,
                  'Lead NUEVO lleva 5+ días sin contacto. Si no hay seguimiento, puede declinar automáticamente.' AS description,
                  'lead',
                  l.id_lead,
                  :uid,
                  :uname,
                  (COALESCE(l.created_at, now()) + INTERVAL '7 days') AS due_at,
                  5,
                  jsonb_build_object('rule','risk_nuevo','estado_id',l.id_estado)
                FROM public.leads l
                WHERE l.id_estado = :nuevo
                  AND l.fecha_evento IS NOT NULL
                  AND {scope_sql}
                  AND ({has_contact_sql}) IS FALSE
                  AND COALESCE(l.created_at, now()) <= (now() - INTERVAL '5 days')
                  AND :enable_lead_tasks
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "nuevo": int(nuevo_id),
                "enable_lead_tasks": bool(enable_lead_tasks),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_ids_text": marcas_ids_text or ["0"],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    # SEGUIMIENTO_PENDIENTE: lead sin movimiento por X días (ventas/admin).
    # Esto NO reemplaza las reglas; solo hace visible la lista.
    try:
        db.execute(
            text(
                f"""
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'SEGUIMIENTO_PENDIENTE' AS kind,
                  'Registrar seguimiento' AS title,
                  'Lead sin movimiento hace 3+ días. Registrar contacto o próximo paso.' AS description,
                  'lead',
                  l.id_lead,
                  :uid,
                  :uname,
                  (COALESCE(l.updated_at, l.created_at, now()) + INTERVAL '4 days') AS due_at,
                  12,
                  jsonb_build_object('rule','stale_any','estado_id',l.id_estado,'last_at',COALESCE(l.updated_at,l.created_at,now()))
                FROM public.leads l
                WHERE l.id_estado NOT IN (:decl, :conf)
                  AND (:is_admin OR {scope_sql})
                  AND COALESCE(l.updated_at, l.created_at, now()) <= (now() - INTERVAL '3 days')
                  AND :enable_lead_tasks
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "is_admin": bool(is_admin),
                "enable_lead_tasks": bool(enable_lead_tasks),
                "decl": int(declinado_id),
                "conf": int(confirmado_id),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_ids_text": marcas_ids_text or ["0"],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    # Nota: se eliminó la tarea "EVENTO_PROXIMO_INCOMPLETO" (hoy/mañana) porque el proceso real es semanal
    # (confirmados de la semana) y se gestiona con tareas específicas: COMPLETAR_TELEFONO/DIRECCION/HORARIO.

    try:
        db.execute(
            text(
                f"""
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'RIESGO_AUTO_DECLINE_CONTACTADO_SIN_FECHA' AS kind,
                  'Riesgo: lead sin movimiento' AS title,
                  'Lead CONTACTADO sin fecha_evento y sin movimiento. Si no hay seguimiento, puede declinar automáticamente.' AS description,
                  'lead',
                  l.id_lead,
                  :uid,
                  :uname,
                  (COALESCE(l.updated_at, l.created_at, now()) + INTERVAL '5 days') AS due_at,
                  6,
                  jsonb_build_object('rule','risk_contactado_sin_fecha','estado_id',l.id_estado)
                FROM public.leads l
                WHERE l.id_estado = :contactado
                  AND l.fecha_evento IS NULL
                  AND (:is_admin OR {scope_sql})
                  AND COALESCE(l.updated_at, l.created_at, now()) <= (now() - INTERVAL '3 days')
                  AND :enable_lead_tasks
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "contactado": int(contactado_id),
                "is_admin": bool(is_admin),
                "enable_calendar_tasks": bool(enable_calendar_tasks),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_ids_text": marcas_ids_text or ["0"],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    try:
        db.execute(
            text(
                f"""
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'RIESGO_AUTO_DECLINE_CONTACTADO_CON_FECHA' AS kind,
                  'Riesgo: lead con fecha estancado' AS title,
                  'Lead CONTACTADO con fecha_evento del mes y comentarios, sin movimiento. Si no hay seguimiento, puede declinar automáticamente.' AS description,
                  'lead',
                  l.id_lead,
                  :uid,
                  :uname,
                  (COALESCE(l.updated_at, l.created_at, now()) + INTERVAL '7 days') AS due_at,
                  6,
                  jsonb_build_object('rule','risk_contactado_con_fecha','estado_id',l.id_estado,'fecha_evento',l.fecha_evento)
                FROM public.leads l
                WHERE l.id_estado = :contactado
                  AND l.fecha_evento IS NOT NULL
                  AND EXTRACT(YEAR FROM l.fecha_evento) = EXTRACT(YEAR FROM CURRENT_DATE)
                  AND EXTRACT(MONTH FROM l.fecha_evento) = EXTRACT(MONTH FROM CURRENT_DATE)
                  AND (COALESCE(NULLIF(btrim({notes_expr}),''), NULL) IS NOT NULL)
                  AND (:is_admin OR {scope_sql})
                  AND COALESCE(l.updated_at, l.created_at, now()) <= (now() - INTERVAL '5 days')
                  AND :enable_lead_tasks
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "contactado": int(contactado_id),
                "is_admin": bool(is_admin),
                "enable_lead_tasks": bool(enable_lead_tasks),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_ids_text": marcas_ids_text or ["0"],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    try:
        db.execute(
            text(
                f"""
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'RIESGO_COTIZADO_EVENTO_CERCA' AS kind,
                  'Riesgo: evento cerca sin confirmar' AS title,
                  'Lead COTIZADO con evento cercano (<= 4 días) sin confirmar. Requiere acción inmediata.' AS description,
                  'lead',
                  l.id_lead,
                  :uid,
                  :uname,
                  now() + INTERVAL '2 hours' AS due_at,
                  4,
                  jsonb_build_object('rule','risk_cotizado_evento_cerca','estado_id',l.id_estado,'fecha_evento',l.fecha_evento)
                FROM public.leads l
                WHERE l.id_estado = :cotizado
                  AND l.fecha_evento IS NOT NULL
                  AND l.fecha_evento <= (CURRENT_DATE + 4)
                  AND (:is_admin OR {scope_sql})
                  AND :enable_lead_tasks
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "cotizado": int(cotizado_id),
                "is_admin": bool(is_admin),
                "enable_lead_tasks": bool(enable_lead_tasks),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_ids_text": marcas_ids_text or ["0"],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    # (2026-03) Se elimina autogeneración "calendario" para confirmados: se gestiona fuera de tareas.

    # (2026-03) declinados no generan tareas (ni siquiera Admin).

    # RESPONDER_CORREO (DESHABILITADO)
    # El usuario pidió sacar Correos de tareas (se gestiona fuera del tablero de tareas).
    try:
        enable_mail_tasks = (str(os.getenv("CRM_ENABLE_MAIL_TASKS", "0")).strip() in ("1", "true", "TRUE", "yes", "YES"))
        if enable_mail_tasks and _table_exists(db, "gia_email_messages") and (is_exec or is_admin):
            # columnas necesarias
            has_reply_sent = _col_exists(db, "gia_email_messages", "reply_sent")
            has_kind = _col_exists(db, "gia_email_messages", "kind")
            has_inbox_type = _col_exists(db, "gia_email_messages", "inbox_type")
            if has_reply_sent:
                inbox_sql = "COALESCE(m.inbox_type,'sales')" if has_inbox_type else "'sales'"
                kind_sql = "COALESCE(m.kind,'other')" if has_kind else "'other'"

                # filtro por tipo de inbox según rol
                sales_filter = f"({inbox_sql}='sales' AND {kind_sql} IN ('lead','purchase','other'))"
                pay_filter = f"(({inbox_sql}='payments') OR ({kind_sql}='payment'))"
                role_filter = sales_filter if (not is_admin and not is_finanzas) else f"({sales_filter} OR {pay_filter})"

                db.execute(
                    text(
                        f"""
                        INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                        SELECT
                          'RESPONDER_CORREO' AS kind,
                          'Responder correo' AS title,
                          'Correo recibido pendiente de respuesta.' AS description,
                          'gia_email' AS entity_type,
                          m.id_msg AS entity_id,
                          :uid AS assigned_user_id,
                          :uname AS assigned_username,
                          (COALESCE(m.received_at, m.created_at, now()) + INTERVAL '6 hours') AS due_at,
                          CASE WHEN {kind_sql}='payment' OR {inbox_sql}='payments' THEN 15 ELSE 25 END AS priority,
                          jsonb_build_object(
                            'rule','gia_email_reply',
                            'id_marca', m.id_marca,
                            'marca', COALESCE(m.marca,''),
                            'inbox_type', {inbox_sql},
                            'kind', {kind_sql},
                            'subject', COALESCE(m.subject,''),
                            'from', COALESCE(m.from_email,'')
                          ) AS meta
                        FROM public.gia_email_messages m
                        WHERE COALESCE(m.reply_sent,false) IS FALSE
                          AND {role_filter}
	                          AND (
	                            :is_admin
	                            OR (
	                              m.id_marca IS NOT NULL
	                              AND btrim(COALESCE(m.id_marca::text,'')) = ANY(CAST(:marcas_ids_text AS text[]))
	                            )
	                          )
	                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "uid": int(user_id),
                        "uname": (username or "").strip()[:200],
                        "is_admin": bool(is_admin),
                        "marcas_ids_text": marcas_ids_text or ["0"],
                    },
                )
    except Exception:
        pass

    # AUTO-CLOSE: si el lead ya tiene evidencia de seguimiento, cerramos tareas abiertas que dependen de eso.
    # Esto cubre el caso donde el ejecutivo registra seguimiento directamente en el lead (no desde la vista de tareas),
    # y evita que la tarea siga apareciendo por meses.
    try:
        followup_kinds = [
            "CONTACTAR_LEAD",
            "RIESGO_AUTO_DECLINE_NUEVO",
            "RIESGO_AUTO_DECLINE_CONTACTADO_SIN_FECHA",
            "RIESGO_AUTO_DECLINE_CONTACTADO_CON_FECHA",
            "RIESGO_COTIZADO_EVENTO_CERCA",
            "DECLINADO_FECHA_FUTURA",
            "SEGUIMIENTO_PENDIENTE",
            "REVISAR_DECLINADO_FUTURO",
        ]
        db.execute(
            text(
                f"""
                UPDATE public.tasks t
                SET status='done',
                    completed_at=now(),
                    completed_by='AUTO',
                    updated_at=now(),
                    meta = t.meta || jsonb_build_object('auto_closed', true, 'auto_reason', 'has_followup', 'auto_at', now())
                WHERE t.assigned_user_id = :uid
                  AND t.status = 'open'
                  AND t.entity_type = 'lead'
                  AND t.kind = ANY(CAST(:kinds AS text[]))
                  AND EXISTS (
                    SELECT 1
                    FROM public.leads l
                    WHERE l.id_lead = t.entity_id
                      AND ({has_contact_sql}) IS TRUE
                  )
                """
            ),
            {"uid": int(user_id), "kinds": followup_kinds},
        )
    except Exception:
        pass

    # AUTO-CLOSE: confirmados/declinados NO deben quedar como tareas abiertas.
    try:
        if _table_exists(db, "leads") and _col_exists(db, "leads", "id_estado"):
            confirmado_id = _estado_id_like(db, "CONFIRM%", 4)
            declinado_id = _estado_id_like(db, "%DECLIN%", 5)
            db.execute(
                text(
                    """
                    UPDATE public.tasks t
                    SET status='done',
                        completed_at=now(),
                        completed_by='AUTO',
                        updated_at=now(),
                        meta = COALESCE(t.meta,'{}'::jsonb) || jsonb_build_object(
                          'auto_closed', true,
                          'auto_reason', 'lead_confirmed_or_declined',
                          'auto_at', now()
                        )
                    WHERE t.assigned_user_id = :uid
                      AND t.status = 'open'
                      AND t.entity_type = 'lead'
                      AND EXISTS (
                        SELECT 1
                        FROM public.leads l
                        WHERE l.id_lead = t.entity_id
                          AND l.id_estado = ANY(CAST(:closed_estados AS int[]))
                      )
                    """
                ),
                {"uid": int(user_id), "closed_estados": [int(confirmado_id), int(declinado_id)]},
            )
    except Exception:
        pass

    summary: Dict[str, Any] = {"ok": True, "counts": {}, "overdue": {}}
    try:
        row = db.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE status='open')::int AS open_total,
                  COUNT(*) FILTER (WHERE status='open' AND due_at IS NOT NULL AND due_at < now())::int AS overdue_total,
                  COUNT(*) FILTER (WHERE status='open' AND kind='CONTACTAR_LEAD')::int AS open_contactar,
                  COUNT(*) FILTER (WHERE status='open' AND kind='CONTACTAR_LEAD' AND due_at IS NOT NULL AND due_at < now())::int AS overdue_contactar,
                  COUNT(*) FILTER (WHERE status='open' AND kind LIKE 'COMPLETAR_%')::int AS open_calendario,
                  COUNT(*) FILTER (WHERE status='open' AND kind LIKE 'COMPLETAR_%' AND due_at IS NOT NULL AND due_at < now())::int AS overdue_calendario,
                  0::int AS open_correos,
                  0::int AS overdue_correos
                FROM public.tasks
                WHERE assigned_user_id = :uid
                """
            ),
            {"uid": int(user_id)},
        ).mappings().first()
        if row:
            summary["counts"] = {
                "open_total": int(row.get("open_total") or 0),
                "open_contactar": int(row.get("open_contactar") or 0),
                "open_calendario": int(row.get("open_calendario") or 0),
                "open_correos": 0,
            }
            summary["overdue"] = {
                "overdue_total": int(row.get("overdue_total") or 0),
                "overdue_contactar": int(row.get("overdue_contactar") or 0),
                "overdue_calendario": int(row.get("overdue_calendario") or 0),
                "overdue_correos": 0,
            }
    except Exception:
        pass

    return summary


# ---------------------------------------------------------------------------
# 2026-03: Nueva lógica de tareas (solo NUEVO/CONTACTADO/COTIZADO)
# Nota: redefinimos `upsert_mvp_tasks_for_user` para no depender del bloque legacy.
# ---------------------------------------------------------------------------
def upsert_mvp_tasks_for_user(db: Session, *, user_id: int, username: str, role: str, marcas: list[int] | None = None) -> Dict[str, Any]:
    """Genera tareas de seguimiento (idempotente) y devuelve un resumen.

    Reglas (2026-03):
    - SOLO 3 estados generan tareas: NUEVO / CONTACTADO / COTIZADO.
    - CONFIRMADO/DECLINADO nunca generan tareas.
    - Si `fecha_evento` ya pasó (en esos estados), se auto-declina el lead y NO se generan tareas.

    NUEVO:
      - Con fecha_evento: si no hay seguimiento en 3 días desde creación.
      - Sin fecha_evento: si no hay seguimiento en 5 días desde creación.
    CONTACTADO:
      - Con fecha_evento: si vence <= 7 días y último seguimiento > 3 días.
      - Sin fecha_evento: si último seguimiento > 3 días (seguimiento inmediato).
    COTIZADO:
      - Si último seguimiento > 3 días (con o sin fecha_evento).
      - Si vence <= 2 días, prioridad alta.
      - Si fecha_evento ya pasó, se auto-declina.
    """
    ensure_tasks_table(db)
    if not _tasks_table_exists(db):
        return {"ok": True, "counts": {"open_total": 0}, "overdue": {"overdue_total": 0}, "disabled": True}

    # Solo SUPERADMIN es "global". ADMIN queda acotado por marcas/asignación.
    is_admin = _is_superadmin_role(role)
    r_up = (role or "").strip().upper()

    # IDs de estados (compat)
    nuevo_id = _estado_id_like(db, "%NUEV%", 1)
    contactado_id = _estado_id_like(db, "%CONTACT%", 2)
    cotizado_id = _estado_id_like(db, "%COTIZ%", 3)
    confirmado_id = _estado_id_like(db, "CONFIRM%", 4)
    declinado_id = _estado_id_like(db, "%DECLIN%", 5)

    user_keys = _user_match_keys(db, user_id=int(user_id), username=(username or ""))

    # Marcas (para scope de ejecutivos)
    marcas = list(marcas or [])
    marcas_ids: list[int] = []
    for m in marcas:
        try:
            marcas_ids.append(int(m))
        except Exception:
            pass

    marcas_ids_text: list[str] = []
    seen_mid = set()
    for m in (marcas or []):
        s = str(m or "").strip()
        if not s:
            continue
        if s.isdigit() and s not in seen_mid:
            seen_mid.add(s)
            marcas_ids_text.append(s)
    for x in marcas_ids:
        s = str(x).strip()
        if s and s not in seen_mid:
            seen_mid.add(s)
            marcas_ids_text.append(s)

    marcas_upper: list[str] = []
    try:
        if marcas_ids and _table_exists(db, "marcas"):
            col = "nombre" if _col_exists(db, "marcas", "nombre") else ("marca" if _col_exists(db, "marcas", "marca") else "nombre")
            rows = db.execute(
                text(f"SELECT {col} FROM public.marcas WHERE id_marca = ANY(CAST(:mids AS int[]))"),
                {"mids": marcas_ids},
            ).fetchall()
            marcas_upper = [str(r[0] or "").strip().upper() for r in rows if r and str(r[0] or "").strip()]
    except Exception:
        marcas_upper = []

    # Role puede venir vacío (schemas legacy); inferimos por marcas.
    is_exec = ("EJECUTIVO" in r_up) or (r_up == "2") or ((not r_up) and bool(marcas_ids_text or marcas_upper))
    # Último fallback: si el usuario tiene leads asignados, lo tratamos como ejecutivo para no dejar tareas en 0
    # cuando falta `rol`/`role` o `usuarios_marcas`.
    if not is_exec:
        try:
            if _table_exists(db, "leads") and _col_exists(db, "leads", "id_usuario"):
                any_assigned = db.execute(
                    text(
                        f"""
                        SELECT 1
                        FROM public.leads l
                        WHERE {_assigned_to_user_sql()}
                        LIMIT 1
                        """
                    ),
                    {"user_keys": user_keys},
                ).scalar()
                if any_assigned:
                    is_exec = True
        except Exception:
            pass
    if not is_exec:
        # No generar masivo para otros roles; limpiar este set.
        try:
            db.execute(
                text(
                    """
                    UPDATE public.tasks
                    SET status='done', completed_at=now(), completed_by='AUTO', updated_at=now(),
                        meta = COALESCE(meta,'{}'::jsonb) || jsonb_build_object('auto_closed', true, 'auto_reason', 'tasks_disabled', 'auto_at', now())
                    WHERE assigned_user_id=:uid AND status='open'
                      AND kind = ANY(CAST(:k AS text[]))
                    """
                ),
                {"uid": int(user_id), "k": ["LEAD_NUEVO_SEGUIMIENTO", "LEAD_CONTACTADO_SEGUIMIENTO", "LEAD_COTIZADO_SEGUIMIENTO"]},
            )
        except Exception:
            pass
        return {"ok": True, "counts": {"open_total": 0}, "overdue": {"overdue_total": 0}, "disabled": True}

    # Scope por usuario/marcas
    has_id_usuario = _col_exists(db, "leads", "id_usuario")
    if has_id_usuario:
        assigned_sql = _assigned_to_user_sql()
        unassigned_sql = _unassigned_sql()
    else:
        assigned_sql = "FALSE"
        unassigned_sql = "TRUE"
    brand_sql = _brand_filter_sql(db)
    scope_sql = assigned_sql
    if marcas_ids_text or marcas_upper:
        scope_sql = f"({assigned_sql} OR ({unassigned_sql} AND {brand_sql}))"
    elif is_admin and not has_id_usuario:
        scope_sql = "TRUE"

    # Timestamps (compat)
    # `fecha_ingreso` es el campo más confiable en el CRM (DATE NOT NULL). Si created_at/updated_at vienen NULL,
    # NO debemos caer a now() porque mata la generación de tareas (queda todo como "recién creado").
    created_cols = [c for c in ("created_at", "fecha_ingreso", "fecha_creacion", "creado_at", "created") if _col_exists(db, "leads", c)]
    created_terms: list[str] = []
    for c in created_cols:
        # date + interval funciona, pero lo forzamos a timestamp para comparaciones consistentes con now().
        if c in ("fecha_ingreso", "fecha_creacion"):
            created_terms.append(f"l.{c}::timestamp")
        else:
            created_terms.append(f"l.{c}")
    # COALESCE requiere >=2 argumentos en Postgres; si no hay columnas disponibles, usamos now() directo.
    created_expr = "now()"
    if created_terms:
        created_expr = "COALESCE(" + ", ".join(created_terms + ["now()"]) + ")"
    # Seguimiento real: preferimos `seguimiento_at` si existe, porque `updated_at` puede moverse por cambios de estado/edición.
    # Para reglas de tareas, NO debemos usar updated_at como evidencia de seguimiento.
    follow_cols: list[str] = []
    try:
        if _col_exists(db, "leads", "seguimiento_at"):
            follow_cols.append("l.seguimiento_at")
        elif _col_exists(db, "leads", "followup_at"):
            follow_cols.append("l.followup_at")
    except Exception:
        follow_cols = []
    follow_expr = "NULL"
    if follow_cols:
        follow_expr = "COALESCE(" + ", ".join(follow_cols) + ")"
    updated_cols = [c for c in ("updated_at", "fecha_modificacion", "modificado_at", "updated") if _col_exists(db, "leads", c)]
    last_expr = "COALESCE(" + ", ".join(follow_cols + [f"l.{c}" for c in updated_cols] + [created_expr, "now()"]) + ")"
    # Para reglas (vencimientos/seguimiento), usamos seguimiento real o created_expr como fallback.
    last_follow_expr = f"COALESCE({follow_expr}, {created_expr})"

    has_contact_sql = _has_contact_sql_db(db)
    ev_date = _lead_event_date_expr_db(db, alias="l")
    # Chile (America/Santiago): evita desfaces en borde de mes por timezone del server.
    today = "(now() AT TIME ZONE 'America/Santiago')::date"
    month_start = f"date_trunc('month', {today})::date"
    month_end = f"(date_trunc('month', {today}) + INTERVAL '1 month')::date"
    # Regla negocio (tareas SOLO para leads "del mes"):
    # - con fecha_evento: solo mes actual (y no vencidos)
    # - sin fecha_evento: siempre aplica (las tareas se rigen por seguimiento/antigüedad)
    #   (la noción de "mes actual" se define por fecha_evento; created_at queda para reportes/estadísticas).
    in_scope_with_date = f"({ev_date} IS NOT NULL AND {ev_date} >= {today} AND {ev_date} >= {month_start} AND {ev_date} < {month_end})"
    in_scope_no_date = f"({ev_date} IS NULL)"
    ev_in_scope = f"({in_scope_with_date} OR {in_scope_no_date})"

    # 1) Auto-declinar vencidos (fecha_evento < hoy) en estados que generan tareas
    try:
        if _table_exists(db, "leads") and _col_exists(db, "leads", "fecha_evento") and _col_exists(db, "leads", "id_estado"):
            sets: list[str] = ["id_estado = :decl"]
            if _col_exists(db, "leads", "declinado_at"):
                sets.append("declinado_at = COALESCE(declinado_at, now())")
            if _col_exists(db, "leads", "declinado_motivo"):
                sets.append("declinado_motivo = COALESCE(NULLIF(btrim(declinado_motivo),''), 'LEAD PERDIDO')")
            note_cols: list[str] = []
            if _col_exists(db, "leads", "notas"):
                note_cols.append("notas")
            if _col_exists(db, "leads", "seguimiento"):
                note_cols.append("seguimiento")
            if note_cols:
                reason_sql = f"""
                  (CASE
                    WHEN {last_follow_expr} >= (now() - INTERVAL '2 days') THEN 'LEAD PERDIDO · cliente no contestó'
                    ELSE 'LEAD PERDIDO · no seguimiento/no respuesta'
                  END)
                """
                for col in note_cols:
                    sets.append(
                        f"""{col} = COALESCE({col},'') || CASE WHEN COALESCE({col},'')='' THEN '' ELSE E'\\n\\n' END
                              || '[AUTO] ' || to_char(now(),'YYYY-MM-DD HH24:MI') || E' · Sistema\\n' || {reason_sql}"""
                    )
            db.execute(
                text(
                    f"""
                    UPDATE public.leads l
                    SET {", ".join(sets)}
                    WHERE l.id_estado = ANY(CAST(:st AS int[]))
                      AND ({ev_date}) IS NOT NULL
                      AND ({ev_date}) < {today}
                      AND (:is_admin OR {scope_sql})
                    """
                ),
                {
                    "decl": int(declinado_id),
                    "st": [int(nuevo_id), int(contactado_id), int(cotizado_id)],
                    "is_admin": bool(is_admin),
                    "user_keys": user_keys,
                    "marcas_ids_text": marcas_ids_text or ["0"],
                    "marcas_upper": marcas_upper or ["__NONE__"],
                },
            )
    except Exception:
        pass

    # 1.a0) Auto-close: tareas colgadas cuyo lead ya no existe (borrado físico)
    # o fue borrado lógicamente (is_deleted=true).
    # Esto evita el error "Lead no existe" desde el UI de Tareas.
    try:
        if _table_exists(db, "leads") and _col_exists(db, "leads", "id_lead"):
            has_is_deleted = False
            try:
                has_is_deleted = _col_exists(db, "leads", "is_deleted")
            except Exception:
                has_is_deleted = False
            db.execute(
                text(
                    """
                    UPDATE public.tasks t
                    SET status='done', completed_at=now(), completed_by='AUTO', updated_at=now(),
                        meta = COALESCE(t.meta,'{}'::jsonb) || jsonb_build_object(
                          'auto_closed', true,
                          'auto_reason', 'lead_missing',
                          'auto_at', now()
                        )
                    WHERE t.assigned_user_id=:uid
                      AND t.status='open'
                      AND t.entity_type='lead'
                      AND (
                        NOT EXISTS (SELECT 1 FROM public.leads l WHERE l.id_lead = t.entity_id)
                        OR (:has_is_deleted AND EXISTS (SELECT 1 FROM public.leads ld WHERE ld.id_lead=t.entity_id AND COALESCE(ld.is_deleted,false)=true))
                      )
                    """
                ),
                {"uid": int(user_id), "has_is_deleted": bool(has_is_deleted)},
            )
    except Exception:
        pass

    # 1.a) Si por alguna razón el lead vencido no cambió de estado, igual no debe quedar como tarea abierta.
    try:
        if _table_exists(db, "leads") and _col_exists(db, "leads", "fecha_evento"):
            db.execute(
                text(
                    f"""
	                    UPDATE public.tasks t
	                    SET status='done', completed_at=now(), completed_by='AUTO', updated_at=now(),
	                        meta = COALESCE(t.meta,'{{}}'::jsonb) || jsonb_build_object(
	                          'auto_closed', true,
	                          'auto_reason', 'event_expired',
	                          'auto_at', now()
	                        )
                    WHERE t.assigned_user_id=:uid
                      AND t.status='open'
                      AND t.entity_type='lead'
                      AND EXISTS (
                        SELECT 1
                        FROM public.leads l
                        WHERE l.id_lead = t.entity_id
	                          AND ({ev_date}) IS NOT NULL
	                          AND ({ev_date}) < {today}
	                      )
	                    """
	                ),
	                {"uid": int(user_id)},
	            )
    except Exception:
        pass

    # 1.a2) Auto-close: tareas fuera del mes actual (evita ruido por leads del próximo mes).
    try:
        if _table_exists(db, "leads") and _col_exists(db, "leads", "fecha_evento"):
            db.execute(
                text(
                    f"""
                    UPDATE public.tasks t
                    SET status='done', completed_at=now(), completed_by='AUTO', updated_at=now(),
                        meta = COALESCE(t.meta,'{{}}'::jsonb) || jsonb_build_object(
                          'auto_closed', true,
                          'auto_reason', 'out_of_month',
                          'auto_at', now()
                        )
                    WHERE t.assigned_user_id=:uid
                      AND t.status='open'
                      AND t.entity_type='lead'
                      AND EXISTS (
                        SELECT 1
                        FROM public.leads l
                        WHERE l.id_lead = t.entity_id
                          AND ({ev_date}) IS NOT NULL
                          AND ({ev_date}) >= {month_end}
                      )
                    """
                ),
                {"uid": int(user_id)},
            )
    except Exception:
        pass

    # 1.b) Limpieza: el set legacy de tareas ya no aplica (evita conteos desfasados).
    allowed_kinds = ["LEAD_NUEVO_SEGUIMIENTO", "LEAD_CONTACTADO_SEGUIMIENTO", "LEAD_COTIZADO_SEGUIMIENTO"]
    try:
        db.execute(
            text(
                """
                UPDATE public.tasks
                SET status='done', completed_at=now(), completed_by='AUTO', updated_at=now(),
                    meta = COALESCE(meta,'{}'::jsonb) || jsonb_build_object('auto_closed', true, 'auto_reason', 'legacy_task_disabled', 'auto_at', now())
                WHERE assigned_user_id=:uid
                  AND status='open'
                  AND kind <> ALL(CAST(:allowed AS text[]))
                """
            ),
            {"uid": int(user_id), "allowed": allowed_kinds},
        )
    except Exception:
        pass

    # 2) Insert NUEVO (sin seguimiento) según umbral 3/5 días
    try:
        db.execute(
            text(
                f"""
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'LEAD_NUEVO_SEGUIMIENTO',
                  'Lead nuevo: seguimiento',
                  CASE WHEN l.fecha_evento IS NULL
                    THEN 'NUEVO sin fecha. Seguimiento (5 días).'
                    ELSE 'NUEVO con fecha. Seguimiento (3 días).'
                  END,
                  'lead',
                  l.id_lead,
                  :uid,
                  :uname,
                  CASE WHEN l.fecha_evento IS NULL
                    THEN ({last_follow_expr} + INTERVAL '5 days')
                    ELSE ({last_follow_expr} + INTERVAL '3 days')
                  END,
                  12,
                  jsonb_build_object('rule','nuevo','created_at',{created_expr},'fecha_evento',l.fecha_evento)
                FROM public.leads l
                WHERE l.id_estado = :nuevo
                  AND l.id_estado NOT IN (:decl, :conf)
                  AND (:is_admin OR {scope_sql})
                  AND ({has_contact_sql}) IS NOT TRUE
                  AND {ev_in_scope}
                  AND (
                    (l.fecha_evento IS NOT NULL AND {last_follow_expr} <= (now() - INTERVAL '3 days'))
                    OR
                    (l.fecha_evento IS NULL AND {last_follow_expr} <= (now() - INTERVAL '5 days'))
                  )
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "nuevo": int(nuevo_id),
                "decl": int(declinado_id),
                "conf": int(confirmado_id),
                "is_admin": bool(is_admin),
                "user_keys": user_keys,
                "marcas_ids_text": marcas_ids_text or ["0"],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    # 3) Insert CONTACTADO (>3 días sin seguimiento). Con fecha: solo si vence <= 7 días.
    try:
        db.execute(
            text(
                f"""
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'LEAD_CONTACTADO_SEGUIMIENTO',
                  'Lead contactado: seguimiento',
                  CASE
                    WHEN ({ev_date}) IS NULL THEN 'CONTACTADO sin fecha. Seguimiento inmediato.'
                    WHEN ({ev_date}) < {today} THEN 'Evento vencido (auto-decline).'
                    ELSE 'Vence en ' || GREATEST(0, (({ev_date}) - {today}))::int || ' día(s).'
                  END,
                  'lead',
                  l.id_lead,
                  :uid,
                  :uname,
                  ({last_follow_expr} + INTERVAL '3 days'),
                  10,
                  jsonb_build_object('rule','contactado','last_at',{last_expr},'fecha_evento',l.fecha_evento,'days_to_event',CASE WHEN ({ev_date}) IS NULL THEN NULL ELSE (({ev_date}) - {today})::int END)
                FROM public.leads l
                WHERE l.id_estado = :contactado
                  AND l.id_estado NOT IN (:decl, :conf)
                  AND (:is_admin OR {scope_sql})
                  AND {last_follow_expr} <= (now() - INTERVAL '3 days')
                  AND (
                    {in_scope_no_date}
                    OR
                    (({ev_date}) IS NOT NULL AND ({ev_date}) >= {today} AND ({ev_date}) < {month_end} AND ({ev_date}) <= ({today} + INTERVAL '7 days'))
                  )
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "contactado": int(contactado_id),
                "decl": int(declinado_id),
                "conf": int(confirmado_id),
                "is_admin": bool(is_admin),
                "user_keys": user_keys,
                "marcas_ids_text": marcas_ids_text or ["0"],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    # 4) Insert COTIZADO (>3 días sin seguimiento). Prioridad alta si vence <= 2 días.
    try:
        db.execute(
            text(
                f"""
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'LEAD_COTIZADO_SEGUIMIENTO',
                  'Lead cotizado: seguimiento',
                  CASE
                    WHEN ({ev_date}) IS NULL THEN 'COTIZADO sin fecha. Seguimiento pendiente.'
                    WHEN ({ev_date}) < {today} THEN 'Evento vencido (auto-decline).'
                    ELSE 'Vence en ' || GREATEST(0, (({ev_date}) - {today}))::int || ' día(s).'
                  END,
                  'lead',
                  l.id_lead,
                  :uid,
                  :uname,
                  ({last_follow_expr} + INTERVAL '3 days'),
                  CASE WHEN ({ev_date}) IS NOT NULL AND (({ev_date}) - {today}) <= 2 THEN 6 ELSE 12 END,
                  jsonb_build_object('rule','cotizado','last_at',{last_expr},'fecha_evento',l.fecha_evento,'days_to_event',CASE WHEN ({ev_date}) IS NULL THEN NULL ELSE (({ev_date}) - {today})::int END)
                FROM public.leads l
                WHERE l.id_estado = :cotizado
                  AND l.id_estado NOT IN (:decl, :conf)
                  AND (:is_admin OR {scope_sql})
                  AND {last_follow_expr} <= (now() - INTERVAL '3 days')
                  AND {ev_in_scope}
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "cotizado": int(cotizado_id),
                "decl": int(declinado_id),
                "conf": int(confirmado_id),
                "is_admin": bool(is_admin),
                "user_keys": user_keys,
                "marcas_ids_text": marcas_ids_text or ["0"],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    # 4b) Insert COBRO PENDIENTE (Finanzas): eventos confirmados con abono y saldo pendiente (2do pago).
    # Nota: esto NO es una tarea de lead, porque los leads confirmados se ocultan del módulo de tareas.
    # Usamos entity_type='fin_evento' (fin_eventos.id_evento) para mantenerlo visible.
    try:
        if _table_exists(db, "fin_eventos") and _col_exists(db, "fin_eventos", "id_evento") and _col_exists(db, "fin_eventos", "id_lead"):
            db.execute(
                text(
                    f"""
                    INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                    SELECT
                      'COBRO_PENDIENTE',
                      'Cobro pendiente',
                      CASE
                        WHEN fe.fecha_evento < {today} THEN 'Cobro atrasado (evento pasado). Saldo pendiente: $' || to_char(COALESCE(fe.saldo,0), 'FM999G999G999G999')
                        WHEN fe.fecha_evento = {today} THEN 'Cobro pendiente hoy. Saldo pendiente: $' || to_char(COALESCE(fe.saldo,0), 'FM999G999G999G999')
                        ELSE 'Cobro pendiente. Evento en ' || GREATEST(0, (fe.fecha_evento - {today}))::int || ' día(s). Saldo: $' || to_char(COALESCE(fe.saldo,0), 'FM999G999G999G999')
                      END,
                      'fin_evento',
                      fe.id_evento,
                      :uid,
                      :uname,
                      (fe.fecha_evento::timestamp + INTERVAL '23 hours 59 minutes'),
                      CASE WHEN fe.fecha_evento <= {today} THEN 6 ELSE 14 END,
                      jsonb_build_object(
                        'rule','cobro_pendiente',
                        'id_lead', fe.id_lead,
                        'marca', COALESCE(NULLIF(btrim(fe.marca),''), NULL),
                        'fecha_evento', fe.fecha_evento,
                        'abono', COALESCE(fe.abono,0),
                        'saldo', COALESCE(fe.saldo,0)
                      )
                    FROM public.fin_eventos fe
                    JOIN public.leads l ON l.id_lead = fe.id_lead
                    WHERE COALESCE(fe.fecha_evento, NULL) IS NOT NULL
                      AND fe.fecha_evento >= {month_start} AND fe.fecha_evento < {month_end}
                      AND COALESCE(fe.abono,0) > 0
                      AND COALESCE(fe.saldo,0) > 0
                      AND l.id_estado = :conf
                      -- Cobros: siempre por dueño del lead (no por marca).
                      AND (:is_admin OR {assigned_sql})
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "uid": int(user_id),
                    "uname": (username or "").strip()[:200],
                    "conf": int(confirmado_id),
                    "is_admin": bool(is_admin),
                    "user_keys": user_keys,
                    "marcas_ids_text": marcas_ids_text or ["0"],
                    "marcas_upper": marcas_upper or ["__NONE__"],
                },
            )
    except Exception:
        pass

    # 5) Auto-close: tareas que ya no aplican (estado cambió / seguimiento reciente / fecha ya no calza)
    def _auto_close(kind: str, cond_sql: str) -> None:
        try:
            db.execute(
                text(
                    f"""
                    UPDATE public.tasks t
                    SET status='done', completed_at=now(), completed_by='AUTO', updated_at=now(),
                        meta = COALESCE(t.meta,'{{}}'::jsonb) || jsonb_build_object('auto_closed', true, 'auto_reason', 'no_longer_needed', 'auto_at', now())
                    WHERE t.assigned_user_id = :uid
                      AND t.status='open'
                      AND t.entity_type='lead'
                      AND t.kind=:k
                      AND NOT EXISTS (
                        SELECT 1
                        FROM public.leads l
                        WHERE l.id_lead = t.entity_id
                          AND {cond_sql}
                      )
                    """
                ),
                {
                    "uid": int(user_id),
                    "k": kind,
                    "nuevo": int(nuevo_id),
                    "contactado": int(contactado_id),
                    "cotizado": int(cotizado_id),
                    "decl": int(declinado_id),
                    "conf": int(confirmado_id),
                    "is_admin": bool(is_admin),
                    "user_keys": user_keys,
                    "marcas_ids_text": marcas_ids_text or ["0"],
                    "marcas_upper": marcas_upper or ["__NONE__"],
                },
            )
        except Exception:
            pass

    _auto_close(
        "LEAD_NUEVO_SEGUIMIENTO",
        f"""
          l.id_estado = :nuevo
          AND l.id_estado NOT IN (:decl, :conf)
          AND (:is_admin OR {scope_sql})
          AND ({has_contact_sql}) IS NOT TRUE
          AND {ev_in_scope}
          AND (
            (l.fecha_evento IS NOT NULL AND {last_follow_expr} <= (now() - INTERVAL '3 days'))
            OR
            (l.fecha_evento IS NULL AND {last_follow_expr} <= (now() - INTERVAL '5 days'))
          )
        """,
    )
    _auto_close(
        "LEAD_CONTACTADO_SEGUIMIENTO",
        f"""
          l.id_estado = :contactado
          AND l.id_estado NOT IN (:decl, :conf)
          AND (:is_admin OR {scope_sql})
          AND {last_follow_expr} <= (now() - INTERVAL '3 days')
          AND (
            {in_scope_no_date}
            OR
            (({ev_date}) IS NOT NULL AND ({ev_date}) >= {today} AND ({ev_date}) < {month_end} AND ({ev_date}) <= ({today} + INTERVAL '7 days'))
          )
        """,
    )
    _auto_close(
        "LEAD_COTIZADO_SEGUIMIENTO",
        f"""
          l.id_estado = :cotizado
          AND l.id_estado NOT IN (:decl, :conf)
          AND (:is_admin OR {scope_sql})
          AND {last_follow_expr} <= (now() - INTERVAL '3 days')
          AND {ev_in_scope}
        """,
    )

    # 6) Auto-close: confirmados/declinados (por si queda algo abierto)
    try:
        db.execute(
            text(
                """
                UPDATE public.tasks t
                SET status='done', completed_at=now(), completed_by='AUTO', updated_at=now(),
                    meta = COALESCE(t.meta,'{}'::jsonb) || jsonb_build_object('auto_closed', true, 'auto_reason', 'lead_confirmed_or_declined', 'auto_at', now())
                WHERE t.assigned_user_id=:uid AND t.status='open' AND t.entity_type='lead'
                  AND EXISTS (
                    SELECT 1 FROM public.leads l WHERE l.id_lead=t.entity_id AND l.id_estado = ANY(CAST(:closed AS int[]))
                  )
                """
            ),
            {"uid": int(user_id), "closed": [int(confirmado_id), int(declinado_id)]},
        )
    except Exception:
        pass

    # 6b) Auto-close: cobros que ya no aplican (saldo pagado / fuera de mes / lead no confirmado / no asignado)
    try:
        if _table_exists(db, "fin_eventos") and _col_exists(db, "fin_eventos", "id_evento"):
            db.execute(
                text(
                    f"""
                    UPDATE public.tasks t
                    SET status='done', completed_at=now(), completed_by='AUTO', updated_at=now(),
                        meta = COALESCE(t.meta,'{{}}'::jsonb) || jsonb_build_object('auto_closed', true, 'auto_reason', 'cobro_no_longer_needed', 'auto_at', now())
                    WHERE t.assigned_user_id=:uid
                      AND t.status='open'
                      AND t.entity_type='fin_evento'
                      AND t.kind='COBRO_PENDIENTE'
                      AND NOT EXISTS (
                        SELECT 1
                        FROM public.fin_eventos fe
                        JOIN public.leads l ON l.id_lead = fe.id_lead
                        WHERE fe.id_evento = t.entity_id
                          AND fe.fecha_evento IS NOT NULL
                          AND fe.fecha_evento >= {month_start} AND fe.fecha_evento < {month_end}
                          AND COALESCE(fe.abono,0) > 0
                          AND COALESCE(fe.saldo,0) > 0
                          AND l.id_estado = :conf
                          AND (:is_admin OR {assigned_sql})
                      )
                    """
                ),
                {
                    "uid": int(user_id),
                    "conf": int(confirmado_id),
                    "is_admin": bool(is_admin),
                    "user_keys": user_keys,
                },
            )
    except Exception:
        pass

    # 7) Summary (coherente con list_tasks: excluye leads cerrados)
    summary: Dict[str, Any] = {"ok": True, "counts": {}, "overdue": {}}
    try:
        row = db.execute(
            text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE t.status='open')::int AS open_total,
                  COUNT(*) FILTER (WHERE t.status='open' AND t.due_at IS NOT NULL AND t.due_at < now())::int AS overdue_total,
                  COUNT(*) FILTER (WHERE t.status='open' AND t.kind='LEAD_NUEVO_SEGUIMIENTO')::int AS open_nuevos,
                  COUNT(*) FILTER (WHERE t.status='open' AND t.kind='LEAD_CONTACTADO_SEGUIMIENTO')::int AS open_contactados,
                  COUNT(*) FILTER (WHERE t.status='open' AND t.kind='LEAD_COTIZADO_SEGUIMIENTO')::int AS open_cotizados,
                  COUNT(*) FILTER (WHERE t.status='open' AND t.kind='COBRO_PENDIENTE')::int AS open_cobros
                FROM public.tasks t
                WHERE t.assigned_user_id = :uid
                  AND NOT (
                    t.entity_type='lead'
                    AND EXISTS (
                      SELECT 1 FROM public.leads l2
                      WHERE l2.id_lead = t.entity_id AND l2.id_estado = ANY(CAST(:closed AS int[]))
                    )
                  )
                """
            ),
            {"uid": int(user_id), "closed": [int(confirmado_id), int(declinado_id)]},
        ).mappings().first()
        if row:
            summary["counts"] = {
                "open_total": int(row.get("open_total") or 0),
                "open_nuevos": int(row.get("open_nuevos") or 0),
                "open_contactados": int(row.get("open_contactados") or 0),
                "open_cotizados": int(row.get("open_cotizados") or 0),
                "open_cobros": int(row.get("open_cobros") or 0),
                # compat (UI vieja)
                "open_contactar": int(row.get("open_nuevos") or 0),
                "open_calendario": 0,
                "open_correos": 0,
            }
            summary["overdue"] = {
                "overdue_total": int(row.get("overdue_total") or 0),
                "overdue_contactar": 0,
                "overdue_calendario": 0,
                "overdue_correos": 0,
            }
    except Exception:
        pass
    return summary


def list_tasks(
    db: Session,
    *,
    assigned_user_id: int,
    status: str = TASK_STATUS_OPEN,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    ensure_tasks_table(db)
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    if not _tasks_table_exists(db):
        return {"ok": True, "items": [], "limit": limit, "offset": offset, "disabled": True}
    status = (status or TASK_STATUS_OPEN).strip().lower()
    if status not in (TASK_STATUS_OPEN, TASK_STATUS_DONE, TASK_STATUS_SKIPPED, "all"):
        status = TASK_STATUS_OPEN

    where = "t.assigned_user_id = :uid"
    params: Dict[str, Any] = {"uid": int(assigned_user_id), "lim": limit, "off": offset}
    if status != "all":
        where += " AND t.status = :st"
        params["st"] = status

    # IMPORTANTE: list_tasks es un GET. No debe escribir en BD.
    # Aquí SOLO filtramos (ocultamos) tareas que ya no son relevantes:
    # - Leads fuera de los 3 estados (NUEVO/CONTACTADO/COTIZADO)
    # - Leads CONFIRMADOS/DECLINADOS
    # - Leads inexistentes o borrados (evita "Lead no existe" al abrir seguimiento)
    # - Leads con fecha_evento vencida
    # - Leads con fecha_evento fuera del mes actual
    # - Leads sin fecha_evento, pero creados fuera del mes actual (evita tareas eternas)
    try:
        if _table_exists(db, "leads") and _col_exists(db, "leads", "id_estado"):
            confirmado_id = _estado_id_like(db, "CONFIRM%", 4)
            declinado_id = _estado_id_like(db, "%DECLIN%", 5)
            closed_estados = [int(confirmado_id), int(declinado_id)]
            params["closed_estados"] = closed_estados

            where += """
              AND NOT (
                t.entity_type = 'lead'
                AND EXISTS (
                  SELECT 1
                  FROM public.leads l2
                  WHERE l2.id_lead = t.entity_id
                    AND l2.id_estado = ANY(CAST(:closed_estados AS int[]))
                )
              )
            """

            # Ocultar tareas cuyo lead no existe (o fue eliminado físicamente).
            where += """
              AND NOT (
                t.entity_type='lead'
                AND NOT EXISTS (
                  SELECT 1
                  FROM public.leads lx
                  WHERE lx.id_lead = t.entity_id
                )
              )
            """

            # Ocultar tareas de leads borrados lógicamente (si existe la columna).
            try:
                if _col_exists(db, "leads", "is_deleted"):
                    where += """
                      AND NOT (
                        t.entity_type='lead'
                        AND EXISTS (
                          SELECT 1
                          FROM public.leads ld
                          WHERE ld.id_lead = t.entity_id
                            AND COALESCE(ld.is_deleted,false) = true
                        )
                      )
                    """
            except Exception:
                pass

            # Además: por regla de negocio, las tareas del módulo solo aplican a 3 estados
            # (NUEVO / CONTACTADO / COTIZADO). Si el lead cambió a otro estado y quedó una
            # tarea abierta "colgada", la ocultamos (el sync la cierra de forma persistente).
            try:
                allowed_estados = [
                    int(_estado_id_like(db, "%NUEV%", 1)),
                    int(_estado_id_like(db, "%CONTACT%", 2)),
                    int(_estado_id_like(db, "%COTIZ%", 3)),
                ]
                # De-dup defensivo
                allowed_estados = [x for i, x in enumerate(allowed_estados) if x and x not in allowed_estados[:i]]
                params["allowed_estados"] = allowed_estados
                where += """
                  AND NOT (
                    t.entity_type='lead'
                    AND EXISTS (
                      SELECT 1
                      FROM public.leads l0
                      WHERE l0.id_lead = t.entity_id
                        AND l0.id_estado IS NOT NULL
                        AND NOT (l0.id_estado = ANY(CAST(:allowed_estados AS int[])))
                    )
                  )
                """
            except Exception:
                pass
    except Exception:
        pass

    # Además: por definición del módulo, las tareas son SOLO para leads "vivos" del mes actual.
    # Si la fecha de evento ya pasó, ese lead debe auto-declinarse (sync) y NO debe mostrarse aquí.
    try:
        if _table_exists(db, "leads") and _col_exists(db, "leads", "fecha_evento"):
            # Chile timezone: evita desfaces en borde de mes por timezone del server.
            today = "(now() AT TIME ZONE 'America/Santiago')::date"

            where += """
              AND NOT (
                t.entity_type='lead'
                AND EXISTS (
                  SELECT 1
                  FROM public.leads l3
                  WHERE l3.id_lead = t.entity_id
                    AND (%(ev)s) IS NOT NULL
                    AND (%(ev)s) < %(today)s
                )
              )
            """ % {"ev": _lead_event_date_expr_db(db, alias="l3"), "today": today}

            # Además: tareas solo para el mes actual (evita que aparezcan leads del próximo mes).
            month_start = f"date_trunc('month', {today})::date"
            month_end = f"(date_trunc('month', {today}) + INTERVAL '1 month')::date"

            where += """
              AND NOT (
                t.entity_type='lead'
                AND EXISTS (
                  SELECT 1
                  FROM public.leads l4
                  WHERE l4.id_lead = t.entity_id
                    AND (%(ev)s) IS NOT NULL
                    AND (%(ev)s) >= %(month_end)s
                )
              )
            """ % {"ev": _lead_event_date_expr_db(db, alias="l4"), "month_end": month_end}

            # Extra defensivo: también excluimos fechas anteriores al mes actual (aunque no estén vencidas por timezone / parse raro).
            where += """
              AND NOT (
                t.entity_type='lead'
                AND EXISTS (
                  SELECT 1
                  FROM public.leads l6
                  WHERE l6.id_lead = t.entity_id
                    AND (%(ev)s) IS NOT NULL
                    AND (%(ev)s) < %(month_start)s
                )
              )
            """ % {"ev": _lead_event_date_expr_db(db, alias="l6"), "month_start": month_start}

            # Leads sin fecha_evento: se mantienen visibles si cumplen regla (no aplicamos filtro por mes aquí).
    except Exception:
        pass

    # Nunca 500: si hay diferencias de esquema (columnas/tablas), degradar a lista simple.
    try:
        has_leads = _table_exists(db, "leads")
        has_marcas = _table_exists(db, "marcas")
        has_estados = _table_exists(db, "estados_lead")
        has_fin_eventos = _table_exists(db, "fin_eventos")

        joins = ""

        lead_cliente_base = "'—'"
        lead_marca_base = "''"
        lead_estado_base = "''"
        lead_tel_base = "''"
        lead_fecha_base = "NULL::date"

        if has_leads:
            joins += " LEFT JOIN public.leads l ON (t.entity_type='lead' AND t.entity_id=l.id_lead) "
            lead_cliente_base = _lead_name_expr_db(db)
            if _col_exists(db, "leads", "telefono"):
                lead_tel_base = "COALESCE(l.telefono,'')"
            if _col_exists(db, "leads", "fecha_evento"):
                lead_fecha_base = _lead_event_date_expr_db(db, alias="l")

            # Marca (por ID o texto)
            if has_marcas and _col_exists(db, "leads", "id_marca") and _col_exists(db, "marcas", "id_marca"):
                joins += " LEFT JOIN public.marcas m ON m.id_marca = l.id_marca "
                if _col_exists(db, "marcas", "nombre") and _col_exists(db, "marcas", "marca"):
                    lead_marca_base = "COALESCE(m.nombre, m.marca, '')"
                elif _col_exists(db, "marcas", "nombre"):
                    lead_marca_base = "COALESCE(m.nombre, '')"
                elif _col_exists(db, "marcas", "marca"):
                    lead_marca_base = "COALESCE(m.marca, '')"
            elif _col_exists(db, "leads", "marca"):
                lead_marca_base = "COALESCE(l.marca,'')"

            if has_estados and _col_exists(db, "leads", "id_estado") and _col_exists(db, "estados_lead", "id_estado"):
                joins += " LEFT JOIN public.estados_lead e ON e.id_estado = l.id_estado "
                if _col_exists(db, "estados_lead", "nombre"):
                    lead_estado_base = "COALESCE(e.nombre, '')"

        # Finanzas (cobros pendientes): entity_type='fin_evento'
        if has_fin_eventos:
            joins += " LEFT JOIN public.fin_eventos fe ON (t.entity_type='fin_evento' AND t.entity_id=fe.id_evento) "
            lead_cliente_base = f"COALESCE(NULLIF(btrim({lead_cliente_base}),''), NULLIF(btrim(COALESCE(fe.cliente,'')),''), '—')"
            lead_marca_base = f"COALESCE(NULLIF(btrim({lead_marca_base}),''), NULLIF(btrim(COALESCE(fe.marca,'')),''), '')"
            lead_fecha_base = f"COALESCE({lead_fecha_base}, fe.fecha_evento)"
            lead_estado_base = f"(CASE WHEN t.entity_type='fin_evento' THEN 'CONFIRMADO' ELSE {lead_estado_base} END)"

        lead_cliente_expr = f"{lead_cliente_base} AS lead_cliente"
        lead_marca_expr = f"{lead_marca_base} AS lead_marca"
        lead_estado_expr = f"{lead_estado_base} AS lead_estado"
        lead_tel_expr = f"{lead_tel_base} AS lead_telefono"
        lead_fecha_expr = f"{lead_fecha_base} AS lead_fecha_evento"

        q = f"""
          SELECT t.id_task, t.created_at, t.updated_at, t.status, t.priority,
                 t.kind, t.title, t.description, t.entity_type, t.entity_id,
                 t.due_at, t.completed_at, t.completed_by,
                 t.meta,
                 {lead_cliente_expr},
                 {lead_marca_expr},
                 {lead_estado_expr},
                 {lead_tel_expr},
                 {lead_fecha_expr}
          FROM public.tasks t
          {joins}
          WHERE {where}
          ORDER BY
            (CASE WHEN t.status='open' AND t.due_at IS NOT NULL AND t.due_at < now() THEN 0 ELSE 1 END) ASC,
            t.priority ASC,
            t.due_at ASC NULLS LAST,
            t.id_task DESC
          LIMIT :lim OFFSET :off
        """
        items = [dict(r) for r in db.execute(text(q), params).mappings().all()]
        return {"ok": True, "items": items, "limit": limit, "offset": offset}
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        # Fallback ultra-seguro: tareas sin joins
        try:
            q2 = f"""
              SELECT t.id_task, t.created_at, t.updated_at, t.status, t.priority,
                     t.kind, t.title, t.description, t.entity_type, t.entity_id,
                     t.due_at, t.completed_at, t.completed_by, t.meta
              FROM public.tasks t
              WHERE {where}
              ORDER BY
                (CASE WHEN t.status='open' AND t.due_at IS NOT NULL AND t.due_at < now() THEN 0 ELSE 1 END) ASC,
                t.priority ASC,
                t.due_at ASC NULLS LAST,
                t.id_task DESC
              LIMIT :lim OFFSET :off
            """
            items = [dict(r) for r in db.execute(text(q2), params).mappings().all()]
            return {"ok": True, "items": items, "limit": limit, "offset": offset, "degraded": True, "error": str(e)[:160]}
        except Exception:
            return {"ok": True, "items": [], "limit": limit, "offset": offset, "disabled": True, "error": str(e)[:160]}


def complete_task(db: Session, *, id_task: int, completed_by: str) -> Dict[str, Any]:
    ensure_tasks_table(db)
    if not _tasks_table_exists(db):
        return {"ok": False, "disabled": True}
    db.execute(
        text(
            """
            UPDATE public.tasks
            SET status='done', completed_at=now(), completed_by=:by, updated_at=now()
            WHERE id_task=:id
            """
        ),
        {"id": int(id_task), "by": (completed_by or "").strip()[:200]},
    )
    return {"ok": True}


def skip_task(db: Session, *, id_task: int, skipped_by: str, reason: str) -> Dict[str, Any]:
    ensure_tasks_table(db)
    if not _tasks_table_exists(db):
        return {"ok": False, "disabled": True}
    db.execute(
        text(
            """
            UPDATE public.tasks
            SET status='skipped', completed_at=now(), completed_by=:by,
                updated_at=now(),
                meta = jsonb_set(COALESCE(meta,'{}'::jsonb), '{skip_reason}', to_jsonb(CAST(:r AS text)), true)
            WHERE id_task=:id
            """
        ),
        {
            "id": int(id_task),
            "by": (skipped_by or "").strip()[:200],
            "r": (reason or "").strip()[:240],
        },
    )
    return {"ok": True}
