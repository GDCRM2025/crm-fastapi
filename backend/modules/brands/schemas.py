from pydantic import BaseModel

class BrandOut(BaseModel):
    id_marca: int
    nombre: str
    class Config:
        from_attributes = True

class BrandCreate(BaseModel):
    nombre: str

class BrandUpdate(BaseModel):
    nombre: str
