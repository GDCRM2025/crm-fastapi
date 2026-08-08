from sqlalchemy import Column, Integer, String, Date, Text, Numeric, TIMESTAMP
from backend.core.database import Base

class LeadPG(Base):
    __tablename__ = "leads"

    id_lead           = Column(Integer, primary_key=True, index=True)
    codigo_cliente    = Column(String(50), nullable=False, unique=True)
    fecha_ingreso     = Column(Date, nullable=False)
    id_marca          = Column(Integer, nullable=False)  # FK en BD, no la declaramos aquí
    nombre_cliente    = Column(String(150), nullable=False)
    id_categoria      = Column(Integer)
    id_tipo_cliente   = Column(Integer)
    plataforma        = Column(String(50))
    fecha_evento      = Column(Date)
    dia               = Column(String(20))
    mes               = Column(String(20))
    semana            = Column(Integer)
    anio              = Column(Integer)
    id_comuna         = Column(Integer)
    telefono          = Column(String(20))
    whatsapp_link     = Column(Text)
    email             = Column(String(150))
    monto_cotizado    = Column(Numeric(12, 2))
    id_estado         = Column(Integer)
    fecha_cierre      = Column(Date)
    seguimiento       = Column(Text)
    id_usuario        = Column(Integer)
    numero_cotizacion = Column(String(50))
    id_segmentacion   = Column(Integer)
    created_at        = Column(TIMESTAMP(timezone=True))
    updated_at        = Column(TIMESTAMP(timezone=True))
