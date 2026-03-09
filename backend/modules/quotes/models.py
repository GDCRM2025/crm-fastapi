# backend/modules/quotes/models.py
import datetime as dt
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Integer, DateTime, Numeric, String, ForeignKey
from backend.core.database import Base


class Quote(Base):
    __tablename__ = "cotizaciones"

    id_cotizacion: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    id_lead: Mapped[int] = mapped_column(
        ForeignKey("leads.id_lead", ondelete="RESTRICT"), nullable=False, index=True
    )
    fecha: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    total: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    pdf_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    items: Mapped[list["QuoteItem"]] = relationship(
        back_populates="quote", cascade="all, delete-orphan"
    )


class QuoteItem(Base):
    __tablename__ = "cotizacion_productos"

    id_item: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    id_cotizacion: Mapped[int] = mapped_column(
        ForeignKey("cotizaciones.id_cotizacion", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    producto: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cantidad: Mapped[int] = mapped_column(Integer, default=1)
    precio_unitario: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    quote: Mapped[Quote] = relationship(back_populates="items")
