from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine, Base
from models import *

# Crear todas las tablas definidas en models.py
Base.metadata.create_all(bind=engine)

# Crear la app de FastAPI
app = FastAPI()

# Configurar CORS para permitir cualquier origen
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Cambia esto si quieres restringir a dominios específicos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ruta base
@app.get("/")
async def root():
    return {"message": "🚀 Aplicación FastAPI desplegada correctamente con Render"}

# Solo se ejecuta si corres el archivo directamente (útil para desarrollo local y producción en Render)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
