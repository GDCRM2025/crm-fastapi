from pydantic import BaseModel, EmailStr
from typing import List, Optional

class UsuarioBase(BaseModel):
    nombre_usuario: str
    email: EmailStr
    nivel: str
    status: bool = True

class UsuarioCreate(UsuarioBase):
    password: str
    marcas_ids: Optional[List[int]] = None

class UsuarioUpdate(BaseModel):
    nombre_usuario: Optional[str] = None
    email: Optional[EmailStr] = None
    nivel: Optional[str] = None
    status: Optional[bool] = None
    marcas_ids: Optional[List[int]] = None
    password: Optional[str] = None

class MarcaOut(BaseModel):
    id_marca: int
    nombre: Optional[str] = None
    class Config:
        from_attributes = True

class UsuarioOut(BaseModel):
    id_usuario: int
    nombre_usuario: str
    email: EmailStr
    nivel: str
    status: bool
    marcas: List[MarcaOut] = []
    class Config:
        from_attributes = True
