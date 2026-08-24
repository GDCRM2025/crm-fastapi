from __future__ import annotations

import re
import unicodedata
from typing import Any

CHANNELS = ("WHATSAPP", "INSTAGRAM", "MESSENGER", "EMAIL")
BRANDS = {
    "CAMALEON": {"id": 1, "name": "CAMALEON", "executive": "Andrés Landerer"},
    "GOURMET": {"id": 2, "name": "GOURMET", "executive": "Constanza Franco"},
    "EXPRESS": {"id": 3, "name": "EXPRESS", "executive": "Daniel Toledo"},
    "DEL_SABOR": {"id": 4, "name": "DEL SABOR", "executive": "Andrés Landerer"},
}


def _norm(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]+", "_", raw.upper()).strip("_")


def role_code(user: dict[str, Any] | None) -> str:
    user = user or {}
    return _norm(user.get("role") or user.get("rol") or user.get("cargo"))


def is_admin(user: dict[str, Any] | None) -> bool:
    return role_code(user) in {"ADMIN", "SUPERADMIN"}


def is_control_gestion(user: dict[str, Any] | None) -> bool:
    return role_code(user) in {"CONTROL_DE_GESTION", "CONTROL_GESTION"}


def is_sales(user: dict[str, Any] | None) -> bool:
    role = role_code(user)
    return "EJECUTIVO" in role or "VENTAS" in role or role in {"JDV", "JEFE_DE_VENTAS"}


def can_operate_omnichannel(user: dict[str, Any] | None) -> bool:
    return is_admin(user) or is_control_gestion(user) or is_sales(user)


def can_delete_leads(user: dict[str, Any] | None) -> bool:
    # Regla contractual: CONTROL DE GESTION puede operar, pero nunca borrar leads.
    if is_control_gestion(user):
        return False
    return is_admin(user)


def _brand_tokens(user: dict[str, Any] | None) -> set[str]:
    user = user or {}
    raw = user.get("marcas") or []
    if isinstance(raw, (str, int)):
        raw = [raw]
    out: set[str] = set()
    by_id = {str(info["id"]): code for code, info in BRANDS.items()}
    for item in raw:
        token = _norm(item)
        if str(item) in by_id:
            out.add(by_id[str(item)])
        elif token in BRANDS:
            out.add(token)
        elif token == "DEL_SABOR":
            out.add("DEL_SABOR")
    return out


def can_access_brand(user: dict[str, Any] | None, brand_code: str | int | None) -> bool:
    if not can_operate_omnichannel(user):
        return False
    if is_admin(user) or is_control_gestion(user):
        return True
    token = _norm(brand_code)
    if token.isdigit():
        token = {str(v["id"]): k for k, v in BRANDS.items()}.get(token, token)
    return token in _brand_tokens(user)


def visible_brands(user: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        {"code": code, **info}
        for code, info in BRANDS.items()
        if can_access_brand(user, code)
    ]
