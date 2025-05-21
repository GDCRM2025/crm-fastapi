from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# IMPORT CORRECTO, usando el paquete 'backend'
from backend.database import engine, Base
from backend.models import *  

# Crea tablas
Base.metadata.create_all(bind=engine)

app = FastAPI()

# Habilitar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "¡Aplicación FastAPI desplegada 
correctamente!"}
