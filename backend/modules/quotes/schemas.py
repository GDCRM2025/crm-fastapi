from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

class QuoteItemCreate(BaseModel):
    id_producto: int
    cantidad: int
    precio_unitario: float
    total: float

class QuoteCreate(BaseModel):
    id_lead: int
    fecha: Optional[datetime] = None
    total: float = 0
    pdf_url: Optional[str] = None

class QuoteItemOut(BaseModel):
    id: int
    id_producto: int
    cantidad: int
    precio_unitario: float
    total: float
    class Config:
        from_attributes = True

class QuoteOut(BaseModel):
    id_cotizacion: int
    id_lead: int
    fecha: Optional[datetime]
    total: float
    pdf_url: Optional[str]
    class Config:
        from_attributes = True

class QuoteDetail(QuoteOut):
    items: List[QuoteItemOut] = Field(default_factory=list)  # 👈 evita lista compartida
