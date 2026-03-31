from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
import json
import datetime

from backend.db import get_db

router = APIRouter(prefix="/rrhh", tags=["rrhh"])

NOMINA_FIELDS = [
    "colaborador",
    "centro_costo",
    "situacion_contractual",
    "sueldo_fijo",
    "fecha_inicio",
    "hh_liquido",
    "hh_diario",
    "dias_trabajados",
    "a_pagar_liquido",
    "fijo_bruto",
    "comisiones_brutas",
    "pct_imposicion",
    "comision_neta",
    "comision_no_imponible",
    "adelantos",
    "total_pagar",
    "fecha_vencimiento",
    "estado",
    "fecha_regularizacion",
    "antiguedad",
    "dias_generados",
    "dias_tomados",
    "sobrante",
    "observaciones",
]


def _ensure_tables(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_nomina (
              id_nomina SERIAL PRIMARY KEY,
              colaborador TEXT,
              centro_costo TEXT,
              situacion_contractual TEXT,
              sueldo_fijo NUMERIC,
              fecha_inicio DATE,
              hh_liquido NUMERIC,
              hh_diario NUMERIC,
              dias_trabajados NUMERIC,
              a_pagar_liquido NUMERIC,
              fijo_bruto NUMERIC,
              comisiones_brutas NUMERIC,
              pct_imposicion NUMERIC,
              comision_neta NUMERIC,
              comision_no_imponible NUMERIC,
              adelantos NUMERIC,
              total_pagar NUMERIC,
              fecha_vencimiento DATE,
              estado TEXT,
              fecha_regularizacion DATE,
              antiguedad TEXT,
              dias_generados NUMERIC,
              dias_tomados NUMERIC,
              sobrante NUMERIC,
              observaciones TEXT,
              raw JSONB
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_adelantos (
              id_adelanto SERIAL PRIMARY KEY,
              colaborador TEXT NOT NULL,
              monto NUMERIC NOT NULL,
              fecha DATE DEFAULT CURRENT_DATE,
              motivo TEXT
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_staff (
              id_staff SERIAL PRIMARY KEY,
              colaborador TEXT NOT NULL,
              rut TEXT,
              email TEXT,
              telefono TEXT,
              rol TEXT,
              centro_costo TEXT,
              fecha_ingreso DATE,
              afp TEXT,
              afp_pct NUMERIC,
              salud_tipo TEXT,
              salud_pct NUMERIC,
              is_active BOOLEAN DEFAULT TRUE,
              observaciones TEXT,
              ficha JSONB
            )
            """
        )
    )
    # Ensure new columns exist on older installs
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS observaciones TEXT"))
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS ficha JSONB"))
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS presencial_dow SMALLINT"))
    db.execute(text("ALTER TABLE rrhh_staff ADD COLUMN IF NOT EXISTS modalidad_default TEXT"))
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_inasistencias (
              id_inasistencia SERIAL PRIMARY KEY,
              colaborador TEXT NOT NULL,
              fecha DATE NOT NULL,
              dias NUMERIC DEFAULT 1,
              motivo TEXT
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_vacaciones (
              id_vacacion SERIAL PRIMARY KEY,
              colaborador TEXT NOT NULL,
              fecha_inicio DATE NOT NULL,
              fecha_fin DATE NOT NULL,
              dias NUMERIC,
              observaciones TEXT
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_solicitudes (
              id_solicitud SERIAL PRIMARY KEY,
              colaborador TEXT NOT NULL,
              tipo TEXT NOT NULL, -- adelanto / vacaciones / permiso
              fecha_inicio DATE,
              fecha_fin DATE,
              dias NUMERIC,
              monto NUMERIC,
              motivo TEXT,
              estado TEXT DEFAULT 'pendiente',
              created_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_afp (
              id_afp SERIAL PRIMARY KEY,
              nombre TEXT UNIQUE NOT NULL,
              pct_comision NUMERIC,
              is_active BOOLEAN DEFAULT TRUE,
              updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rrhh_salud (
              id_salud SERIAL PRIMARY KEY,
              tipo TEXT NOT NULL, -- FONASA / ISAPRE
              nombre TEXT UNIQUE NOT NULL,
              pct_base NUMERIC,
              is_active BOOLEAN DEFAULT TRUE,
              updated_at TIMESTAMP DEFAULT now()
            )
            """
        )
    )
    db.commit()

    # Seed AFP commissions if empty
    try:
        cnt = db.execute(text("SELECT COUNT(*) FROM rrhh_afp")).scalar_one()
        if int(cnt or 0) == 0:
            data = [
                ("Capital", 1.44),
                ("Cuprum", 1.44),
                ("Habitat", 1.27),
                ("Modelo", 0.58),
                ("PlanVital", 1.16),
                ("Provida", 1.45),
                ("Uno", 0.46),
            ]
            for n, p in data:
                db.execute(
                    text("INSERT INTO rrhh_afp(nombre, pct_comision) VALUES (:n,:p)"),
                    {"n": n, "p": p},
                )
            db.commit()
    except Exception:
        db.rollback()

    # Seed salud (FONASA base 7%, ISAPRE base 7% editable)
    try:
        cnt = db.execute(text("SELECT COUNT(*) FROM rrhh_salud")).scalar_one()
        if int(cnt or 0) == 0:
            data = [
                ("FONASA", "FONASA", 7.0),
                ("ISAPRE", "ISAPRE (base)", 7.0),
            ]
            for t, n, p in data:
                db.execute(
                    text("INSERT INTO rrhh_salud(tipo, nombre, pct_base) VALUES (:t,:n,:p)"),
                    {"t": t, "n": n, "p": p},
                )
            db.commit()
    except Exception:
        db.rollback()


@router.get("/nomina")
def nomina_list(db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    try:
        # Base: colaboradores activos (rrhh_staff)
        staff_rows = db.execute(
            text(
                """
                SELECT id_staff, colaborador, centro_costo, rol, afp, afp_pct, salud_tipo, salud_pct,
                       fecha_ingreso, observaciones, ficha
                FROM rrhh_staff
                WHERE is_active IS TRUE
                ORDER BY colaborador ASC NULLS LAST, id_staff ASC
                """
            )
        ).mappings().all()

        # adelantos por colaborador
        adel_rows = db.execute(
            text(
                """
                SELECT lower(colaborador) AS c, COALESCE(SUM(monto),0) AS total
                FROM rrhh_adelantos
                GROUP BY lower(colaborador)
                """
            )
        ).fetchall()
        adel_map = {str(r[0]): float(r[1] or 0) for r in adel_rows}

        # inasistencias del mes actual
        faltas_map = {}
        try:
            faltas_rows = db.execute(
                text(
                    """
                    SELECT lower(colaborador) AS c, COALESCE(SUM(dias),0) AS dias
                    FROM rrhh_inasistencias
                    WHERE fecha >= date_trunc('month', current_date)
                      AND fecha < (date_trunc('month', current_date) + interval '1 month')
                    GROUP BY lower(colaborador)
                    """
                )
            ).fetchall()
            faltas_map = {str(r[0]): float(r[1] or 0) for r in faltas_rows}
        except Exception:
            faltas_map = {}

        # vacaciones acumuladas/tomadas
        vac_map = {}
        try:
            vac_rows = db.execute(
                text(
                    """
                    SELECT lower(colaborador) AS c, COALESCE(SUM(dias),0) AS dias
                    FROM rrhh_vacaciones
                    GROUP BY lower(colaborador)
                    """
                )
            ).fetchall()
            vac_map = {str(r[0]): float(r[1] or 0) for r in vac_rows}
        except Exception:
            vac_map = {}

        items = []
        def _num(v):
            if v is None:
                return 0.0
            if isinstance(v, (int, float)):
                return float(v)
            try:
                return float(str(v).replace(".", "").replace(",", "."))
            except Exception:
                return 0.0

        for r in staff_rows:
            d = dict(r)
            ficha = d.get("ficha") or {}
            if isinstance(ficha, str):
                try:
                    ficha = json.loads(ficha)
                except Exception:
                    ficha = {}
            key = str((d.get("colaborador") or "")).lower()
            faltas = float(faltas_map.get(key, 0) or 0)
            sueldo = _num(ficha.get("renta_liquida") or ficha.get("sueldo_fijo") or 0)
            adel = float(adel_map.get(key, 0) or 0)
            vac_tomadas = float(vac_map.get(key, 0) or 0)
            fecha_ingreso = d.get("fecha_ingreso")
            vac_acumuladas = 0.0
            if fecha_ingreso:
                try:
                    if isinstance(fecha_ingreso, str):
                        fecha_ingreso = datetime.date.fromisoformat(fecha_ingreso)
                    days = (datetime.date.today() - fecha_ingreso).days
                    vac_acumuladas = max(0.0, (days / 30.0) * 1.25)
                except Exception:
                    vac_acumuladas = 0.0
            vac_disponibles = max(0.0, vac_acumuladas - vac_tomadas)
            dias = max(0, 30 - faltas)
            diario = (sueldo / 30.0) if sueldo else 0.0
            descuento_faltas = diario * faltas
            total_pagar = max(0.0, sueldo - descuento_faltas - adel)
            items.append({
                "colaborador": d.get("colaborador"),
                "centro_costo": d.get("centro_costo"),
                "cargo": ficha.get("cargo") or d.get("rol"),
                "sueldo_fijo": sueldo,
                "afp": d.get("afp"),
                "afp_pct": d.get("afp_pct"),
                "salud_tipo": d.get("salud_tipo"),
                "salud_pct": d.get("salud_pct"),
                "faltas_mes": faltas,
                "dias_trabajados": dias,
                "adelantos_total": adel,
                "vac_acumuladas": round(vac_acumuladas, 2),
                "vac_tomadas": round(vac_tomadas, 2),
                "vac_disponibles": round(vac_disponibles, 2),
                "total_pagar": round(total_pagar, 0),
                "saldo": round(total_pagar, 0),
            })
        return {"ok": True, "items": items}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.get("/nomina/fields")
def nomina_fields() -> dict[str, Any]:
    return {"ok": True, "fields": NOMINA_FIELDS}


@router.post("/nomina")
def nomina_create(body: dict, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    data = {k: body.get(k) for k in NOMINA_FIELDS if k in body}
    if not data.get("colaborador"):
        return {"ok": False, "detail": "colaborador requerido"}
    cols = ", ".join(data.keys())
    vals = ", ".join([f":{k}" for k in data.keys()])
    q = text(f"INSERT INTO rrhh_nomina({cols}) VALUES ({vals}) RETURNING id_nomina")
    rid = db.execute(q, data).scalar_one()
    db.commit()
    return {"ok": True, "id_nomina": rid}


@router.put("/nomina/{id_nomina}")
def nomina_update(id_nomina: int, body: dict, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    data = {k: body.get(k) for k in NOMINA_FIELDS if k in body}
    if not data:
        return {"ok": False, "detail": "sin cambios"}
    sets = ", ".join([f"{k}=:{k}" for k in data.keys()])
    data["id_nomina"] = id_nomina
    db.execute(text(f"UPDATE rrhh_nomina SET {sets} WHERE id_nomina=:id_nomina"), data)
    db.commit()
    return {"ok": True}


@router.delete("/nomina/{id_nomina}")
def nomina_delete(id_nomina: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    db.execute(text("DELETE FROM rrhh_nomina WHERE id_nomina=:id"), {"id": id_nomina})
    db.commit()
    return {"ok": True}


@router.post("/nomina/clear")
def nomina_clear(db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    db.execute(text("DELETE FROM rrhh_nomina"))
    db.commit()
    return {"ok": True}


@router.get("/adelantos")
def adelantos(colaborador: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    params = {}
    where = ""
    if colaborador:
        where = "WHERE colaborador ILIKE :c"
        params["c"] = f"%{colaborador}%"
    rows = db.execute(
        text(
            f"""
            SELECT id_adelanto, colaborador, monto, fecha, motivo
            FROM rrhh_adelantos
            {where}
            ORDER BY fecha DESC, id_adelanto DESC
            """
        ),
        params,
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.post("/adelantos")
def adelanto_create(body: dict, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    colaborador = (body.get("colaborador") or "").strip()
    monto = body.get("monto")
    fecha = body.get("fecha")
    motivo = (body.get("motivo") or "").strip()
    if not colaborador:
        return {"ok": False, "detail": "colaborador requerido"}
    if monto is None:
        return {"ok": False, "detail": "monto requerido"}
    db.execute(
        text(
            """
            INSERT INTO rrhh_adelantos(colaborador, monto, fecha, motivo)
            VALUES (:c, :m, COALESCE(:f, CURRENT_DATE), :mot)
            """
        ),
        {"c": colaborador, "m": monto, "f": fecha, "mot": motivo},
    )
    db.commit()
    return {"ok": True}


@router.get("/adelantos")
def adelanto_list(colaborador: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    try:
        if colaborador:
            rows = db.execute(
                text(
                    """
                    SELECT id_adelanto, colaborador, monto, fecha, motivo
                    FROM rrhh_adelantos
                    WHERE lower(colaborador) = lower(:c)
                    ORDER BY fecha DESC, id_adelanto DESC
                    """
                ),
                {"c": colaborador},
            ).mappings().all()
        else:
            rows = db.execute(
                text(
                    """
                    SELECT id_adelanto, colaborador, monto, fecha, motivo
                    FROM rrhh_adelantos
                    ORDER BY fecha DESC, id_adelanto DESC
                    """
                )
            ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.put("/adelantos/{id_adelanto}")
def adelanto_update(id_adelanto: int, body: dict, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    try:
        db.execute(
            text(
                """
                UPDATE rrhh_adelantos
                SET fecha = COALESCE(:fecha, fecha),
                    monto = COALESCE(:monto, monto),
                    motivo = COALESCE(:motivo, motivo)
                WHERE id_adelanto = :id
                """
            ),
            {
                "id": id_adelanto,
                "fecha": body.get("fecha"),
                "monto": body.get("monto"),
                "motivo": body.get("motivo"),
            },
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.delete("/adelantos/{id_adelanto}")
def adelanto_delete(id_adelanto: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    db.execute(text("DELETE FROM rrhh_adelantos WHERE id_adelanto=:id"), {"id": id_adelanto})
    db.commit()
    return {"ok": True}


@router.get("/staff")
def staff_list(db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    try:
        rows = db.execute(
            text(
                """
                SELECT *
                FROM rrhh_staff
                ORDER BY colaborador ASC NULLS LAST, id_staff ASC
                """
            )
        ).mappings().all()
        items = []
        for r in rows:
            d = dict(r)
            ficha = d.get("ficha")
            if isinstance(ficha, str):
                try:
                    d["ficha"] = json.loads(ficha)
                except Exception:
                    d["ficha"] = {}
            items.append(d)
        return {"ok": True, "items": items}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.get("/staff/{id_staff}/ficha")
def staff_ficha(id_staff: int, db: Session = Depends(get_db)) -> HTMLResponse:
    _ensure_tables(db)
    row = db.execute(
        text(
            """
            SELECT id_staff, colaborador, rut, email, telefono, rol, centro_costo, fecha_ingreso,
                   afp, afp_pct, salud_tipo, salud_pct, observaciones, ficha
            FROM rrhh_staff
            WHERE id_staff = :id
            """
        ),
        {"id": id_staff},
    ).mappings().first()
    if not row:
        return HTMLResponse("<h3>No se encontró colaborador.</h3>", status_code=404)
    d = dict(row)
    ficha = d.get("ficha") or {}
    if isinstance(ficha, str):
        try:
            ficha = json.loads(ficha)
        except Exception:
            ficha = {}

    def v(key, fallback="—"):
        val = ficha.get(key)
        if val is None or val == "":
            return fallback
        return str(val)

    html = f"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <title>Ficha ingreso · {d.get("colaborador") or ""}</title>
  <style>
    body{{ font-family: Arial, sans-serif; margin:24px; color:#111; }}
    .head{{ display:flex; justify-content:space-between; align-items:center; }}
    .btn{{ padding:8px 12px; border:1px solid #111; background:#111; color:#fff; border-radius:8px; cursor:pointer; }}
    h1{{ font-size:20px; margin:0 0 12px 0; }}
    h2{{ font-size:14px; margin:16px 0 6px 0; text-transform:uppercase; letter-spacing:.08em; }}
    table{{ width:100%; border-collapse:collapse; font-size:12px; }}
    td{{ padding:6px 8px; border:1px solid #ddd; }}
    .muted{{ color:#666; }}
  </style>
</head>
<body>
  <div class="head">
    <div>
      <h1>Ficha de Ingreso</h1>
      <div class="muted">{d.get("colaborador") or "—"} · {d.get("rut") or "—"}</div>
    </div>
    <button class="btn" onclick="window.print()">Imprimir PDF</button>
  </div>

  <h2>Antecedentes Cargo</h2>
  <table>
    <tr><td>Área</td><td>{v("area")}</td><td>Cargo</td><td>{v("cargo")}</td></tr>
    <tr><td>Renta líquida</td><td>{v("renta_liquida")}</td><td>Entrevistador</td><td>{v("entrevistador")}</td></tr>
    <tr><td>Fecha entrevista</td><td>{v("fecha_entrevista")}</td><td>Fecha ingreso</td><td>{d.get("fecha_ingreso") or "—"}</td></tr>
  </table>

  <h2>Antecedentes Personales</h2>
  <table>
    <tr><td>Nombres</td><td>{v("nombres")}</td><td>Apellidos</td><td>{v("apellido_paterno")} {v("apellido_materno")}</td></tr>
    <tr><td>Fecha nac.</td><td>{v("fecha_nacimiento")}</td><td>Nacionalidad</td><td>{v("nacionalidad")}</td></tr>
    <tr><td>Género</td><td>{v("genero")}</td><td>Estado civil</td><td>{v("estado_civil")}</td></tr>
    <tr><td>Teléfono</td><td>{d.get("telefono") or "—"}</td><td>Email</td><td>{d.get("email") or "—"}</td></tr>
    <tr><td>Dirección</td><td colspan="3">{v("calle")} {v("numero")} {v("depto")} {v("direccion_extra")}, {v("comuna")}, {v("ciudad")}, {v("pais")}</td></tr>
    <tr><td>Emergencia</td><td>{v("emergencia_contacto")}</td><td>Tel. emergencia</td><td>{v("emergencia_telefono")}</td></tr>
  </table>

  <h2>Antecedentes Familiares</h2>
  <table>
    <tr><td>Parentesco</td><td>{v("fam_parentesco")}</td><td>Nombre</td><td>{v("fam_nombre")}</td></tr>
    <tr><td>RUT</td><td>{v("fam_rut")}</td><td>Fecha nac.</td><td>{v("fam_nacimiento")}</td></tr>
    <tr><td>Carga</td><td>{v("fam_carga")}</td><td>Actividad</td><td>{v("fam_actividad")}</td></tr>
  </table>

  <h2>Tallas</h2>
  <table>
    <tr><td>Pantalón</td><td>{v("talla_pantalon")}</td><td>Chaqueta</td><td>{v("talla_chaqueta")}</td></tr>
    <tr><td>Polera</td><td>{v("talla_polera")}</td><td>Calzado</td><td>{v("talla_calzado")}</td></tr>
    <tr><td>Guantes</td><td>{v("talla_guantes")}</td><td></td><td></td></tr>
  </table>

  <h2>Afiliaciones</h2>
  <table>
    <tr><td>AFP</td><td>{d.get("afp") or "—"} ({d.get("afp_pct") or "—"}%)</td><td>Salud</td><td>{d.get("salud_tipo") or "—"} ({d.get("salud_pct") or "—"}%)</td></tr>
  </table>

  <h2>Datos Bancarios</h2>
  <table>
    <tr><td>Banco</td><td>{v("banco")}</td><td>Tipo cuenta</td><td>{v("tipo_cuenta")}</td></tr>
    <tr><td>N° cuenta</td><td>{v("cuenta_numero")}</td><td></td><td></td></tr>
  </table>
</body>
</html>
"""
    return HTMLResponse(html)


@router.get("/afp")
def afp_list(db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    rows = db.execute(
        text("SELECT id_afp, nombre, pct_comision FROM rrhh_afp WHERE is_active IS TRUE ORDER BY nombre")
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.get("/salud")
def salud_list(db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    rows = db.execute(
        text("SELECT id_salud, tipo, nombre, pct_base FROM rrhh_salud WHERE is_active IS TRUE ORDER BY tipo, nombre")
    ).mappings().all()
    return {"ok": True, "items": [dict(r) for r in rows]}


@router.post("/staff")
def staff_create(body: dict, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    colaborador = (body.get("colaborador") or "").strip()
    if not colaborador:
        return {"ok": False, "detail": "colaborador requerido"}
    ficha = body.get("ficha")
    if isinstance(ficha, (dict, list)):
        ficha = json.dumps(ficha, ensure_ascii=False)
    data = {
        "colaborador": colaborador,
        "rut": body.get("rut"),
        "email": body.get("email"),
        "telefono": body.get("telefono"),
        "rol": body.get("rol"),
        "centro_costo": body.get("centro_costo"),
        "fecha_ingreso": body.get("fecha_ingreso"),
        "afp": body.get("afp"),
        "afp_pct": body.get("afp_pct"),
        "salud_tipo": body.get("salud_tipo"),
        "salud_pct": body.get("salud_pct"),
        "is_active": body.get("is_active", True),
        "observaciones": body.get("observaciones"),
        "ficha": ficha,
    }
    try:
        db.execute(
            text(
                """
                INSERT INTO rrhh_staff(
                  colaborador,rut,email,telefono,rol,centro_costo,fecha_ingreso,
                  afp,afp_pct,salud_tipo,salud_pct,is_active,observaciones,ficha
                ) VALUES (
                  :colaborador,:rut,:email,:telefono,:rol,:centro_costo,:fecha_ingreso,
                  :afp,:afp_pct,:salud_tipo,:salud_pct,:is_active,:observaciones,CAST(:ficha AS JSONB)
                )
                RETURNING id_staff
                """
            ),
            data,
        ).scalar_one()
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.put("/staff/{id_staff}")
def staff_update(id_staff: int, body: dict, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    ficha = body.get("ficha")
    if isinstance(ficha, (dict, list)):
        ficha = json.dumps(ficha, ensure_ascii=False)
    data = {
        "id_staff": id_staff,
        "colaborador": body.get("colaborador"),
        "rut": body.get("rut"),
        "email": body.get("email"),
        "telefono": body.get("telefono"),
        "rol": body.get("rol"),
        "centro_costo": body.get("centro_costo"),
        "fecha_ingreso": body.get("fecha_ingreso"),
        "afp": body.get("afp"),
        "afp_pct": body.get("afp_pct"),
        "salud_tipo": body.get("salud_tipo"),
        "salud_pct": body.get("salud_pct"),
        "is_active": body.get("is_active", True),
        "observaciones": body.get("observaciones"),
        "ficha": ficha,
    }
    try:
        db.execute(
            text(
                """
                UPDATE rrhh_staff
                SET colaborador=:colaborador,
                    rut=:rut,
                    email=:email,
                    telefono=:telefono,
                    rol=:rol,
                    centro_costo=:centro_costo,
                    fecha_ingreso=:fecha_ingreso,
                    afp=:afp,
                    afp_pct=:afp_pct,
                    salud_tipo=:salud_tipo,
                    salud_pct=:salud_pct,
                    is_active=:is_active,
                    observaciones=:observaciones,
                    ficha=CAST(:ficha AS JSONB)
                WHERE id_staff=:id_staff
                """
            ),
            data,
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.delete("/staff/{id_staff}")
def staff_delete(id_staff: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    db.execute(text("DELETE FROM rrhh_staff WHERE id_staff=:id"), {"id": id_staff})
    db.commit()
    return {"ok": True}


@router.post("/inasistencias")
def inasistencia_create(body: dict, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    colaborador = (body.get("colaborador") or "").strip()
    fecha = body.get("fecha")
    dias = body.get("dias") or 1
    motivo = (body.get("motivo") or "").strip()
    if not colaborador or not fecha:
        return {"ok": False, "detail": "colaborador y fecha requeridos"}
    db.execute(
        text(
            """
            INSERT INTO rrhh_inasistencias(colaborador, fecha, dias, motivo)
            VALUES (:c, :f, :d, :m)
            """
        ),
        {"c": colaborador, "f": fecha, "d": dias, "m": motivo},
    )
    db.commit()
    return {"ok": True}


@router.get("/inasistencias")
def inasistencia_list(colaborador: str | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    try:
        if colaborador:
            rows = db.execute(
                text(
                    """
                    SELECT id_inasistencia, colaborador, fecha, dias, motivo
                    FROM rrhh_inasistencias
                    WHERE lower(colaborador) = lower(:c)
                    ORDER BY fecha DESC, id_inasistencia DESC
                    """
                ),
                {"c": colaborador},
            ).mappings().all()
        else:
            rows = db.execute(
                text(
                    """
                    SELECT id_inasistencia, colaborador, fecha, dias, motivo
                    FROM rrhh_inasistencias
                    ORDER BY fecha DESC, id_inasistencia DESC
                    """
                )
            ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.put("/inasistencias/{id_inasistencia}")
def inasistencia_update(id_inasistencia: int, body: dict, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    try:
        db.execute(
            text(
                """
                UPDATE rrhh_inasistencias
                SET fecha = COALESCE(:fecha, fecha),
                    dias = COALESCE(:dias, dias),
                    motivo = COALESCE(:motivo, motivo)
                WHERE id_inasistencia = :id
                """
            ),
            {
                "id": id_inasistencia,
                "fecha": body.get("fecha"),
                "dias": body.get("dias"),
                "motivo": body.get("motivo"),
            },
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.delete("/inasistencias/{id_inasistencia}")
def inasistencia_delete(id_inasistencia: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    db.execute(text("DELETE FROM rrhh_inasistencias WHERE id_inasistencia=:id"), {"id": id_inasistencia})
    db.commit()
    return {"ok": True}


@router.get("/solicitudes")
def solicitudes_list(
    estado: str | None = None,
    colaborador: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _ensure_tables(db)
    try:
        where = []
        params: dict[str, Any] = {}
        if estado:
            where.append("estado = :estado")
            params["estado"] = estado
        if colaborador:
            where.append("lower(colaborador) = lower(:colaborador)")
            params["colaborador"] = colaborador
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = db.execute(
            text(
                f"""
                SELECT id_solicitud, colaborador, tipo, fecha_inicio, fecha_fin, dias, monto, motivo, estado, created_at
                FROM rrhh_solicitudes
                {where_sql}
                ORDER BY created_at DESC, id_solicitud DESC
                """
            ),
            params,
        ).mappings().all()
        return {"ok": True, "items": [dict(r) for r in rows]}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.post("/solicitudes")
def solicitudes_create(body: dict, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    colaborador = (body.get("colaborador") or "").strip()
    tipo = (body.get("tipo") or "").strip()
    if not colaborador or not tipo:
        return {"ok": False, "detail": "colaborador y tipo requeridos"}
    db.execute(
        text(
            """
            INSERT INTO rrhh_solicitudes(
              colaborador, tipo, fecha_inicio, fecha_fin, dias, monto, motivo, estado
            ) VALUES (
              :c, :t, :fi, :ff, :d, :m, :mot, :e
            )
            """
        ),
        {
            "c": colaborador,
            "t": tipo,
            "fi": body.get("fecha_inicio"),
            "ff": body.get("fecha_fin"),
            "d": body.get("dias"),
            "m": body.get("monto"),
            "mot": body.get("motivo"),
            "e": body.get("estado") or "pendiente",
        },
    )
    db.commit()
    return {"ok": True}


@router.put("/solicitudes/{id_solicitud}")
def solicitudes_update(id_solicitud: int, body: dict, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    try:
        db.execute(
            text(
                """
                UPDATE rrhh_solicitudes
                SET estado = COALESCE(:estado, estado),
                    motivo = COALESCE(:motivo, motivo)
                WHERE id_solicitud = :id
                """
            ),
            {"id": id_solicitud, "estado": body.get("estado"), "motivo": body.get("motivo")},
        )
        db.commit()
        return {"ok": True}
    except Exception as e:
        db.rollback()
        return {"ok": False, "detail": str(e)}


@router.get("/solicitudes/{id_solicitud}/pdf")
def solicitud_pdf(id_solicitud: int, db: Session = Depends(get_db)) -> HTMLResponse:
    _ensure_tables(db)
    row = db.execute(
        text(
            """
            SELECT id_solicitud, colaborador, tipo, fecha_inicio, fecha_fin, dias, monto, motivo, estado, created_at
            FROM rrhh_solicitudes
            WHERE id_solicitud = :id
            """
        ),
        {"id": id_solicitud},
    ).mappings().first()
    if not row:
        return HTMLResponse("<h3>No se encontró solicitud.</h3>", status_code=404)
    d = dict(row)
    html = f"""
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <title>Solicitud RRHH · {d.get("tipo")}</title>
  <style>
    body{{ font-family: Arial, sans-serif; margin:24px; color:#111; }}
    .head{{ display:flex; justify-content:space-between; align-items:center; }}
    .btn{{ padding:8px 12px; border:1px solid #111; background:#111; color:#fff; border-radius:8px; cursor:pointer; }}
    h1{{ font-size:18px; margin:0 0 12px 0; }}
    table{{ width:100%; border-collapse:collapse; font-size:12px; }}
    td{{ padding:6px 8px; border:1px solid #ddd; }}
  </style>
</head>
<body>
  <div class="head">
    <h1>Solicitud {d.get("tipo")}</h1>
    <button class="btn" onclick="window.print()">Imprimir PDF</button>
  </div>
  <table>
    <tr><td>Colaborador</td><td>{d.get("colaborador")}</td></tr>
    <tr><td>Tipo</td><td>{d.get("tipo")}</td></tr>
    <tr><td>Fecha inicio</td><td>{d.get("fecha_inicio") or "—"}</td></tr>
    <tr><td>Fecha fin</td><td>{d.get("fecha_fin") or "—"}</td></tr>
    <tr><td>Días</td><td>{d.get("dias") or "—"}</td></tr>
    <tr><td>Monto</td><td>{d.get("monto") or "—"}</td></tr>
    <tr><td>Motivo</td><td>{d.get("motivo") or "—"}</td></tr>
    <tr><td>Estado</td><td>{d.get("estado")}</td></tr>
    <tr><td>Fecha creación</td><td>{d.get("created_at")}</td></tr>
  </table>
</body>
</html>
"""
    return HTMLResponse(html)


@router.post("/vacaciones")
def vacaciones_create(body: dict, db: Session = Depends(get_db)) -> dict[str, Any]:
    _ensure_tables(db)
    colaborador = (body.get("colaborador") or "").strip()
    fi = body.get("fecha_inicio")
    ff = body.get("fecha_fin")
    dias = body.get("dias")
    obs = (body.get("observaciones") or "").strip()
    if not colaborador or not fi or not ff:
        return {"ok": False, "detail": "colaborador y fechas requeridas"}
    db.execute(
        text(
            """
            INSERT INTO rrhh_vacaciones(colaborador, fecha_inicio, fecha_fin, dias, observaciones)
            VALUES (:c, :fi, :ff, :d, :o)
            """
        ),
        {"c": colaborador, "fi": fi, "ff": ff, "d": dias, "o": obs},
    )
    db.commit()
    return {"ok": True}
