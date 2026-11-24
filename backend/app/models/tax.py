from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON,
    Numeric, String, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TaxJurisdiction(TimestampMixin, Base):
    __tablename__ = "tax_jurisdictions"
    __table_args__ = (
        UniqueConstraint("owner_id", "code", name="uq_tax_jurisdiction_owner_code"),
        Index("ix_tax_jurisdictions_owner", "owner_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(10))
    region: Mapped[str | None] = mapped_column(String(120))
    currency: Mapped[str | None] = mapped_column(String(3))
    tax_year_start_month: Mapped[int | None] = mapped_column(Integer)
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TaxCategory(TimestampMixin, Base):
    __tablename__ = "tax_categories"
    __table_args__ = (UniqueConstraint("owner_id", "code", name="uq_tax_category_owner_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TaxRate(TimestampMixin, Base):
    __tablename__ = "tax_rates"
    __table_args__ = (Index("ix_tax_rates_owner_jurisdiction", "owner_id", "jurisdiction_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    jurisdiction_id: Mapped[int] = mapped_column(ForeignKey("tax_jurisdictions.id", ondelete="CASCADE"), nullable=False)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("tax_categories.id", ondelete="SET NULL"))
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    tax_type: Mapped[str] = mapped_column(String(40), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    valid_from: Mapped[object | None] = mapped_column(Date)
    valid_to: Mapped[object | None] = mapped_column(Date)
    is_compound: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class TaxRule(TimestampMixin, Base):
    __tablename__ = "tax_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    jurisdiction_id: Mapped[int] = mapped_column(ForeignKey("tax_jurisdictions.id", ondelete="CASCADE"), nullable=False)
    rate_id: Mapped[int | None] = mapped_column(ForeignKey("tax_rates.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    conditions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    treatment: Mapped[str] = mapped_column(String(40), default="standard", nullable=False)
    price_mode: Mapped[str] = mapped_column(String(20), default="exclusive", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class TaxRegistration(TimestampMixin, Base):
    __tablename__ = "tax_registrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    jurisdiction_id: Mapped[int] = mapped_column(ForeignKey("tax_jurisdictions.id", ondelete="CASCADE"), nullable=False)
    identifier_type: Mapped[str] = mapped_column(String(80), nullable=False)
    identifier_value: Mapped[str] = mapped_column(String(180), nullable=False)
    valid_from: Mapped[object | None] = mapped_column(Date)
    valid_to: Mapped[object | None] = mapped_column(Date)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TaxTransaction(TimestampMixin, Base):
    __tablename__ = "tax_transactions"
    __table_args__ = (
        Index("ix_tax_transactions_owner_date", "owner_id", "tax_date"),
        Index("ix_tax_transactions_jurisdiction_date", "jurisdiction_id", "tax_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    jurisdiction_id: Mapped[int] = mapped_column(ForeignKey("tax_jurisdictions.id"), nullable=False)
    rate_id: Mapped[int | None] = mapped_column(ForeignKey("tax_rates.id", ondelete="SET NULL"))
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[int | None] = mapped_column(Integer)
    tax_date: Mapped[object] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    taxable_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    rate_snapshot: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    treatment: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class TaxAuditEvent(Base):
    __tablename__ = "tax_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class TaxIntegration(TimestampMixin, Base):
    __tablename__ = "tax_integrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    jurisdiction_id: Mapped[int | None] = mapped_column(ForeignKey("tax_jurisdictions.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    integration_type: Mapped[str] = mapped_column(String(60), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(500))
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="not_configured", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
