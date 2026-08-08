from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend" / "routers" / "whatsapp_commercial.py"
GREENIE = ROOT / "web" / "views" / "tools_whatsapp_greenie.html"
LEGACY = ROOT / "web" / "views" / "tools_whatsapp.html"


def replace_function(source: str, start: str, end: str, replacement: str) -> str:
    begin = source.find(start)
    if begin < 0:
        raise SystemExit(f"No se encontro inicio: {start}")
    finish = source.find(end, begin)
    if finish < 0:
        raise SystemExit(f"No se encontro fin: {end}")
    return source[:begin] + replacement.rstrip() + "\n\n\n" + source[finish:]


def patch_backend() -> None:
    source = BACKEND.read_text(encoding="utf-8")

    lead_rows = r'''def _lead_rows(db: Session, wa_id: str) -> list[dict[str, Any]]:
    if not _table_exists(db, "leads"):
        return []

    lead_cols = _cols(db, "leads")
    marca_exists = _table_exists(db, "marcas")
    estado_exists = _table_exists(db, "estados_lead")
    comuna_exists = _table_exists(db, "comunas")

    marca_cols = _cols(db, "marcas") if marca_exists else set()
    estado_cols = _cols(db, "estados_lead") if estado_exists else set()
    comuna_cols = _cols(db, "comunas") if comuna_exists else set()

    marca_expr = (
        "m.marca" if "marca" in marca_cols
        else "m.nombre" if "nombre" in marca_cols
        else "m.nombre_marca" if "nombre_marca" in marca_cols
        else "NULL::text"
    )
    estado_expr = (
        "es.nombre" if "nombre" in estado_cols
        else "es.estado" if "estado" in estado_cols
        else "es.nombre_estado" if "nombre_estado" in estado_cols
        else "NULL::text"
    )
    estado_color_expr = "es.color" if "color" in estado_cols else "'#64748b'::text"
    comuna_expr = (
        "co.nombre" if "nombre" in comuna_cols
        else "co.comuna" if "comuna" in comuna_cols
        else "co.nombre_comuna" if "nombre_comuna" in comuna_cols
        else "NULL::text"
    )

    marca_join = "LEFT JOIN public.marcas m ON m.id_marca=l.id_marca" if marca_exists and "id_marca" in lead_cols and "id_marca" in marca_cols else ""
    estado_join = "LEFT JOIN public.estados_lead es ON es.id_estado=l.id_estado" if estado_exists and "id_estado" in lead_cols and "id_estado" in estado_cols else ""
    comuna_join = "LEFT JOIN public.comunas co ON co.id_comuna=l.id_comuna" if comuna_exists and "id_comuna" in lead_cols and "id_comuna" in comuna_cols else ""

    if not marca_join:
        marca_expr = "NULL::text"
    if not estado_join:
        estado_expr = "NULL::text"
        estado_color_expr = "'#64748b'::text"
    if not comuna_join:
        comuna_expr = "NULL::text"

    is_deleted = "AND COALESCE(l.is_deleted,FALSE)=FALSE" if "is_deleted" in lead_cols else ""
    created = _sql_col("l", lead_cols, "created_at", "NULL::timestamptz")
    updated = _sql_col("l", lead_cols, "updated_at", created)
    phone9 = _phone9(wa_id)
    if not phone9:
        return []

    phone_col = next((name for name in ("telefono", "phone", "celular") if name in lead_cols), None)
    if not phone_col:
        return []

    select = f"""
        l.id_lead,
        {_sql_col('l', lead_cols, 'cliente', "''::text")} AS cliente,
        {_sql_col('l', lead_cols, 'email', "NULL::text")} AS email,
        {_sql_col('l', lead_cols, phone_col, "NULL::text")} AS telefono,
        {_sql_col('l', lead_cols, 'direccion', "NULL::text")} AS direccion,
        {_sql_col('l', lead_cols, 'id_marca', 'NULL::bigint')} AS id_marca,
        {_sql_col('l', lead_cols, 'id_estado', 'NULL::bigint')} AS id_estado,
        {_sql_col('l', lead_cols, 'id_comuna', 'NULL::bigint')} AS id_comuna,
        {_sql_col('l', lead_cols, 'fecha_evento', 'NULL::date')} AS fecha_evento,
        {_sql_col('l', lead_cols, 'monto_cotizado', '0::numeric')} AS monto_cotizado,
        {_sql_col('l', lead_cols, 'plataforma', "NULL::text")} AS plataforma,
        {_sql_col('l', lead_cols, 'notas', "NULL::text")} AS notas,
        {_sql_col('l', lead_cols, 'num_cotizacion', "NULL::text")} AS num_cotizacion,
        {_sql_col('l', lead_cols, 'cotizacion_pdf_url', "NULL::text")} AS cotizacion_pdf_url,
        {_sql_col('l', lead_cols, 'seguimiento_at', 'NULL::timestamptz')} AS seguimiento_at,
        {_sql_col('l', lead_cols, 'calendar_start', 'NULL::timestamptz')} AS calendar_start,
        {_sql_col('l', lead_cols, 'calendar_end', 'NULL::timestamptz')} AS calendar_end,
        {_sql_col('l', lead_cols, 'calendar_html_link', "NULL::text")} AS calendar_html_link,
        {created} AS created_at,
        {updated} AS updated_at,
        COALESCE({marca_expr}, '') AS marca,
        COALESCE({estado_expr}, '') AS estado,
        COALESCE({estado_color_expr}, '#64748b') AS estado_color,
        COALESCE({comuna_expr}, '') AS comuna
    """

    order_status = f"UPPER(COALESCE({estado_expr},''))" if estado_join else "''"
    rows = db.execute(text(f"""
        SELECT {select}
        FROM public.leads l
        {marca_join}
        {estado_join}
        {comuna_join}
        WHERE RIGHT(regexp_replace(COALESCE(l.{phone_col}::text,''), '[^0-9]', '', 'g'), 9)=:phone9
          {is_deleted}
        ORDER BY
          CASE WHEN {order_status} LIKE '%CONFIRM%' THEN 1
               WHEN {order_status} LIKE '%DECLIN%' THEN 2
               ELSE 0 END,
          {_sql_col('l', lead_cols, 'fecha_evento', 'NULL::date')} DESC NULLS LAST,
          {created} DESC NULLS LAST,
          l.id_lead DESC
        LIMIT 100
    """), {"phone9": phone9}).mappings().all()
    return [dict(row) for row in rows]
'''

    statuses = r'''def _statuses(db: Session) -> list[dict[str, Any]]:
    if not _table_exists(db, "estados_lead"):
        return []
    cols = _cols(db, "estados_lead")
    id_col = next((name for name in ("id_estado", "id") if name in cols), None)
    name_col = next((name for name in ("nombre", "estado", "nombre_estado") if name in cols), None)
    if not id_col or not name_col:
        return []
    color_expr = "color" if "color" in cols else "'#64748b'::text"
    rows = db.execute(text(f"""
        SELECT {id_col} AS id_estado,
               {name_col} AS nombre,
               COALESCE({color_expr}, '#64748b') AS color
        FROM public.estados_lead
        ORDER BY {id_col}
    """)).mappings().all()
    return [dict(row) for row in rows]
'''

    source = replace_function(source, "def _lead_rows", "def _quote_rows", lead_rows)
    source = replace_function(source, "def _statuses", "def _executives", statuses)

    old = '''    leads = _lead_rows(db, str(conversation["wa_id"]))
    lead_ids = [int(row["id_lead"]) for row in leads]
    quotes = _quote_rows(db, lead_ids)
'''
    new = '''    try:
        leads = _lead_rows(db, str(conversation["wa_id"]))
    except Exception:
        db.rollback()
        raise
    lead_ids = [int(row["id_lead"]) for row in leads]
    try:
        quotes = _quote_rows(db, lead_ids)
    except Exception:
        db.rollback()
        quotes = []
'''
    if old not in source:
        raise SystemExit("No se encontro bloque principal commercial_360")
    source = source.replace(old, new, 1)

    old = '''        "estados": _statuses(db),
        "executives": _executives(db) if (conversation.get("is_dispatch") or conversation.get("assignment_mode") == "manual") else [],
'''
    new = '''        "estados": _safe_statuses(db),
        "executives": _safe_executives(db, conversation),
'''
    if old not in source:
        raise SystemExit("No se encontro bloque estados/executives")
    source = source.replace(old, new, 1)

    helper_marker = '@router.get("/conversations/{conversation_id}/commercial-360")'
    helpers = r'''def _safe_statuses(db: Session) -> list[dict[str, Any]]:
    try:
        return _statuses(db)
    except Exception:
        db.rollback()
        return []


def _safe_executives(db: Session, conversation: dict[str, Any]) -> list[dict[str, Any]]:
    if not (conversation.get("is_dispatch") or conversation.get("assignment_mode") == "manual"):
        return []
    try:
        return _executives(db)
    except Exception:
        db.rollback()
        return []


'''
    if helpers not in source:
        source = source.replace(helper_marker, helpers + helper_marker, 1)

    BACKEND.write_text(source, encoding="utf-8")
    print("OK backend Comercial 360")


def patch_greenie(path: Path) -> None:
    source = path.read_text(encoding="utf-8")

    old_let = "let items=[],current=null,messages=[],commercial=null,status='open',replyTarget=null,pollBusy=false,lastSignature='',audioReady=false,lastUnread=new Map(),mediaUrls=[];"
    new_let = "let items=[],current=null,messages=[],commercial=null,status='open',replyTarget=null,pollBusy=false,lastSignature='',lastListSignature='',lastCommercialSignature='',audioReady=false,lastUnread=new Map(),mediaUrls=[];"
    if old_let in source:
        source = source.replace(old_let, new_let, 1)
    elif new_let not in source:
        raise SystemExit(f"No se encontro declaracion de estado en {path}")

    source = re.sub(
        r"async function loadList\(\)\{.*?\}\n  function renderList\(\)",
        """async function loadList(){const q=$('#q').value.trim();const j=await api('/gia/whatsapp/conversations?status='+encodeURIComponent(status)+(q?'&q='+encodeURIComponent(q):''));const next=j.items||[];for(const x of next){const prev=lastUnread.get(x.id)||0;if((x.unread_count||0)>prev)beep();lastUnread.set(x.id,x.unread_count||0)}const nextSignature=JSON.stringify(next.map(x=>[x.id,x.profile_name,x.wa_id,x.brand_code,x.executive_name,x.last_message,x.last_message_at,x.last_direction,x.unread_count,x.status]));items=next;if(nextSignature!==lastListSignature){lastListSignature=nextSignature;renderList()}}
  function renderList()""",
        source,
        count=1,
        flags=re.S,
    )

    source = re.sub(
        r"async function refreshOpen\(\)\{.*?\}\n  const signature=",
        """async function refreshOpen(){if(!current)return;const id=current.id,[j,c]=await Promise.all([getMessages(id),getCommercial(id)]);const sig=signature(j.items||[]);const commercialSig=JSON.stringify(c||{});current=j.conversation;if(sig!==lastSignature){messages=j.items||[];lastSignature=sig;renderMessages()}commercial=c;if(commercialSig!==lastCommercialSignature){lastCommercialSignature=commercialSig;renderSide()}await loadList()}
  const signature=""",
        source,
        count=1,
        flags=re.S,
    )

    old_open = "renderMessages();renderSide();renderList();lastSignature=signature(messages)"
    new_open = "renderMessages();renderSide();renderList();lastSignature=signature(messages);lastCommercialSignature=JSON.stringify(commercial||{});lastListSignature=JSON.stringify(items.map(x=>[x.id,x.profile_name,x.wa_id,x.brand_code,x.executive_name,x.last_message,x.last_message_at,x.last_direction,x.unread_count,x.status]))"
    if old_open in source:
        source = source.replace(old_open, new_open, 1)
    elif new_open not in source:
        raise SystemExit(f"No se encontro openConversation en {path}")

    source = source.replace("BUILD 20260727-PRECISION4", "BUILD 20260727-STABLE5")
    source = source.replace("BUILD 20260727-REAL", "BUILD 20260727-STABLE5")
    path.write_text(source, encoding="utf-8")
    print(f"OK sin parpadeo: {path}")


def main() -> None:
    patch_backend()
    patch_greenie(GREENIE)
    patch_greenie(LEGACY)
    print("GREENIE_360_STABLE5_OK")


if __name__ == "__main__":
    main()
