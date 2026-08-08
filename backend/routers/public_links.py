from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend.core.public_tokens import verify

from sqlalchemy import text

from backend.core.db import get_connection


router = APIRouter(prefix="/public", tags=["public"])

def _cols(conn, table: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t
            """
        ),
        {"t": table},
    ).fetchall()
    return {str(r[0]) for r in rows if r and r[0]}


@router.get("/vcard/{token}")
def public_vcard(token: str):
    """
    Descarga pública de vCard (link compartible).
    Útil para abrir desde el teléfono sin JWT.

    Token firmado incluye: {"t":"vcard","id_lead":123}
    """
    ok, payload, err = verify(token)
    if not ok or not payload:
        raise HTTPException(status_code=401, detail=err or "Token inválido")

    if str(payload.get("t") or "").lower() != "vcard":
        raise HTTPException(status_code=401, detail="Token inválido")
    raw_id = payload.get("id_lead")
    try:
        id_lead = int(raw_id)
    except Exception:
        raise HTTPException(status_code=401, detail="Token inválido")

    with get_connection() as conn:
        marca_cols = _cols(conn, "marcas")
        comuna_cols = _cols(conn, "comunas")
        marca_expr = "COALESCE(m.marca,'')"
        if "nombre" in marca_cols:
            marca_expr = "COALESCE(m.marca, m.nombre, '')"
        comuna_expr = "COALESCE(c.nombre,'')"
        if "nombre" not in comuna_cols and "comuna" in comuna_cols:
            comuna_expr = "COALESCE(c.comuna,'')"
        elif "nombre" not in comuna_cols and "comuna" not in comuna_cols:
            comuna_expr = "''"

        row = conn.execute(
            text(
                f"""
                SELECT
                  l.id_lead,
                  l.cliente AS nombre_cliente,
                  l.email,
                  l.telefono,
                  {marca_expr} AS marca_nombre,
                  {comuna_expr} AS comuna_nombre
                FROM public.leads l
                LEFT JOIN public.marcas m ON m.id_marca=l.id_marca
                LEFT JOIN public.comunas c ON c.id_comuna=l.id_comuna
                WHERE l.id_lead=:id AND COALESCE(l.is_deleted,false)=false
                """
            ),
            {"id": int(id_lead)},
        ).mappings().first()
        if not row:
            raise HTTPException(status_code=404, detail="Lead no existe")
        lead = dict(row)

    def esc(s: str) -> str:
        s = (s or "").replace("\r", "").replace("\n", "\\n")
        s = s.replace(";", "\\;").replace(",", "\\,")
        return s

    name = str(lead.get("cliente") or lead.get("nombre_cliente") or "").strip() or f"Lead {id_lead}"
    email = str(lead.get("email") or "").strip()
    tel = str(lead.get("telefono") or "").strip()
    org = str(lead.get("marca_nombre") or lead.get("marca") or "").strip()
    comuna = str(lead.get("comuna_nombre") or lead.get("comuna") or "").strip()
    note_bits = []
    if comuna:
        note_bits.append(f"Comuna: {comuna}")
    if org:
        note_bits.append(f"Marca: {org}")
    note = " · ".join(note_bits)

    lines = [
        "BEGIN:VCARD",
        "VERSION:3.0",
        f"FN:{esc(name)}",
    ]
    parts = [p for p in name.split(" ") if p.strip()]
    if parts:
        first = parts[0]
        last = " ".join(parts[1:]) if len(parts) > 1 else ""
        lines.append(f"N:{esc(last)};{esc(first)};;;")
    if org:
        lines.append(f"ORG:{esc(org)}")
    if tel:
        lines.append(f"TEL;TYPE=CELL:{esc(tel)}")
    if email:
        lines.append(f"EMAIL;TYPE=INTERNET:{esc(email)}")
    if note:
        lines.append(f"NOTE:{esc(note)}")
    lines.append("END:VCARD")
    body = "\r\n".join(lines) + "\r\n"

    filename = f"contacto_lead_{id_lead}.vcf"
    return Response(
        content=body,
        media_type="text/vcard; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
    )
