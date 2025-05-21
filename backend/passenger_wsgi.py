import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# Importa tu app ASGI desde el paquete
from backend.main import app as application

# Crea tablas si hiciera falta
from backend.database import Base, engine
Base.metadata.create_all(bind=engine)
