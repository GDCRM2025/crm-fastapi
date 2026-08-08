from pydantic import BaseModel, EmailStr
from typing import Optional, Literal

LeadStatus = Literal["pendiente","cotizado","contactado","negociacion","confirmado","declinado"]

class LeadBase(BaseModel):
    title: str
    contact_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    source: Optional[str] = None
    status: LeadStatus = "pendiente"
    amount: Optional[float] = None
    notes: Optional[str] = None

class LeadCreate(LeadBase):
    pass

class LeadUpdate(BaseModel):
    title: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    source: Optional[str] = None
    status: Optional[LeadStatus] = None
    amount: Optional[float] = None
    notes: Optional[str] = None

class LeadOut(LeadBase):
    id: int
    class Config:
        from_attributes = True
