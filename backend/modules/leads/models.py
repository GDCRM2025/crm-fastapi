# backend/modules/leads/models.py
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String
from backend.core.database import Base


class Lead(Base):
    __tablename__ = "leads"

    id_lead: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)
