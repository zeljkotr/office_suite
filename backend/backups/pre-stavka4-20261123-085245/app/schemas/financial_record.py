from datetime import date
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class FinancialRecordCreate(BaseModel):
    record_type: str
    record_date: date
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    category: str = Field(min_length=1, max_length=100)
    counterparty: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    payment_method: str | None = Field(default=None, max_length=60)
    document_number: str | None = Field(default=None, max_length=100)
    invoice_id: int | None = Field(default=None, gt=0)

    @field_validator("record_type")
    @classmethod
    def valid_type(cls, value):
        if value not in {"income", "expense"}:
            raise ValueError("Record type must be income or expense.")
        return value

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value):
        value = value.strip().upper()
        if not value.isalpha():
            raise ValueError("Currency must contain three letters.")
        return value

    @field_validator("category")
    @classmethod
    def clean_category(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Category is required.")
        return value


class FinancialRecordUpdate(BaseModel):
    record_date: date | None = None
    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    counterparty: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    payment_method: str | None = Field(default=None, max_length=60)
    document_number: str | None = Field(default=None, max_length=100)
    invoice_id: int | None = Field(default=None, gt=0)

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value):
        if value is None:
            return value
        value = value.strip().upper()
        if not value.isalpha():
            raise ValueError("Currency must contain three letters.")
        return value


class FinancialPaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    payment_date: date
    payment_method: str | None = Field(default=None, max_length=60)
    reference: str | None = Field(default=None, max_length=150)
    notes: str | None = Field(default=None, max_length=20000)


class FinancialRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    record_type: str
    record_date: date
    amount: Decimal
    currency: str
    category: str
    counterparty: str | None
    description: str | None
    payment_method: str | None
    document_number: str | None
    invoice_id: int | None
    attachments: list
    owner_id: int
    created_at: object
    updated_at: object
