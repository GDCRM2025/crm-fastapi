# backend/models.py

from sqlalchemy import Column, String, Enum
from backend.database import Base  # CORRECTO: usa la misma Base global
import enum

# ==== Enum de roles de usuario ====
class RolEnum(str, enum.Enum):
    Admin = "Admin"
    JDV = "JDV"
    EjecVen = "EjecVen"
    Ops = "Ops"
    JDO = "JDO"

# ==== Modelo de tabla: Usuarios ====
class Usuario(Base):
    __tablename__ = "usuarios"

    id_usuario = Column(String, primary_key=True, index=True)
    nombre_usuario = Column(String, nullable=False)
    correo = Column(String, unique=True, nullable=False)
    contraseña = Column(String, nullable=False)
    rol = Column(Enum(RolEnum), nullable=False)
    status = Column(String, default="Activo")
    marcas = Column(String, nullable=True)  # Solo aplica si es EjecVen

# ==== Modelo de tabla: CRM General ====
class CRMGeneral(Base):
    __tablename__ = "crm_general"

    id = Column(String, primary_key=True)
    codigo_cliente = Column(String, nullable=False)
    fecha_ingreso = Column(String)
    marca = Column(String)
    nombre_cliente = Column(String)
    categoria_cliente = Column(String)
    tipo_cliente = Column(String)
    plataforma = Column(String)
    fecha_evento = Column(String)
    dia = Column(String)
    mes = Column(String)
    semana = Column(String)
    año = Column(String)
    comuna = Column(String)
    telefono = Column(String)
    link_whatsapp = Column(String)
    email = Column(String)
    monto_cotizado = Column(String)
    estado = Column(String)
    fecha_cierre = Column(String)
    seguimiento = Column(String)
    fecha_seguimiento = Column(String)
    creado_por = Column(String)
    nro_cotizacion = Column(String)
    segmentacion = Column(String)
