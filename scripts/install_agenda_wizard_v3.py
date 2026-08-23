from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEADS = ROOT / "web" / "views" / "leads.html"
ROUTER = ROOT / "backend" / "routers" / "agenda_confirmacion.py"
TAG = '<script src="../js/agenda_wizard_v3.js?v=20260730-1"></script>'
CONTACT_MARKER = "# AGENDA_CONTACT_SYNC_V3"


def patch_leads() -> None:
    text = LEADS.read_text(encoding="utf-8")
    lines = [
        line
        for line in text.splitlines()
        if "agenda_wizard_v2.js" not in line and "agenda_wizard_v3.js" not in line
    ]
    text = "\n".join(lines) + "\n"
    if "</body>" not in text:
        raise SystemExit("ERROR: no se encontro </body> en web/views/leads.html")
    text = text.replace("</body>", f"  {TAG}\n</body>", 1)
    LEADS.write_text(text, encoding="utf-8")
    print("OK agenda_wizard_v3 include")


def patch_contact_sync() -> None:
    text = ROUTER.read_text(encoding="utf-8")
    if CONTACT_MARKER in text:
        print("OK contacto lead ya sincronizado")
        return

    anchor = '''        normalized["tbd"] = {
            "hora": any("HR" in item for item in warnings),
            "direccion": any("DIR" in item for item in warnings),
        }

        _audit_start(
'''

    replacement = '''        normalized["tbd"] = {
            "hora": any("HR" in item for item in warnings),
            "direccion": any("DIR" in item for item in warnings),
        }

        # AGENDA_CONTACT_SYNC_V3
        # Los datos editados en el wizard pasan a ser los datos vigentes del lead.
        # Se guardan antes de preparar la agenda para que Calendar, resumen y ficha
        # consuman la misma fuente y no queden telefono/direccion divergentes.
        contact_updates: dict[str, Any] = {}
        if "telefono" in normalized:
            contact_updates["telefono"] = str(normalized.get("telefono") or "").strip() or None
        if "direccion" in normalized:
            contact_updates["direccion"] = str(normalized.get("direccion") or "").strip() or None

        if contact_updates:
            assignments = ", ".join(f"{column}=:{column}" for column in contact_updates)
            params = {"id_lead": int(id_lead), **contact_updates}
            updated = db.execute(
                text(f"UPDATE public.leads SET {assignments} WHERE id_lead=:id_lead"),
                params,
            )
            if int(updated.rowcount or 0) != 1:
                db.rollback()
                raise HTTPException(status_code=404, detail="Lead no existe")
            db.commit()
            normalized["contacto_lead_actualizado"] = True

        _audit_start(
'''

    if anchor not in text:
        raise SystemExit("ERROR: no se encontro el punto de sincronizacion en agenda_confirmacion.py")

    text = text.replace(anchor, replacement, 1)
    ROUTER.write_text(text, encoding="utf-8")
    print("OK telefono/direccion se guardan en lead")


def main() -> None:
    patch_leads()
    patch_contact_sync()
    print("AGENDA_WIZARD_V3_INSTALLED")
    print(TAG)


if __name__ == "__main__":
    main()
