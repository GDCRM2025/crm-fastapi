from typing import Optional, Literal
from pydantic import BaseModel, EmailStr

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    role: Optional[Literal["admin","ejecutivo","operaciones","bodega","patio"]] = None

class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str] = None
    role: Optional[str] = None

    class Config:
        from_attributes = True
