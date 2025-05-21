from backend.database import Base, engine
from backend.models import Usuario, CRMGeneral

print("⏳ Creando tablas en la base de datos...")
Base.metadata.create_all(bind=engine)
print("✅ Tablas creadas correctamente.")
