from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
from typing import Any

from sqlalchemy import text

from backend.core.database import engine


TZ = "America/Santiago"


def _ensure_tables(cn) -> None:
    try:
        cn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS rrhh_reminder_runs (
                  id SMALLINT PRIMARY KEY DEFAULT 1,
                  last_run_at TIMESTAMPTZ
                )
                """
            )
        )
        cn.execute(text("INSERT INTO rrhh_reminder_runs(id,last_run_at) VALUES (1,NULL) ON CONFLICT (id) DO NOTHING"))
    except Exception:
        pass

    try:
        cn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS rrhh_reminder_state (
                  id BIGSERIAL PRIMARY KEY,
                  fecha DATE NOT NULL,
                  id_staff INTEGER NOT NULL,
                  kind TEXT NOT NULL, -- IN / OUT
                  attempt INTEGER NOT NULL DEFAULT 0,
                  last_sent_at TIMESTAMPTZ,
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                  UNIQUE(fecha, id_staff, kind)
                )
                """
            )
        )
        cn.execute(text("CREATE INDEX IF NOT EXISTS ix_rrhh_reminder_state_staff ON rrhh_reminder_state(id_staff, fecha DESC)"))
    except Exception:
        pass


def _acquire_run_lock(cn, *, min_interval_seconds: int = 55) -> bool:
    """
    Best-effort throttle cross-process using a single-row table.
    Returns True if this worker should run, False otherwise.
    """
    _ensure_tables(cn)
    try:
        r = cn.execute(
            text(
                """
                UPDATE rrhh_reminder_runs
                SET last_run_at = now()
                WHERE id=1
                  AND (last_run_at IS NULL OR last_run_at < (now() - (:sec || ' seconds')::interval))
                RETURNING last_run_at
                """
            ),
            {"sec": int(min_interval_seconds)},
        ).fetchone()
        return bool(r)
    except Exception:
        return False


@dataclass
class _StaffShift:
    id_staff: int
    id_usuario: int | None
    colaborador: str
    hora_entrada: str
    hora_salida: str
    tolerancia_min: int


def _parse_hm(s: str) -> tuple[int, int] | None:
    try:
        parts = (s or "").strip().split(":")
        if len(parts) < 2:
            return None
        hh = int(parts[0])
        mm = int(parts[1])
        if hh < 0 or hh > 23 or mm < 0 or mm > 59:
            return None
        return hh, mm
    except Exception:
        return None


def _expected_datetimes(today: dt.date, he: str, hs: str) -> tuple[dt.datetime | None, dt.datetime | None]:
    ini = _parse_hm(he)
    fin = _parse_hm(hs)
    if not ini or not fin:
        return None, None
    d_in = dt.datetime(today.year, today.month, today.day, ini[0], ini[1])
    d_out = dt.datetime(today.year, today.month, today.day, fin[0], fin[1])
    if d_out <= d_in:
        d_out = d_out + dt.timedelta(days=1)
    return d_in, d_out


def run_rrhh_mark_reminders(*, dry_run: bool = False, force_run: bool = False) -> dict[str, Any]:
    """
    Motor automático:
    - ENTRADA: recordatorios a los 5, 10, 15 min desde hora_entrada si no hay IN.
    - SALIDA: recordatorios a los 5, 10, 15 min desde hora_salida si no hay OUT (solo si hubo IN).
    - Si tras 3 intentos sigue faltando, crea desvío MISSING_IN/MISSING_OUT.
    """
    now_scl = dt.datetime.now(dt.timezone.utc)  # usamos UTC y convertimos en SQL a TZ cuando aplica
    attempts = [5, 10, 15]
    sent = 0
    created_desvios = 0
    touched = 0

    with engine.begin() as cn:
        if (not force_run) and (not _acquire_run_lock(cn)):
            return {"ok": True, "skipped": True, "sent": 0, "created_desvios": 0}

        # Fecha "hoy" en Santiago (porque la tabla de marcaciones usa created_at TZ).
        today = cn.execute(text(f"SELECT (now() AT TIME ZONE '{TZ}')::date")).scalar()
        if not today:
            return {"ok": True, "sent": 0, "created_desvios": 0}
        today = dt.date.fromisoformat(str(today))
        dow = int(today.weekday())  # 0=Mon

        # Turno teórico por staff para hoy.
        # Elegimos el horario más reciente que aplique a la fecha y al día de la semana.
        rows = cn.execute(
            text(
                """
                WITH cand AS (
                  SELECT
                    s.id_staff,
                    s.id_usuario,
                    s.colaborador,
                    t.hora_entrada,
                    t.hora_salida,
                    COALESCE(t.tolerancia_min,0) AS tolerancia_min,
                    h.desde,
                    h.hasta,
                    h.dow_mask,
                    ROW_NUMBER() OVER (
                      PARTITION BY s.id_staff
                      ORDER BY h.desde DESC, h.id_horario DESC
                    ) AS rn
                  FROM rrhh_staff s
                  JOIN rrhh_horarios h ON h.id_staff=s.id_staff AND h.is_active IS TRUE
                  JOIN rrhh_turnos t ON t.id_turno=h.id_turno AND t.is_active IS TRUE
                  WHERE s.is_active IS TRUE
                    AND COALESCE(s.puede_marcar, TRUE) IS TRUE
                    AND s.id_usuario IS NOT NULL
                    AND h.desde <= :d
                    AND (h.hasta IS NULL OR h.hasta >= :d)
                    AND (h.dow_mask IS NULL OR (h.dow_mask & (1 << :dow)) <> 0)
                )
                SELECT id_staff, id_usuario, colaborador, hora_entrada, hora_salida, tolerancia_min
                FROM cand
                WHERE rn=1
                """
            ),
            {"d": today.isoformat(), "dow": dow},
        ).mappings().all()

        shifts = [
            _StaffShift(
                id_staff=int(r["id_staff"]),
                id_usuario=int(r["id_usuario"]) if r.get("id_usuario") is not None else None,
                colaborador=str(r.get("colaborador") or "").strip() or f"#{r['id_staff']}",
                hora_entrada=str(r.get("hora_entrada") or "").strip(),
                hora_salida=str(r.get("hora_salida") or "").strip(),
                tolerancia_min=int(r.get("tolerancia_min") or 0),
            )
            for r in rows
        ]
        if not shifts:
            return {"ok": True, "sent": 0, "created_desvios": 0, "no_staff": True}

        uid_list = [s.id_usuario for s in shifts if s.id_usuario]

        # Marcaciones de hoy por usuario (ok=true).
        marks = cn.execute(
            text(
                f"""
                SELECT id_usuario,
                       MAX(CASE WHEN UPPER(COALESCE(tipo,''))='IN'  THEN 1 ELSE 0 END)::int AS has_in,
                       MAX(CASE WHEN UPPER(COALESCE(tipo,''))='OUT' THEN 1 ELSE 0 END)::int AS has_out
                FROM public.sgjo_marcaciones
                WHERE ok IS TRUE
                  AND id_usuario = ANY(:uids)
                  AND ((created_at AT TIME ZONE '{TZ}')::date = :d)
                GROUP BY id_usuario
                """
            ),
            {"uids": uid_list, "d": today.isoformat()},
        ).fetchall()
        has_map: dict[int, dict[str, bool]] = {int(r[0]): {"in": bool(r[1]), "out": bool(r[2])} for r in marks}

        # Estado de intentos de hoy
        st_rows = cn.execute(
            text(
                """
                SELECT id_staff, kind, attempt, last_sent_at
                FROM rrhh_reminder_state
                WHERE fecha=:d AND id_staff = ANY(:staffs)
                """
            ),
            {"d": today.isoformat(), "staffs": [s.id_staff for s in shifts]},
        ).fetchall()
        st: dict[tuple[int, str], dict[str, Any]] = {}
        for r in st_rows:
            st[(int(r[0]), str(r[1]).upper())] = {"attempt": int(r[2] or 0), "last_sent_at": r[3]}

        # helper: upsert state
        def bump_state(id_staff: int, kind: str, attempt: int) -> None:
            cn.execute(
                text(
                    """
                    INSERT INTO rrhh_reminder_state(fecha,id_staff,kind,attempt,last_sent_at,updated_at)
                    VALUES (:d,:s,:k,:a,now(),now())
                    ON CONFLICT (fecha,id_staff,kind)
                    DO UPDATE SET attempt=EXCLUDED.attempt, last_sent_at=now(), updated_at=now()
                    """
                ),
                {"d": today.isoformat(), "s": int(id_staff), "k": kind, "a": int(attempt)},
            )

        # helper: ensure desvio
        def ensure_desvio(id_staff: int, uid: int, kind: str, exp_in: dt.datetime | None, exp_out: dt.datetime | None) -> bool:
            tipo = "MISSING_IN" if kind == "IN" else "MISSING_OUT"
            try:
                res = cn.execute(
                    text(
                        """
                        INSERT INTO rrhh_desvios(
                          fecha, id_staff, id_usuario, colaborador, tipo, status,
                          expected_in, expected_out, meta
                        )
                        SELECT
                          :d, s.id_staff, :u, s.colaborador, :tipo, 'pendiente',
                          :ein, :eout, CAST(:meta AS jsonb)
                        FROM rrhh_staff s
                        WHERE s.id_staff=:s
                        ON CONFLICT (fecha, id_staff, tipo) DO NOTHING
                        RETURNING id_desvio
                        """
                    ),
                    {
                        "d": today.isoformat(),
                        "s": int(id_staff),
                        "u": int(uid),
                        "tipo": tipo,
                        "ein": exp_in.strftime("%H:%M") if exp_in else None,
                        "eout": exp_out.strftime("%H:%M") if exp_out else None,
                        "meta": __import__("json").dumps({"rule": "auto_reminder", "attempts": 3}, ensure_ascii=False),
                    },
                ).fetchone()
                return bool(res)
            except Exception:
                return False

        # envío push (best-effort, si no hay VAPID no rompe)
        def send_push(uid: int, title: str, body: str, tag: str) -> bool:
            if dry_run:
                return True
            try:
                from backend.core.webpush import send_webpush_to_users

                send_webpush_to_users(
                    user_ids=[int(uid)],
                    title=title,
                    body=body[:180],
                    url="/crm/web/views/rrhh_portal.html?v=rrhh-reminder",
                    tag=tag,
                )
                return True
            except Exception:
                return False

        # Recorrer staff
        for sh in shifts:
            uid = sh.id_usuario
            if not uid:
                continue
            m = has_map.get(int(uid), {"in": False, "out": False})
            exp_in, exp_out = _expected_datetimes(today, sh.hora_entrada, sh.hora_salida)
            if not exp_in or not exp_out:
                continue

            # Convertimos "now" a Santiago para comparar con exp_in/out (naive).
            # Usamos SQL para evitar problemas de tz local.
            now_loc = cn.execute(text(f"SELECT (now() AT TIME ZONE '{TZ}')")).scalar()
            try:
                now_loc = dt.datetime.fromisoformat(str(now_loc))
            except Exception:
                now_loc = dt.datetime.now()

            # IN reminders
            if not m["in"]:
                cur = st.get((sh.id_staff, "IN"), {"attempt": 0}).get("attempt", 0)
                # tolerancia no cambia la regla de recordatorio; solo la hora esperada.
                for i, off in enumerate(attempts, start=1):
                    if cur >= i:
                        continue
                    if now_loc < (exp_in + dt.timedelta(minutes=off)):
                        break
                    # send attempt i
                    ok = send_push(
                        int(uid),
                        "RRHH · Recuerda marcar ENTRADA",
                        f"Tu entrada era a las {sh.hora_entrada}. Por favor marca ENTRADA (QR/GPS).",
                        tag=f"rrhh-in-{sh.id_staff}-{today.isoformat()}-{i}",
                    )
                    if ok:
                        bump_state(sh.id_staff, "IN", i)
                        sent += 1
                        touched += 1
                    break

                # falla tras 3 intentos y pasado el último offset
                cur2 = st.get((sh.id_staff, "IN"), {"attempt": 0}).get("attempt", 0)
                # si acabamos de bump_state, cur2 está viejo; no importa, evaluamos por tiempo + existencia de IN
                if now_loc >= (exp_in + dt.timedelta(minutes=attempts[-1] + 1)) and not m["in"]:
                    # Si el estado ya llegó a 3, crea desvío.
                    # (no exigimos que hayan sido enviados push, porque puede faltar VAPID; igual marca la falla)
                    created = ensure_desvio(sh.id_staff, int(uid), "IN", exp_in, exp_out)
                    if created:
                        created_desvios += 1
                continue

            # OUT reminders: solo si hubo IN y aún no hay OUT
            if m["in"] and not m["out"]:
                cur = st.get((sh.id_staff, "OUT"), {"attempt": 0}).get("attempt", 0)
                for i, off in enumerate(attempts, start=1):
                    if cur >= i:
                        continue
                    if now_loc < (exp_out + dt.timedelta(minutes=off)):
                        break
                    ok = send_push(
                        int(uid),
                        "RRHH · Recuerda marcar SALIDA",
                        f"Tu salida era a las {sh.hora_salida}. Por favor marca SALIDA (QR/GPS).",
                        tag=f"rrhh-out-{sh.id_staff}-{today.isoformat()}-{i}",
                    )
                    if ok:
                        bump_state(sh.id_staff, "OUT", i)
                        sent += 1
                        touched += 1
                    break

                if now_loc >= (exp_out + dt.timedelta(minutes=attempts[-1] + 1)) and not m["out"]:
                    created = ensure_desvio(sh.id_staff, int(uid), "OUT", exp_in, exp_out)
                    if created:
                        created_desvios += 1

        return {
            "ok": True,
            "date": today.isoformat(),
            "sent": int(sent),
            "created_desvios": int(created_desvios),
            "touched": int(touched),
            "dry_run": bool(dry_run),
        }
