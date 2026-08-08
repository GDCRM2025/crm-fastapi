# backend/core/calendar_event.py
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

GCAL_TIMEZONE = "America/Santiago"
GCAL_MAIN_CALENDAR_ID = "oscarw8@gmail.com"


def _parse_hhmm(value: str) -> Optional[tuple[int, int]]:
    """
    Recibe '18:30' y devuelve (18, 30).
    Si viene vacío o mal formado, devuelve None.
    """
    if not value:
        return None
    parts = value.split(":")
    if len(parts) < 2:
        return None
    try:
        h = int(parts[0])
        m = int(parts[1])
        return h, m
    except ValueError:
        return None


def build_start_end_datetimes(
    fecha_evento_ymd: str,
    hora_inicio: str,
    hora_termino: str,
) -> tuple[Optional[datetime], Optional[datetime]]:
    """
    Combina la fecha del evento (YYYY-MM-DD) con las horas HH:MM.
    Si la hora de término es menor o igual que la de inicio, asume que cruza medianoche
    y suma un día al término.
    Si alguna hora no viene, devuelve None en ese lado.
    """
    if not fecha_evento_ymd:
        return None, None

    try:
        base_date = datetime.strptime(fecha_evento_ymd, "%Y-%m-%d").date()
    except ValueError:
        return None, None

    hm_ini = _parse_hhmm(hora_inicio)
    hm_fin = _parse_hhmm(hora_termino)

    start_dt: Optional[datetime] = None
    end_dt: Optional[datetime] = None

    if hm_ini:
        h, m = hm_ini
        start_dt = datetime(
            base_date.year, base_date.month, base_date.day, h, m, 0
        )

    if hm_fin:
        h2, m2 = hm_fin
        end_dt = datetime(
            base_date.year, base_date.month, base_date.day, h2, m2, 0
        )

    if start_dt and end_dt and end_dt <= start_dt:
        # Cruza medianoche → sumar 1 día al término
        end_dt = end_dt + timedelta(days=1)

    return start_dt, end_dt


def build_description_block(
    productos: List[Dict[str, Any]],
    montaje_sugerido: str,
    ops: Optional[int],
    telefono: Optional[str],
    direccion: Optional[str],
) -> str:
    """
    Arma el texto de descripción con el formato:

    🍔 PRODUCTOS
    • 200 HOT DOG ITALIANOS
    ...

    🛠️ MONTAJE
    • ...

    💎 OPS: X
    ☎️ TELÉFONO: +569...
    🗺️ DIRECCIÓN: ...
    """
    lines: List[str] = []

    # --- PRODUCTOS ---
    lines.append("🍔 PRODUCTOS")
    if productos:
        for item in productos:
            nombre = str(item.get("nombre") or item.get("producto") or "").strip()
            cant_raw = item.get("cantidad") or item.get("qty") or item.get("cantidad_producto")
            try:
                cant = int(round(float(cant_raw)))
            except Exception:
                cant = 0
            if not nombre:
                continue
            lines.append(f"• {cant} {nombre}")
        if not any(line.startswith("•") for line in lines[1:]):
            lines.append("• Sin productos (no hay cotización asociada).")
    else:
        lines.append("• Sin productos (no hay cotización asociada).")

    lines.append("")  # línea en blanco

    # --- MONTAJE ---
    lines.append("🛠️ MONTAJE")
    montaje_sugerido = (montaje_sugerido or "").strip()
    if montaje_sugerido:
        for raw in montaje_sugerido.splitlines():
            t = raw.strip()
            if not t:
                continue
            if t.startswith("•"):
                lines.append(t)
            else:
                lines.append(f"• {t}")
    else:
        lines.append("• Definir montaje según tipo de productos contratados.")

    lines.append("")

    # --- OPS / TEL / DIRECCIÓN ---
    ops_txt = str(ops) if ops is not None else "TBD"
    tel_txt = telefono.strip() if (telefono or "").strip() else "TBD"
    dir_txt = direccion.strip() if (direccion or "").strip() else "TBD"

    lines.append(f"💎 OPS: {ops_txt}")
    lines.append(f"☎️ TELÉFONO: {tel_txt}")
    lines.append(f"🗺️ DIRECCIÓN: {dir_txt}")

    return "\n".join(lines)


def build_summary(
    nombre_cliente: str,
    marca: str,
    *,
    hora_inicio: Optional[str],
    hora_termino: Optional[str],
    direccion: Optional[str],
) -> str:
    """
    Arma el título del evento:
    'Pedro Fritz - Camaleon (DIR TBD, HR TBD)'
    si faltan dirección u horas.
    """
    base = f"{nombre_cliente.strip()} - {marca.strip()}"

    tbd_flags: List[str] = []

    has_horas = bool((hora_inicio or "").strip() and (hora_termino or "").strip())
    if not has_horas:
        tbd_flags.append("HR TBD")

    has_dir = bool((direccion or "").strip())
    if not has_dir:
        tbd_flags.append("DIR TBD")

    if tbd_flags:
        base += " (" + ", ".join(tbd_flags) + ")"

    return base


def build_google_calendar_event(
    *,
    lead: Dict[str, Any],
    productos: List[Dict[str, Any]],
    fecha_evento: str,          # 'YYYY-MM-DD' (del lead)
    hora_inicio: str,           # 'HH:MM' desde el modal
    hora_termino: str,          # 'HH:MM' desde el modal
    comuna: str,
    direccion: Optional[str],
    telefono: Optional[str],
    ops: Optional[int],
    montaje_sugerido: str,
) -> Dict[str, Any]:
    """
    Devuelve el dict listo para mandarlo a:
      service.events().insert(calendarId=GCAL_MAIN_CALENDAR_ID, body=event)
    """

    # --- Summary ---
    nombre_cliente = str(lead.get("nombre_cliente") or "").strip()
    marca = str(lead.get("marca") or "").strip()
    summary = build_summary(
        nombre_cliente or "Evento",
        marca or "",
        hora_inicio=hora_inicio,
        hora_termino=hora_termino,
        direccion=direccion,
    )

    # --- start / end ---
    start_dt, end_dt = build_start_end_datetimes(fecha_evento, hora_inicio, hora_termino)

    start_block: Dict[str, Any]
    end_block: Dict[str, Any]

    if start_dt:
        start_block = {
            "dateTime": start_dt.isoformat(timespec="seconds"),
            "timeZone": GCAL_TIMEZONE,
        }
    else:
        # Sin hora → evento de día completo
        start_block = {"date": fecha_evento, "timeZone": GCAL_TIMEZONE}

    if end_dt:
        end_block = {
            "dateTime": end_dt.isoformat(timespec="seconds"),
            "timeZone": GCAL_TIMEZONE,
        }
    else:
        # día completo → mismo día
        end_block = {"date": fecha_evento, "timeZone": GCAL_TIMEZONE}

    # --- location ---
    location = (comuna or "").strip()

    # --- description ---
    description = build_description_block(
        productos=productos,
        montaje_sugerido=montaje_sugerido,
        ops=ops,
        telefono=telefono,
        direccion=direccion,
    )

    event: Dict[str, Any] = {
        "summary": summary,
        "start": start_block,
        "end": end_block,
        "location": location,
        "description": description,
    }

    return event
