# Catálogo fijo de roles (sin tabla)
ROLES_BY_ID = {
    1: "Admin",
    2: "Bodeguero",
    3: "Cocinero",
    4: "Conductor",
    5: "Ejecutivo de Ventas",
    6: "Jefe de Compras",
    7: "Jefe de Cocina",
    8: "Jefe de Operaciones",
    9: "Operaciones",
}
ROLES_BY_NAME = {v.lower(): k for k, v in ROLES_BY_ID.items()}

def role_name_from_id(role_id: int | None) -> str:
    if role_id is None: return ""
    try:
        return ROLES_BY_ID.get(int(role_id), "")
    except Exception:
        return ""

def role_id_from_name(name: str | None) -> int | None:
    if not name: return None
    return ROLES_BY_NAME.get(name.strip().lower())

# ¿Quiénes son “admin-like” (ven todo / pueden administrar)?
ADMINLIKE_IDS = {1, 8, 9}  # Admin, Jefe de Operaciones, Operaciones
