from datetime import date
import re
from sqlalchemy import text, select, func
from sqlalchemy.orm import Session

# --- Normalizadores y validadores ---

def initials_from_name(nombre: str) -> str:
    partes = [p for p in re.split(r"\s+", nombre.strip()) if p]
    if not partes:
        return "XX"
    # hasta 3 iniciales
    ini = "".join(p[0] for p in partes[:3]).upper()
    return ini

def only_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")

def valid_email(email: str) -> bool:
    if not email:
        return True  # permitir vacío
    return bool(re.match(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", email.strip(), re.I))

def chile_phone_e164(phone: str) -> str:
    # deja solo dígitos y asegura 56 + 9 dígitos para móviles (o 56 + 8-9 fallback)
    d = only_digits(phone)
    if d.startswith("56"):
        core = d[2:]
    elif d.startswith("0"):
        core = d.lstrip("0")
    else:
        core = d
    if len(core) == 9:
        return "56" + core
    if len(core) == 8:  # líneas fijas
        return "56" + core
    # si viene con 11/12 dígitos, intenta usar tal cual si empieza por 56
    if d.startswith("56") and len(d) in (11, 12):
        return d
    # fallback: devuelve solo dígitos
    return d

def whatsapp_deeplink(phone_e164: str, nombre: str = "") -> str:
    if not phone_e164:
        return ""
    # usar esquema app (no web)
    from urllib.parse import quote
    msg = quote(f"Hola {nombre}".strip())
    return f"whatsapp://send?phone={phone_e164}&text={msg}"

# --- Cálculos de fecha_evento -> dia/mes/semana/anio ---

def derive_date_parts(fecha_evento):
    if not fecha_evento:
        return None, None, None, None
    d = fecha_evento
    dia = d.strftime("%d")
    mes = d.strftime("%m")
    semana = int(d.strftime("%V"))
    anio = d.year
    return dia, mes, semana, anio

# --- Generación SEGURA de codigo_cliente con advisory lock por prefijo ---

def safe_generate_codigo_cliente(db: Session, marca_cod3: str, nombre_cliente: str) -> str:
    """
    Formato: {MAR}-{INI}-{NN}
    - MAR: 3 primeras letras de la marca (upper)
    - INI: iniciales del nombre (hasta 3) (upper)
    - NN : consecutivo de 2 dígitos por prefijo MAR-INI, calculado en transacción
    Usa pg_advisory_xact_lock para serializar por-hash del prefijo y evitar carreras.
    """
    ini = initials_from_name(nombre_cliente)
    mar = (marca_cod3 or "XXX")[:3].upper()
    prefix = f"{mar}-{ini}-"

    # lock por prefijo (hash a BIGINT estable)
    lock_sql = text("SELECT pg_advisory_xact_lock( hashtextextended(:k, 0) )")
    db.execute(lock_sql, {"k": prefix})

    # busca máximo NN existente para ese prefijo
    max_sql = text("""
        SELECT COALESCE(MAX( right(codigo_cliente, 2)::int ), 0) AS maxn
        FROM leads
        WHERE codigo_cliente LIKE :pref
    """)
    maxn = db.execute(max_sql, {"pref": prefix + "%"}).scalar() or 0
    nxt = maxn + 1
    code = f"{prefix}{nxt:02d}"
    return code
