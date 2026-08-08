# backend/models/usuario.py
from sqlalchemy import Column, Integer, String, Boolean, TIMESTAMP, Table, ForeignKey
from sqlalchemy.orm import relationship
from backend.core.database import Base

UsuarioMarca = Table(
    "usuario_marcas",
    Base.metadata,
    Column("id_usuario", Integer, ForeignKey("usuarios.id_usuario"), primary_key=True),
    Column("id_marca",   Integer, ForeignKey("marcas.id_marca"),   primary_key=True),
)

class Marca(Base):
    __tablename__ = "marcas"
    id_marca = Column(Integer, primary_key=True, index=True)
    nombre   = Column(String(100), nullable=False)

class Usuario(Base):
    __tablename__ = "usuarios"
    id_usuario     = Column(Integer, primary_key=True, index=True)
    nombre_usuario = Column(String(100), nullable=False)
    email          = Column(String(150), nullable=False)
    password_hash  = Column(String, nullable=False)
    nivel          = Column(String(20),  nullable=False)
    status         = Column(Boolean,     nullable=False, default=True)
    id_marca       = Column(Integer,     nullable=True)  # legado
    created_at     = Column(TIMESTAMP(timezone=True), nullable=False)

    marcas = relationship("Marca", secondary=UsuarioMarca, backref="usuarios")
