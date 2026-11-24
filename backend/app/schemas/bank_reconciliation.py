from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class BankAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    bank_name: str | None = Field(default=None, max_length=150)
    iban: str | None = Field(default=None, max_length=64)
    account_number: str | None = Field(default=None, max_length=64)
    currency: str = Field(min_length=3, max_length=3)
    opening_balance: Decimal = Field(default=Decimal("0"), max_digits=14, decimal_places=2)

    @field_validator("currency")
    @classmethod
    def clean_currency(cls, value):
        value = value.strip().upper()
        if not value.isalpha():
            raise ValueError("Currency must contain three letters.")
        return value


class BankAccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    bank_name: str | None = Field(default=None, max_length=150)
    iban: str | None = Field(default=None, max_length=64)
    account_number: str | None = Field(default=None, max_length=64)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    opening_balance: Decimal | None = Field(
        default=None, max_digits=14, decimal_places=2
    )
    active: bool | None = None

    @field_validator("currency")
    @classmethod
    def clean_currency(cls, value):
        if value is None:
            return value
        value = value.strip().upper()
        if not value.isalpha():
            raise ValueError("Currency must contain three letters.")
        return value


class BankTransactionCreate(BaseModel):
    transaction_date: date
    value_date: date | None = None
    amount: Decimal = Field(max_digits=14, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    description: str | None = Field(default=None, max_length=20000)
    counterparty: str | None = Field(default=None, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    external_id: str | None = Field(default=None, max_length=255)

    @field_validator("currency")
    @classmethod
    def clean_currency(cls, value):
        value = value.strip().upper()
        if not value.isalpha():
            raise ValueError("Currency must contain three letters.")
        return value


class BankMatchRequest(BaseModel):
    financial_record_id: int = Field(gt=0)


class BankReconciliationCreate(BaseModel):
    period_start: date
    period_end: date
    statement_closing_balance: Decimal = Field(
        max_digits=14, decimal_places=2
    )
    note: str | None = Field(default=None, max_length=5000)
