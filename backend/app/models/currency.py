from sqlalchemy import Column, Integer, String, Numeric, Date, DateTime
from sqlalchemy import UniqueConstraint, Index, func

from app.models.base import Base


class CurrencySettings(Base):
    __tablename__ = "currency_settings"

    owner_id = Column(Integer, primary_key=True)
    base_currency = Column(String(3), nullable=False, default="RSD")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)


class CurrencyRate(Base):
    __tablename__ = "currency_rates"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "from_currency", "to_currency", "effective_date",
            name="uq_currency_rate_owner_pair_date",
        ),
        Index("ix_currency_rate_lookup",
              "owner_id", "from_currency", "to_currency", "effective_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, nullable=False, index=True)
    from_currency = Column(String(3), nullable=False)
    to_currency = Column(String(3), nullable=False)
    effective_date = Column(Date, nullable=False)
    rate = Column(Numeric(20, 10), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(),
                        nullable=False)
