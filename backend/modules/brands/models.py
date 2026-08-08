from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.database import Base

class Brand(Base):
    __tablename__ = "marcas"
    __table_args__ = {"schema": "public"}

    id_marca: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nombre: Mapped[str] = mapped_column(String, nullable=False, unique=True)
