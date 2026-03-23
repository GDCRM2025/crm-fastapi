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
    """Idempotente: crea tabla + índices mínimos."""
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


def _is_admin_role(role: str) -> bool:
    r = (role or "").strip().upper()
    return r in ("ADMIN", "SUPERADMIN")


def _lead_name_expr() -> str:
    # nombre_cliente puede variar; en este proyecto normalmente es nombre_cliente
    return "COALESCE(NULLIF(btrim(l.nombre_cliente),''), NULLIF(btrim(l.cliente),''), '—')"


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


def _has_contact_sql() -> str:
    # MVP: consideramos contacto si en notas existe WSP/CALL/EMAIL
    return "(COALESCE(l.notas,'') ILIKE '%[WSP]%' OR COALESCE(l.notas,'') ILIKE '%[CALL]%' OR COALESCE(l.notas,'') ILIKE '%[EMAIL]%')"


def upsert_mvp_tasks_for_user(db: Session, *, user_id: int, username: str, role: str) -> Dict[str, Any]:
    """Genera tareas mínimas (idempotente) y devuelve un resumen.

    MVP (Ventas/Admin):
    - CONTACTAR: leads NUEVO asignados al usuario sin contacto registrado.
    - COMPLETAR_DATOS: confirmados sin teléfono/dirección/horario (si aplica).
    - REVISAR_DECLINADO_FUTURO: (Admin) declinados con fecha_evento futura.
    """
    ensure_tasks_table(db)
    is_admin = _is_admin_role(role)

    nuevo_id = _estado_id_like(db, "%NUEV%", 1)
    confirmado_id = _estado_id_like(db, "CONFIRM%", 4)
    declinado_id = _estado_id_like(db, "%DECLIN%", 5)
    contactado_id = _estado_id_like(db, "%CONTACT%", 2)
    cotizado_id = _estado_id_like(db, "%COTIZ%", 3)

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
                  AND COALESCE(l.id_usuario, 0) = :uid
                  AND ({_has_contact_sql()}) IS FALSE
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": int(user_id), "uname": (username or "").strip()[:200], "nuevo": int(nuevo_id)},
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
                  AND COALESCE(l.id_usuario, 0) = :uid
                  AND ({_has_contact_sql()}) IS FALSE
                  AND COALESCE(l.created_at, now()) <= (now() - INTERVAL '5 days')
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": int(user_id), "uname": (username or "").strip()[:200], "nuevo": int(nuevo_id)},
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
                  AND (:is_admin OR COALESCE(l.id_usuario,0)=:uid)
                  AND COALESCE(l.updated_at, l.created_at, now()) <= (now() - INTERVAL '3 days')
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": int(user_id), "uname": (username or "").strip()[:200], "contactado": int(contactado_id), "is_admin": bool(is_admin)},
        )
    except Exception:
        pass

    try:
        db.execute(
            text(
                """
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
                  AND (COALESCE(NULLIF(btrim(l.notas),''), NULL) IS NOT NULL)
                  AND (:is_admin OR COALESCE(l.id_usuario,0)=:uid)
                  AND COALESCE(l.updated_at, l.created_at, now()) <= (now() - INTERVAL '5 days')
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": int(user_id), "uname": (username or "").strip()[:200], "contactado": int(contactado_id), "is_admin": bool(is_admin)},
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
                  AND (:is_admin OR COALESCE(l.id_usuario,0)=:uid)
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": int(user_id), "uname": (username or "").strip()[:200], "cotizado": int(cotizado_id), "is_admin": bool(is_admin)},
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
                  AND (:is_admin OR COALESCE(l.id_usuario,0)=:uid)
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": int(user_id), "uname": (username or "").strip()[:200], "conf": int(confirmado_id), "is_admin": bool(is_admin)},
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
                  AND (:is_admin OR COALESCE(l.id_usuario,0)=:uid)
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": int(user_id), "uname": (username or "").strip()[:200], "conf": int(confirmado_id), "is_admin": bool(is_admin)},
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
                  AND (:is_admin OR COALESCE(l.id_usuario,0)=:uid)
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": int(user_id), "uname": (username or "").strip()[:200], "conf": int(confirmado_id), "is_admin": bool(is_admin)},
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
    status = (status or TASK_STATUS_OPEN).strip().lower()
    if status not in (TASK_STATUS_OPEN, TASK_STATUS_DONE, TASK_STATUS_SKIPPED, "all"):
        status = TASK_STATUS_OPEN

    where = "t.assigned_user_id = :uid"
    params: Dict[str, Any] = {"uid": int(assigned_user_id), "lim": limit, "off": offset}
    if status != "all":
        where += " AND t.status = :st"
        params["st"] = status

    q = f"""
      SELECT t.id_task, t.created_at, t.updated_at, t.status, t.priority,
             t.kind, t.title, t.description, t.entity_type, t.entity_id,
             t.due_at, t.completed_at, t.completed_by,
             {_lead_name_expr()} AS lead_cliente,
             COALESCE(m.nombre, m.marca, '') AS lead_marca,
             COALESCE(e.nombre, '') AS lead_estado,
             l.telefono AS lead_telefono,
             l.fecha_evento AS lead_fecha_evento
      FROM public.tasks t
      LEFT JOIN public.leads l ON (t.entity_type='lead' AND t.entity_id=l.id_lead)
      LEFT JOIN public.marcas m ON m.id_marca = l.id_marca
      LEFT JOIN public.estados_lead e ON e.id_estado = l.id_estado
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


def complete_task(db: Session, *, id_task: int, completed_by: str) -> Dict[str, Any]:
    ensure_tasks_table(db)
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
