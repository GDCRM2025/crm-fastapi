from __future__ import annotations

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
    return r in ("ADMIN", "SUPERADMIN", "1")


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
        f"{txt} ILIKE '%[EMAIL]%' OR {txt} ILIKE '%CORREO%' OR {txt} ILIKE '%MAIL%'"
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
            return "((l.id_marca IS NOT NULL AND l.id_marca = ANY(CAST(:marcas_ids AS int[]))) OR (upper(COALESCE(l.marca,'')) = ANY(CAST(:marcas_upper AS text[]))))"
        if has_id:
            return "(l.id_marca = ANY(CAST(:marcas_ids AS int[])))"
        if has_txt:
            return "(upper(COALESCE(l.marca,'')) = ANY(CAST(:marcas_upper AS text[])))"
    except Exception:
        pass
    return "FALSE"


def upsert_mvp_tasks_for_user(db: Session, *, user_id: int, username: str, role: str, marcas: list[int] | None = None) -> Dict[str, Any]:
    """Genera tareas mínimas (idempotente) y devuelve un resumen.

    MVP (Ventas/Admin):
    - CONTACTAR: leads NUEVO asignados al usuario sin contacto registrado.
    - COMPLETAR_DATOS: confirmados sin teléfono/dirección/horario (si aplica).
    - REVISAR_DECLINADO_FUTURO: (Admin) declinados con fecha_evento futura.
    """
    ensure_tasks_table(db)
    if not _tasks_table_exists(db):
        return {"ok": True, "created": 0, "skipped": 0, "disabled": True}
    is_admin = _is_admin_role(role)
    r_up = (role or "").strip().upper()
    is_finanzas = r_up in ("FINANZAS", "11")

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
                  AND {scope_sql}
                  AND ({has_contact_sql}) IS FALSE
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "nuevo": int(nuevo_id),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
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
                  AND {scope_sql}
                  AND ({has_contact_sql}) IS FALSE
                  AND COALESCE(l.created_at, now()) <= (now() - INTERVAL '5 days')
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "nuevo": int(nuevo_id),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    try:
        db.execute(
            text(
                """
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
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "contactado": int(contactado_id),
                "is_admin": bool(is_admin),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
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
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "contactado": int(contactado_id),
                "is_admin": bool(is_admin),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    try:
        db.execute(
            text(
                """
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
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "cotizado": int(cotizado_id),
                "is_admin": bool(is_admin),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    # COMPLETAR_DATOS (confirmados): faltantes (tel/dir/hr)
    # Nota: pre_start/pre_end existen en algunos deploys; los aseguramos de forma best-effort.
    try:
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS pre_start TIMESTAMPTZ"))
        db.execute(text("ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS pre_end TIMESTAMPTZ"))
    except Exception:
        pass

    try:
        # teléfono
        db.execute(
            text(
                """
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'COMPLETAR_TELEFONO' AS kind,
                  'Completar teléfono' AS title,
                  'Evento confirmado sin teléfono.' AS description,
                  'lead',
                  l.id_lead,
                  :uid,
                  :uname,
                  now() + INTERVAL '2 hours',
                  30,
                  jsonb_build_object('rule','mvp_tel')
                FROM public.leads l
                WHERE l.id_estado = :conf
                  AND (l.telefono IS NULL OR btrim(l.telefono)='')
                  AND (:is_admin OR {scope_sql})
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "conf": int(confirmado_id),
                "is_admin": bool(is_admin),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
        # dirección
        db.execute(
            text(
                """
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'COMPLETAR_DIRECCION' AS kind,
                  'Completar dirección' AS title,
                  'Evento confirmado sin dirección.' AS description,
                  'lead',
                  l.id_lead,
                  :uid,
                  :uname,
                  now() + INTERVAL '2 hours',
                  30,
                  jsonb_build_object('rule','mvp_dir')
                FROM public.leads l
                WHERE l.id_estado = :conf
                  AND (COALESCE(NULLIF(btrim(l.direccion),''), NULL) IS NULL)
                  AND (:is_admin OR {scope_sql})
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "conf": int(confirmado_id),
                "is_admin": bool(is_admin),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
        # horario (pre_start/pre_end)
        db.execute(
            text(
                """
                INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                SELECT
                  'COMPLETAR_HORARIO' AS kind,
                  'Completar horario' AS title,
                  'Evento confirmado sin horario.' AS description,
                  'lead',
                  l.id_lead,
                  :uid,
                  :uname,
                  now() + INTERVAL '2 hours',
                  30,
                  jsonb_build_object('rule','mvp_hr')
                FROM public.leads l
                WHERE l.id_estado = :conf
                  AND (l.pre_start IS NULL OR l.pre_end IS NULL)
                  AND (:is_admin OR {scope_sql})
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "uid": int(user_id),
                "uname": (username or "").strip()[:200],
                "conf": int(confirmado_id),
                "is_admin": bool(is_admin),
                "user_keys": user_keys,
                "marcas_ids": marcas_ids or [0],
                "marcas_upper": marcas_upper or ["__NONE__"],
            },
        )
    except Exception:
        pass

    # REVISAR_DECLINADO_FUTURO (Admin)
    if is_admin:
        try:
            db.execute(
                text(
                    """
                    INSERT INTO public.tasks(kind,title,description,entity_type,entity_id,assigned_user_id,assigned_username,due_at,priority,meta)
                    SELECT
                      'REVISAR_DECLINADO_FUTURO',
                      'Revisar declinado con fecha futura',
                      'Lead declinado pero fecha_evento aún no pasa: revisar seguimiento y motivo.',
                      'lead',
                      l.id_lead,
                      :uid,
                      :uname,
                      now() + INTERVAL '12 hours',
                      5,
                      jsonb_build_object('rule','mvp_declinado_futuro','fecha_evento',l.fecha_evento)
                    FROM public.leads l
                    WHERE l.id_estado = :decl
                      AND l.fecha_evento IS NOT NULL
                      AND l.fecha_evento >= CURRENT_DATE
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"uid": int(user_id), "uname": (username or "").strip()[:200], "decl": int(declinado_id)},
            )
        except Exception:
            pass

    # RESPONDER_CORREO (Ventas/Finanzas/Admin)
    # - Ventas: correos "sales" y kind lead/purchase/other (no respondidos)
    # - Finanzas/Admin: también correos "payments" o kind payment
    try:
        if _table_exists(db, "gia_email_messages"):
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
                              AND m.id_marca = ANY(CAST(:marcas_ids AS int[]))
                            )
                          )
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "uid": int(user_id),
                        "uname": (username or "").strip()[:200],
                        "is_admin": bool(is_admin),
                        "marcas_ids": marcas_ids or [0],
                    },
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
                  COUNT(*) FILTER (WHERE status='open' AND kind='CONTACTAR_LEAD' AND due_at IS NOT NULL AND due_at < now())::int AS overdue_contactar
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
            }
            summary["overdue"] = {
                "overdue_total": int(row.get("overdue_total") or 0),
                "overdue_contactar": int(row.get("overdue_contactar") or 0),
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

    # Nunca 500: si hay diferencias de esquema (columnas/tablas), degradar a lista simple.
    try:
        has_leads = _table_exists(db, "leads")
        has_marcas = _table_exists(db, "marcas")
        has_estados = _table_exists(db, "estados_lead")

        joins = ""
        lead_cliente_expr = "'—' AS lead_cliente"
        lead_marca_expr = "'' AS lead_marca"
        lead_estado_expr = "'' AS lead_estado"
        lead_tel_expr = "'' AS lead_telefono"
        lead_fecha_expr = "NULL::date AS lead_fecha_evento"

        if has_leads:
            joins += " LEFT JOIN public.leads l ON (t.entity_type='lead' AND t.entity_id=l.id_lead) "
            lead_cliente_expr = f"{_lead_name_expr_db(db)} AS lead_cliente"
            if _col_exists(db, "leads", "telefono"):
                lead_tel_expr = "COALESCE(l.telefono,'') AS lead_telefono"
            if _col_exists(db, "leads", "fecha_evento"):
                lead_fecha_expr = "l.fecha_evento AS lead_fecha_evento"

            if has_marcas and _col_exists(db, "leads", "id_marca") and _col_exists(db, "marcas", "id_marca"):
                joins += " LEFT JOIN public.marcas m ON m.id_marca = l.id_marca "
                # algunas bases usan m.nombre, otras m.marca
                if _col_exists(db, "marcas", "nombre") and _col_exists(db, "marcas", "marca"):
                    lead_marca_expr = "COALESCE(m.nombre, m.marca, '') AS lead_marca"
                elif _col_exists(db, "marcas", "nombre"):
                    lead_marca_expr = "COALESCE(m.nombre, '') AS lead_marca"
                elif _col_exists(db, "marcas", "marca"):
                    lead_marca_expr = "COALESCE(m.marca, '') AS lead_marca"

            if has_estados and _col_exists(db, "leads", "id_estado") and _col_exists(db, "estados_lead", "id_estado"):
                joins += " LEFT JOIN public.estados_lead e ON e.id_estado = l.id_estado "
                if _col_exists(db, "estados_lead", "nombre"):
                    lead_estado_expr = "COALESCE(e.nombre, '') AS lead_estado"

        q = f"""
          SELECT t.id_task, t.created_at, t.updated_at, t.status, t.priority,
                 t.kind, t.title, t.description, t.entity_type, t.entity_id,
                 t.due_at, t.completed_at, t.completed_by,
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
                     t.due_at, t.completed_at, t.completed_by
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
                meta = jsonb_set(meta, '{skip_reason}', to_jsonb(:r::text), true)
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
