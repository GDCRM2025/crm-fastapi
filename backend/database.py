import os
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////Users/oscarmendoza/Desktop/CRM 2025/backend/crm.db"
)
try:
    # camino principal
    from .core.database import get_db
except Exception:
    # fallback si el proyecto usa este otro módulo
    from .core.db import get_db
