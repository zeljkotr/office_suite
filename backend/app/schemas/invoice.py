from pydantic import ConfigDict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class InvoiceExtractionResponse(BaseModel):
    supplier_name: str | None = None
    supplier_address: str | None = None
    invoice_number: str | None = None
    supplier_tax_id: str | None = None
    supplier_email: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax_rate: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    line_items: list[dict[str, Any]] = Field(default_factory=list)
    extraction_method: str
    requires_human_review: bool = True


class InvoiceValidationResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requires_human_review: bool = True
    calculated_total: str


class InvoiceProcessingResponse(BaseModel):
    fingerprint: str
    extracted_data: InvoiceExtractionResponse
    validation: InvoiceValidationResponse
    requires_human_review: bool = True


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    direction: Literal["incoming", "outgoing"] = "incoming"
    invoice_number: str | None
    supplier_name: str | None
    supplier_tax_id: str | None
    supplier_email: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    status: str
    payment_status: str
    original_filename: str
    uploaded_by: int
    reviewed_by: int | None
    paid_by: int | None
    created_at: datetime
    updated_at: datetime
    paid_amount: Decimal = Decimal("0")
    refunded_amount: Decimal = Decimal("0")
    credit_note_total: Decimal = Decimal("0")
    adjusted_total: Decimal = Decimal("0")
    outstanding_amount: Decimal = Decimal("0")
    overpaid_amount: Decimal = Decimal("0")
    advance_paid_amount: Decimal = Decimal("0")


class InvoiceUpdateRequest(BaseModel):
    direction: Literal["incoming", "outgoing"] = "incoming"
    supplier_name: str | None = Field(default=None, max_length=255)
    invoice_number: str | None = Field(default=None, max_length=100)
    supplier_tax_id: str | None = Field(default=None, max_length=100)
    supplier_email: str | None = Field(default=None, max_length=255)
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    subtotal: Decimal | None = Field(default=None, ge=0)
    tax_amount: Decimal | None = Field(default=None, ge=0)
    total_amount: Decimal | None = Field(default=None, ge=0)
    review_notes: str | None = Field(default=None, max_length=5000)


class InvoiceListItemResponse(InvoiceResponse):
    paid_amount: Decimal = Decimal("0")
    refunded_amount: Decimal = Decimal("0")
    credit_note_total: Decimal = Decimal("0")
    adjusted_total: Decimal = Decimal("0")
    outstanding_amount: Decimal = Decimal("0")
    overpaid_amount: Decimal = Decimal("0")


class InvoiceListResponse(BaseModel):
    items: list[InvoiceListItemResponse]
    total: int
    page: int
    page_size: int


class InvoicePaymentCreateRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    paid_at: datetime | None = None
    method: str | None = Field(default=None, max_length=50)
    transaction_reference: str | None = Field(default=None, max_length=255)


class InvoicePaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int
    amount: Decimal
    currency: str
    paid_at: datetime
    method: str | None
    transaction_reference: str | None
    created_by: int
    created_at: datetime


class InvoiceAuditEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int
    user_id: int | None
    event_type: str
    details: str | None
    created_at: datetime


class InvoiceRefundCreateRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    refunded_at: datetime | None = None
    method: str | None = Field(default=None, max_length=50)
    transaction_reference: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=2000)


class InvoiceRefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int
    amount: Decimal
    currency: str
    refunded_at: datetime
    method: str | None
    transaction_reference: str | None
    reason: str | None
    created_by: int
    created_at: datetime


class InvoiceCreditNoteCreateRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    credit_note_number: str | None = Field(default=None, max_length=100)
    issued_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=2000)


class InvoiceCreditNoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_id: int
    credit_note_number: str | None
    amount: Decimal
    currency: str
    reason: str
    is_cancellation: bool
    issued_at: datetime
    created_by: int | None
    created_at: datetime


class InvoiceAdvanceCreateRequest(BaseModel):
    direction: Literal["incoming", "outgoing"]
    party_name: str = Field(min_length=1, max_length=255)
    currency: str = Field(min_length=3, max_length=3)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    received_at: datetime | None = None
    method: str | None = Field(default=None, max_length=50)
    transaction_reference: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=2000)


class InvoiceAdvanceResponse(BaseModel):
    id: int
    direction: Literal["incoming", "outgoing"]
    party_name: str
    currency: str
    amount: Decimal
    received_at: datetime
    method: str | None
    transaction_reference: str | None
    notes: str | None
    created_by: int
    created_at: datetime
    allocated_amount: Decimal = Decimal("0")
    refunded_amount: Decimal = Decimal("0")
    available_amount: Decimal = Decimal("0")


class InvoiceAdvanceAllocationCreateRequest(BaseModel):
    invoice_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)


class InvoiceAdvanceAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    advance_id: int
    invoice_id: int
    amount: Decimal
    allocated_at: datetime
    created_by: int


class InvoiceAdvanceRefundCreateRequest(BaseModel):
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    refunded_at: datetime | None = None
    method: str | None = Field(default=None, max_length=50)
    transaction_reference: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=2000)


class InvoiceAdvanceRefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    advance_id: int
    amount: Decimal
    refunded_at: datetime
    method: str | None
    transaction_reference: str | None
    reason: str | None
    created_by: int
