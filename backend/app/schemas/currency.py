from datetime import date
from decimal import Decimal
from pydantic import BaseModel, Field, field_validator


class CurrencySettingsUpdate(BaseModel):
    base_currency: str = Field(min_length=3, max_length=3)

    @field_validator("base_currency")
    @classmethod
    def normalize_currency(cls, value):
        value = value.strip().upper()
        if not value.isalpha():
            raise ValueError("Currency must contain three letters.")
        return value


class CurrencyRateCreate(BaseModel):
    from_currency: str = Field(min_length=3, max_length=3)
    to_currency: str = Field(min_length=3, max_length=3)
    effective_date: date
    rate: Decimal = Field(gt=0, max_digits=20, decimal_places=10)

    @field_validator("from_currency", "to_currency")
    @classmethod
    def normalize_currency(cls, value):
        value = value.strip().upper()
        if not value.isalpha():
            raise ValueError("Currency must contain three letters.")
        return value
