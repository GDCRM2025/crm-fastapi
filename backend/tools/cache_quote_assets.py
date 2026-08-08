from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

# asegura imports del proyecto cuando se ejecuta directo
BASE = Path(__file__).resolve().parents[2]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import requests
from PIL import Image  # type: ignore
from sqlalchemy import text

from backend.core.db import get_connection
from backend.core.quote_assets import normalize_marca, drive_direct


OUT_DIR = BASE / "web" / "images" / "quote_assets"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _drive_confirm_url(html: str, direct: str) -> Optional[str]:
    m = re.search(r'href="(/uc\\?export=download[^"]+)"', html)
    if m:
        return "https://drive.google.com" + m.group(1).replace("&amp;", "&")
    m = re.search(r"confirm=([0-9A-Za-z_\\-]+)", html)
    if m:
        return direct + f"&confirm={m.group(1)}"
    return None


def download_to_png(url: str, target: Path) -> Optional[Path]:
    if not url:
        return None
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if "text/html" in (r.headers.get("Content-Type", "") or ""):
            direct = drive_direct(url)
            r2 = requests.get(direct, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if "text/html" in (r2.headers.get("Content-Type", "") or ""):
                confirm = _drive_confirm_url(r2.text, direct)
                if confirm:
                    r3 = requests.get(confirm, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                    r = r3
                else:
                    r = r2
            else:
                r = r2
        if r.status_code != 200:
            return None
    except Exception:
        return None
    # write temp and convert
    try:
        tmp = Path(str(target) + ".tmp")
        tmp.write_bytes(r.content)
        img = Image.open(tmp)
        target.parent.mkdir(parents=True, exist_ok=True)
        img.save(target, format="PNG")
        tmp.unlink(missing_ok=True)
        return target
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    return None


def main():
    with get_connection() as conn:
        rows = conn.execute(text("SELECT * FROM marcas ORDER BY id_marca")).mappings().all()

        for m in rows:
            marca_name = m.get("nombre") or m.get("marca") or ""
            key = normalize_marca(marca_name or "marca")
            base = OUT_DIR / key
            base.mkdir(parents=True, exist_ok=True)

            def handle(field: str, filename: str) -> Optional[str]:
                url = (m.get(field) or "").strip()
                if not url:
                    return None
                out = base / f"{filename}.png"
                saved = download_to_png(url, out)
                if saved:
                    return f"/web/images/quote_assets/{key}/{filename}.png"
                return None

            logo = handle("logo_url", "logo")
            portada = handle("pdf_portada_url", "portada")
            cot = handle("pdf_cotizacion_url", "cotizacion")
            term = handle("pdf_terminos_url", "terminos")
            banco = handle("pdf_banco_url", "banco")

            updates = {}
            if logo:
                updates["logo_url"] = logo
                updates["logo_path"] = logo
            if portada:
                updates["pdf_portada_url"] = portada
            if cot:
                updates["pdf_cotizacion_url"] = cot
            if term:
                updates["pdf_terminos_url"] = term
            if banco:
                updates["pdf_banco_url"] = banco

            if updates:
                sets = ", ".join([f"{k}=:{k}" for k in updates.keys()])
                updates["id"] = m.get("id_marca")
                conn.execute(text(f"UPDATE marcas SET {sets} WHERE id_marca=:id"), updates)
        conn.commit()

    print("OK: assets descargados y marcas actualizadas a rutas locales.")


if __name__ == "__main__":
    main()
