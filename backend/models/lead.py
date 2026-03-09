# backend/models/lead.py
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from backend.core.database import Base

DEFAULT_STATUS = "pendiente"

class Lead(Base):
    __tablename__ = "leads"
    id        = Column(Integer, primary_key=True, index=True)
    nombre    = Column(String,  nullable=False)   # si tu columna real es name, ajusta el nombre aquí
    email     = Column(String,  nullable=True)
    telefono  = Column(String,  nullable=True)    # si es phone, ajusta
    fuente    = Column(String,  nullable=True)    # si es source, ajusta
    estado    = Column(String,  nullable=False, default=DEFAULT_STATUS)

    id_usuario = Column(Integer, ForeignKey("usuarios.id_usuario"), nullable=True)
    id_marca   = Column(Integer, ForeignKey("marcas.id_marca"),   nullable=True)

    usuario = relationship("Usuario", lazy="joined")
    marca   = relationship("Marca",   lazy="joined")
