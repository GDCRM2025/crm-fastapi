from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import date
from decimal import Decimal

class LeadBase(BaseModel):
    codigo_cliente: str
    fecha_ingreso: date
    id_marca: int
    nombre_cliente: str
    id_categoria: Optional[int] = None
    id_tipo_cliente: Optional[int] = None
    plataforma: Optional[str] = None
    fecha_evento: Optional[date] = None
    dia: Optional[str] = None
    mes: Optional[str] = None
    semana: Optional[int] = None
    anio: Optional[int] = None
    id_comuna: Optional[int] = None
    telefono: Optional[str] = None
    whatsapp_link: Optional[str] = None
    email: Optional[EmailStr] = None
    monto_cotizado: Optional[Decimal] = None
    id_estado: Optional[int] = None
    fecha_cierre: Optional[date] = None
    seguimiento: Optional[str] = None
    id_usuario: Optional[int] = None
    numero_cotizacion: Optional[str] = None
    id_segmentacion: Optional[int] = None

class LeadCreate(LeadBase):
    pass

class LeadUpdate(BaseModel):
    # todos opcionales para patch
    codigo_cliente: Optional[str] = None
    fecha_ingreso: Optional[date] = None
    id_marca: Optional[int] = None
    nombre_cliente: Optional[str] = None
    id_categoria: Optional[int] = None
    id_tipo_cliente: Optional[int] = None
    plataforma: Optional[str] = None
    fecha_evento: Optional[date] = None
    dia: Optional[str] = None
    mes: Optional[str] = None
    semana: Optional[int] = None
    anio: Optional[int] = None
    id_comuna: Optional[int] = None
    telefono: Optional[str] = None
    whatsapp_link: Optional[str] = None
    email: Optional[EmailStr] = None
    monto_cotizado: Optional[Decimal] = None
    id_estado: Optional[int] = None
    fecha_cierre: Optional[date] = None
    seguimiento: Optional[str] = None
    id_usuario: Optional[int] = None
    numero_cotizacion: Optional[str] = None
    id_segmentacion: Optional[int] = None

class LeadOut(LeadBase):
    id_lead: int
    class Config:
        from_attributes = True
