from __future__ import annotations

import re
from typing import Dict
import unicodedata


def _drive_id(url: str) -> str:
    # soporta /file/d/<id>/ y ?id=<id>
    m = re.search(r"/file/d/([^/]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([^&]+)", url)
    if m:
        return m.group(1)
    return url


def drive_direct(url: str) -> str:
    fid = _drive_id(url)
    return f"https://drive.google.com/uc?export=download&id={fid}"

def drive_view(url: str) -> str:
    fid = _drive_id(url)
    return f"https://drive.google.com/uc?export=view&id={fid}"

def normalize_marca(name: str) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.strip().upper()


LOGOS_REMOTE: Dict[str, str] = {
    "EXPRESS": drive_direct("https://drive.google.com/file/d/1O1rreV4XwSG0FSzWKodUBR5VtDwBExR3/view?usp=sharing"),
    "CAMALEON": drive_direct("https://drive.google.com/file/d/139ccxgZPybBgssm5tNxd5bDPDhq_sZ-H/view?usp=sharing"),
    "DEL SABOR": drive_direct("https://drive.google.com/file/d/1Z8Ix8vbT6vtxxbvjoH2uryREtJmG_qKt/view?usp=sharing"),
    "GOURMET": drive_direct("https://drive.google.com/file/d/1CW8cV-QyUEFDCS73uUbOKxAWQL4xLNxD/view?usp=sharing"),
}

# fallback local (sirve desde /web)
LOGOS_LOCAL: Dict[str, str] = {
    "CAMALEON": "/web/images/logo_cam_300px.webp",
    "DEL SABOR": "/web/images/logo_delsabor_300px.webp",
    "EXPRESS": "/web/images/loogo_express_300px.webp",
    "GOURMET": "/web/images/Logo Gourmet Blanco 300x300p.webp",
}

def logo_for(marca: str, prefer_local: bool = True) -> str:
    key = normalize_marca(marca or "")
    if prefer_local and key in LOGOS_LOCAL:
        return LOGOS_LOCAL[key]
    return LOGOS_REMOTE.get(key, "")

# backwards compat
LOGOS = LOGOS_REMOTE


PDF_ASSETS: Dict[str, Dict[str, str]] = {
    "CAMALEON": {
        "banco": drive_direct("https://drive.google.com/file/d/19G4xnzuWH3XP5VBCbT4nQl_OjdjQzhcS/view?usp=sharing"),
        "cotizacion": drive_direct("https://drive.google.com/file/d/1OqgCE2rjHCQQzIYU7aWREQwZooykvF6L/view?usp=drive_link"),
        "terminos": drive_direct("https://drive.google.com/file/d/1AzzAlgHPqJBRmP1DvMGuSjRl5NZgvMh6/view?usp=drive_link"),
        "portada": drive_direct("https://drive.google.com/file/d/1WgMm282yL6NLq8eufe2_AeOBZXQRzgoc/view?usp=drive_link"),
    },
    "DEL SABOR": {
        "banco": drive_direct("https://drive.google.com/file/d/1PGtV-l5ELNLLfflCRF596G4OZDE9GreI/view?usp=drive_link"),
        "cotizacion": drive_direct("https://drive.google.com/file/d/1els9ffNLkeJ9AsEx5X1LKnz8IJ2G_P8_/view?usp=drive_link"),
        "terminos": drive_direct("https://drive.google.com/file/d/1DmEJptGE0MDThtT135y7neEaW_5QLtmS/view?usp=sharing"),
        "portada": drive_direct("https://drive.google.com/file/d/1e39CHNFeaSp2xjFevFYvtiL_seEcJI0g/view?usp=sharing"),
    },
    "EXPRESS": {
        "banco": drive_direct("https://drive.google.com/file/d/12nl4sr4KGm9TlftQj6Gb2LqTVRf_j_9U/view?usp=drive_link"),
        "cotizacion": drive_direct("https://drive.google.com/file/d/1u1k2I9e5BoCpMNhO-y17uhdCAHxvv1wM/view?usp=drive_link"),
        "terminos": drive_direct("https://drive.google.com/file/d/1kZ1mC0j0clMiDatNKmSmhPIzMzHR_onu/view?usp=drive_link"),
        "portada": drive_direct("https://drive.google.com/file/d/11veDhBcHD7oNOrcai3sjSMuSQAmqy-qq/view?usp=drive_link"),
    },
    "GOURMET": {
        "banco": drive_direct("https://drive.google.com/file/d/1KkN0CSKU0p6Add_EUPfM89r_ZbFEJqCD/view?usp=drive_link"),
        "cotizacion": drive_direct("https://drive.google.com/file/d/10nZXlW9D125D2oBy4egNchPRlO1h16Li/view?usp=drive_link"),
        "terminos": drive_direct("https://drive.google.com/file/d/15b1Ud3NSeRrXYP6_RgVbUUq-fqJyyXNA/view?usp=drive_link"),
        "portada": drive_direct("https://drive.google.com/file/d/1cvPro2Cp2eKjD6oQ-1FsQTkZOE2PBnwJ/view?usp=drive_link"),
    },
    # BRONTOS (assets iniciales; también se pueden sobreescribir desde tabla marcas)
    "BRONTOS": {
        "banco": drive_direct("https://drive.google.com/file/d/1FjAep8s4McXubZTNcf9hD93ss7KFhZA8/view?usp=drive_link"),
        "cotizacion": drive_direct("https://drive.google.com/file/d/1MBF-DLJWbfJDiruntEbKDza0h1mJfniw/view?usp=drive_link"),
        "terminos": drive_direct("https://drive.google.com/file/d/1iVdqgC4V3kbv7Jbv3Ioar39SZ65BRiLF/view?usp=drive_link"),
        "portada": drive_direct("https://drive.google.com/file/d/1LujCB7nQ2_EGSIotXQEw2ImklOroHEbJ/view?usp=drive_link"),
    },
}

# Carpetas Drive (assets por marca). La idea es que dentro de la carpeta existan archivos
# con nombres que incluyan: portada / cotizacion / terminos / banco / logo (cualquier extensión).
DRIVE_ASSET_FOLDERS: Dict[str, str] = {
    "DEL SABOR": "11EujmRiFtgHRgsklHr3VFC0paXVNS_Dx",
    "CAMALEON": "1bVuQPNZIN6TL0oBqhA0REkxtc4CsafzL",
    "EXPRESS": "1qBpw6ugENKGQ-cf8AJhob8LAoG0v4jz1",
    "GOURMET": "1Bwck0rMhWQwe4T0DZ1NEccsGbw6_PeW3",
}
