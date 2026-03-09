# backend/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.core.database import init_db
from backend.routers.auth import router as auth_router

app = FastAPI(title="CRM 2025 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ajusta a tu frontend en prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Crea tablas al arrancar
init_db()

# Rutas
app.include_router(auth_router)
