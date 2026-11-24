from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response

from PIL import Image

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user
from app.config import settings
from app.database import get_db
from app.models.invoice import Invoice, InvoiceAuditEvent, InvoicePayment, InvoiceRefund, InvoiceCreditNote, InvoiceAdvance, InvoiceAdvanceAuditEvent, InvoiceAdvanceAllocation, InvoiceAdvanceRefund
from app.models.user import User
from app.schemas.invoice import (
    InvoiceListResponse,
    InvoiceResponse,
    InvoiceUpdateRequest,
    InvoicePaymentCreateRequest,
    InvoicePaymentResponse,
    InvoiceRefundCreateRequest,
    InvoiceRefundResponse,
    InvoiceCreditNoteCreateRequest,
    InvoiceCreditNoteResponse,
    InvoiceAuditEventResponse,
    InvoiceAdvanceCreateRequest,
    InvoiceAdvanceResponse,
    InvoiceAdvanceAllocationCreateRequest,
    InvoiceAdvanceAllocationResponse,
    InvoiceAdvanceRefundCreateRequest,
    InvoiceAdvanceRefundResponse,
)
from app.services.invoice_processing import process_invoice
from app.services.invoice_finance import calculate_invoice_balance
from app.services.ocr import OCRProcessingError
from app.services.invoice_validation import ISO_CURRENCY_CODE, validate_invoice


router = APIRouter(prefix="/invoices", tags=["Invoices"])

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def ensure_invoice_access(invoice: Invoice, user: User) -> None:
    if not user.is_superuser and invoice.uploaded_by != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )




def record_invoice_event(
    db: Session,
    invoice_id: int,
    user_id: int | None,
    event_type: str,
    details: dict | None = None,
) -> None:
    db.add(InvoiceAuditEvent(
        invoice_id=invoice_id,
        user_id=user_id,
        event_type=event_type,
        details=json.dumps(details, default=str) if details else None,
        created_at=datetime.now(timezone.utc),
    ))



def record_advance_event(
    db: Session,
    advance_id: int,
    user_id: int | None,
    event_type: str,
    details: dict | None = None,
) -> None:
    db.add(InvoiceAdvanceAuditEvent(
        advance_id=advance_id,
        user_id=user_id,
        event_type=event_type,
        details=json.dumps(details, default=str) if details else None,
        created_at=datetime.now(timezone.utc),
    ))


def get_invoice_net_paid(db: Session, invoice_id: int) -> Decimal:
    payments = db.query(func.coalesce(func.sum(InvoicePayment.amount), 0)).filter(
        InvoicePayment.invoice_id == invoice_id
    ).scalar()
    refunds = db.query(func.coalesce(func.sum(InvoiceRefund.amount), 0)).filter(
        InvoiceRefund.invoice_id == invoice_id
    ).scalar()
    advances = db.query(
        func.coalesce(func.sum(InvoiceAdvanceAllocation.amount), 0)
    ).filter(
        InvoiceAdvanceAllocation.invoice_id == invoice_id
    ).scalar()
    return (
        Decimal(str(payments or 0))
        - Decimal(str(refunds or 0))
        + Decimal(str(advances or 0))
    )


def get_invoice_credit_total(db: Session, invoice_id: int) -> Decimal:
    credited = db.query(
        func.coalesce(func.sum(InvoiceCreditNote.amount), 0)
    ).filter(
        InvoiceCreditNote.invoice_id == invoice_id
    ).scalar()
    return Decimal(str(credited or 0))


def get_invoice_adjusted_total(db: Session, invoice: Invoice) -> Decimal:
    credited = get_invoice_credit_total(db, invoice.id)
    return max(Decimal("0"), Decimal(str(invoice.total_amount or 0)) - credited)


def validate_file_signature(file_bytes: bytes, extension: str) -> bool:
    if extension == ".pdf":
        return file_bytes.startswith(b"%PDF-")

    if extension in {".jpg", ".jpeg"}:
        return file_bytes.startswith(b"\xff\xd8\xff")

    if extension == ".png":
        return file_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    if extension in {".tif", ".tiff"}:
        return file_bytes.startswith((b"II*\x00", b"MM\x00*"))

    return False


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_invoice(
    file: UploadFile = File(...),
    direction: str = Form(default="incoming"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if direction not in {"incoming", "outgoing"}:
        raise HTTPException(status_code=422, detail="Direction must be incoming or outgoing.")

    original_filename = (file.filename or "document").replace("\\", "/")
    original_filename = original_filename.split("/")[-1]
    extension = Path(original_filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type. Upload PDF, JPG, PNG or TIFF.",
        )

    max_size = settings.max_upload_size_mb * 1024 * 1024
    file_bytes = await file.read(max_size + 1)
    await file.close()

    if not file_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded document is empty.",
        )

    if len(file_bytes) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_size_mb} MB limit.",
        )

    if not validate_file_signature(file_bytes, extension):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="The file content does not match its extension.",
        )

    fingerprint = __import__("hashlib").sha256(file_bytes).hexdigest()

    duplicate = (
        db.query(Invoice)
        .filter(Invoice.duplicate_fingerprint == fingerprint)
        .first()
    )

    if duplicate:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This document has already been uploaded as invoice ID {duplicate.id}.",
        )

    try:
        processing = process_invoice(file_bytes, original_filename, db, current_user)
    except OCRProcessingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    extracted = processing["extracted_data"]
    upload_directory = Path(settings.upload_dir).resolve()
    upload_directory.mkdir(parents=True, exist_ok=True)

    stored_filename = f"{uuid.uuid4().hex}{extension}"
    stored_path = upload_directory / stored_filename

    invoice = Invoice(
        direction=direction,
        invoice_number=extracted.get("invoice_number"),
        supplier_name=extracted.get("supplier_name"),
        supplier_tax_id=extracted.get("supplier_tax_id"),
        supplier_email=extracted.get("supplier_email"),
        invoice_date=date.fromisoformat(extracted["invoice_date"]) if extracted.get("invoice_date") else None,
        due_date=date.fromisoformat(extracted["due_date"]) if extracted.get("due_date") else None,
        currency=extracted.get("currency") or "",
        subtotal=Decimal(str(extracted.get("subtotal") or "0")),
        tax_amount=Decimal(str(extracted.get("tax_amount") or "0")),
        total_amount=Decimal(str(extracted.get("total_amount") or "0")),
        status="pending_review",
        payment_status="unpaid",
        original_filename=original_filename[:255],
        stored_filename=stored_filename,
        file_path=str(stored_path),
        ocr_text=processing["ocr_text"],
        extraction_data=json.dumps(extracted, default=str),
        duplicate_fingerprint=fingerprint,
        uploaded_by=current_user.id,
    )

    try:
        stored_path.write_bytes(file_bytes)
        db.add(invoice)
        db.flush()
        record_invoice_event(db, invoice.id, current_user.id, "uploaded")
        db.commit()
        db.refresh(invoice)
    except Exception:
        db.rollback()
        stored_path.unlink(missing_ok=True)
        raise

    return {
        "invoice": InvoiceResponse.model_validate(invoice).model_dump(mode="json"),
        "extraction": extracted,
        "validation": processing["validation"],
        "requires_human_review": True,
    }


@router.get("", response_model=InvoiceListResponse)
def list_invoices(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    direction: str | None = Query(default=None, pattern="^(incoming|outgoing)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Invoice)
    if not current_user.is_superuser:
        query = query.filter(Invoice.uploaded_by == current_user.id)
    if direction:
        query = query.filter(Invoice.direction == direction)

    total = query.count()

    payment_totals = (
        db.query(
            InvoicePayment.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoicePayment.amount), 0).label("paid_amount"),
        )
        .group_by(InvoicePayment.invoice_id)
        .subquery()
    )

    refund_totals = (
        db.query(
            InvoiceRefund.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoiceRefund.amount), 0).label("refunded_amount"),
        )
        .group_by(InvoiceRefund.invoice_id)
        .subquery()
    )
    credit_totals = (
        db.query(
            InvoiceCreditNote.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoiceCreditNote.amount), 0).label("credit_note_total"),
        )
        .group_by(InvoiceCreditNote.invoice_id)
        .subquery()
    )
    advance_totals = (
        db.query(
            InvoiceAdvanceAllocation.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoiceAdvanceAllocation.amount), 0).label("advance_paid_amount"),
        )
        .group_by(InvoiceAdvanceAllocation.invoice_id)
        .subquery()
    )

    rows = (
        query.outerjoin(payment_totals, payment_totals.c.invoice_id == Invoice.id)
        .outerjoin(refund_totals, refund_totals.c.invoice_id == Invoice.id)
        .outerjoin(credit_totals, credit_totals.c.invoice_id == Invoice.id)
        .outerjoin(advance_totals, advance_totals.c.invoice_id == Invoice.id)
        .add_columns(
            func.coalesce(payment_totals.c.paid_amount, 0).label("paid_amount"),
            func.coalesce(refund_totals.c.refunded_amount, 0).label("refunded_amount"),
            func.coalesce(credit_totals.c.credit_note_total, 0).label("credit_note_total"),
            func.coalesce(advance_totals.c.advance_paid_amount, 0).label("advance_paid_amount"),
        )
        .order_by(Invoice.created_at.desc(), Invoice.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for invoice, paid_amount, refunded_amount, credit_note_total, advance_paid_amount in rows:
        net_paid = (Decimal(str(paid_amount or 0)) - Decimal(str(refunded_amount or 0)) + Decimal(str(advance_paid_amount or 0)))
        credited = Decimal(str(credit_note_total or 0))
        adjusted_total = max(Decimal("0"), Decimal(str(invoice.total_amount or 0)) - credited)
        balance = calculate_invoice_balance(adjusted_total, net_paid)
        item = {
            column.name: getattr(invoice, column.name)
            for column in Invoice.__table__.columns
        }
        item["paid_amount"] = balance["paid_amount"]
        item["advance_paid_amount"] = Decimal(str(advance_paid_amount or 0))
        item["refunded_amount"] = Decimal(str(refunded_amount or 0))
        item["credit_note_total"] = credited
        item["adjusted_total"] = adjusted_total
        item["outstanding_amount"] = balance["outstanding_amount"]
        item["overpaid_amount"] = balance["overpaid_amount"]
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/summary")
def invoice_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payment_totals = (
        db.query(
            InvoicePayment.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoicePayment.amount), 0).label("collected"),
        )
        .group_by(InvoicePayment.invoice_id)
        .subquery()
    )
    refund_totals = (
        db.query(
            InvoiceRefund.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoiceRefund.amount), 0).label("refunded"),
        )
        .group_by(InvoiceRefund.invoice_id)
        .subquery()
    )
    credit_totals = (
        db.query(
            InvoiceCreditNote.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoiceCreditNote.amount), 0).label("credited"),
        )
        .group_by(InvoiceCreditNote.invoice_id)
        .subquery()
    )
    advance_totals = (
        db.query(
            InvoiceAdvanceAllocation.invoice_id.label("invoice_id"),
            func.coalesce(func.sum(InvoiceAdvanceAllocation.amount), 0).label("advance_paid"),
        )
        .group_by(InvoiceAdvanceAllocation.invoice_id)
        .subquery()
    )
    query = (
        db.query(
            Invoice.direction,
            Invoice.currency,
            Invoice.total_amount,
            Invoice.status,
            Invoice.payment_status,
            (
                func.coalesce(payment_totals.c.collected, 0)
                - func.coalesce(refund_totals.c.refunded, 0)
                + func.coalesce(advance_totals.c.advance_paid, 0)
            ).label("collected"),
            func.coalesce(credit_totals.c.credited, 0).label("credited"),
        )
        .outerjoin(payment_totals, payment_totals.c.invoice_id == Invoice.id)
        .outerjoin(refund_totals, refund_totals.c.invoice_id == Invoice.id)
        .outerjoin(credit_totals, credit_totals.c.invoice_id == Invoice.id)
        .outerjoin(advance_totals, advance_totals.c.invoice_id == Invoice.id)
    )
    if not current_user.is_superuser:
        query = query.filter(Invoice.uploaded_by == current_user.id)

    totals = {}
    direction_totals = {"incoming": {}, "outgoing": {}}
    invoice_count = 0
    status_counts = {"pending_review": 0, "approved": 0, "rejected": 0, "paid": 0, "cancelled": 0}
    for direction, currency, total_amount, invoice_status, payment_status, collected_amount, credited_amount in query.all():
        code = (currency or "").upper() or "Unknown currency"
        if invoice_status in status_counts and invoice_status != "paid":
            status_counts[invoice_status] += 1
        invoice_count += 1

        # Only approved invoices contribute to AP/AR financial totals.
        if invoice_status != "approved":
            continue

        values = totals.setdefault(code, {
            "invoiced": Decimal("0"),
            "collected": Decimal("0"),
            "outstanding": Decimal("0"),
        })
        adjusted_total = max(
            Decimal("0"),
            Decimal(str(total_amount or 0)) - Decimal(str(credited_amount or 0)),
        )
        balance = calculate_invoice_balance(adjusted_total, collected_amount)
        total = balance["total_amount"]
        collected = balance["paid_amount"]
        values["invoiced"] += total
        values["collected"] += collected
        values["outstanding"] += balance["outstanding_amount"]

        if balance["payment_status"] == "paid":
            status_counts["paid"] += 1

        directional = direction_totals.setdefault(direction, {})
        directional_values = directional.setdefault(code, {
            "invoiced": Decimal("0"), "collected": Decimal("0"), "outstanding": Decimal("0")
        })
        directional_values["invoiced"] += total
        directional_values["collected"] += collected
        directional_values["outstanding"] += balance["outstanding_amount"]

    return {
        "invoice_count": invoice_count,
        "counts": status_counts,
        "currencies": [
            {
                "currency": code,
                "invoiced": str(values["invoiced"]),
                "collected": str(values["collected"]),
                "outstanding": str(values["outstanding"]),
            }
            for code, values in sorted(totals.items())
        ],
        "by_direction": {
            direction: [
                {"currency": code, **{key: str(value) for key, value in values.items()}}
                for code, values in sorted(currency_totals.items())
            ]
            for direction, currency_totals in direction_totals.items()
        },
    }



def get_advance_balances(db: Session, advance: InvoiceAdvance) -> dict:
    allocated = Decimal(str(
        db.query(func.coalesce(func.sum(InvoiceAdvanceAllocation.amount), 0))
        .filter(InvoiceAdvanceAllocation.advance_id == advance.id).scalar() or 0
    ))
    refunded = Decimal(str(
        db.query(func.coalesce(func.sum(InvoiceAdvanceRefund.amount), 0))
        .filter(InvoiceAdvanceRefund.advance_id == advance.id).scalar() or 0
    ))
    available = Decimal(str(advance.amount)) - allocated - refunded
    return {
        "allocated_amount": allocated,
        "refunded_amount": refunded,
        "available_amount": max(Decimal("0"), available),
    }


@router.get("/advances", response_model=list[InvoiceAdvanceResponse])
def list_invoice_advances(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(InvoiceAdvance)
    if not current_user.is_superuser:
        query = query.filter(InvoiceAdvance.created_by == current_user.id)
    advances = query.order_by(InvoiceAdvance.received_at.desc(), InvoiceAdvance.id.desc()).all()
    return [
        {
            **{column.name: getattr(advance, column.name) for column in InvoiceAdvance.__table__.columns},
            **get_advance_balances(db, advance),
        }
        for advance in advances
    ]


@router.post(
    "/advances",
    response_model=InvoiceAdvanceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invoice_advance(
    payload: InvoiceAdvanceCreateRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    currency = payload.currency.strip().upper()
    party_name = payload.party_name.strip()
    if not party_name:
        raise HTTPException(status_code=422, detail="Partner name is required.")
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(status_code=422, detail="Invalid currency code.")

    advance = InvoiceAdvance(
        direction=payload.direction,
        party_name=party_name,
        currency=currency,
        amount=payload.amount,
        received_at=payload.received_at or datetime.now(timezone.utc),
        method=payload.method.strip() if payload.method else None,
        transaction_reference=payload.transaction_reference.strip() if payload.transaction_reference else None,
        notes=payload.notes.strip() if payload.notes else None,
        created_by=current_admin.id,
    )
    db.add(advance)
    db.flush()
    record_advance_event(
        db, advance.id, current_admin.id, "advance_created",
        {"advance_id": advance.id, "amount": str(advance.amount), "currency": currency,
         "direction": advance.direction, "party_name": party_name},
    )
    db.commit()
    db.refresh(advance)
    return {**{column.name: getattr(advance, column.name) for column in InvoiceAdvance.__table__.columns},
            **get_advance_balances(db, advance)}


@router.get(
    "/advances/{advance_id}/allocations",
    response_model=list[InvoiceAdvanceAllocationResponse],
)
def list_invoice_advance_allocations(
    advance_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    advance = db.query(InvoiceAdvance).filter(InvoiceAdvance.id == advance_id).first()
    if advance is None:
        raise HTTPException(status_code=404, detail="Advance not found.")
    if not current_user.is_superuser and advance.created_by != current_user.id:
        raise HTTPException(status_code=404, detail="Advance not found.")
    return (
        db.query(InvoiceAdvanceAllocation)
        .filter(InvoiceAdvanceAllocation.advance_id == advance_id)
        .order_by(InvoiceAdvanceAllocation.allocated_at.desc(), InvoiceAdvanceAllocation.id.desc())
        .all()
    )


@router.post(
    "/advances/{advance_id}/allocations",
    response_model=InvoiceAdvanceAllocationResponse,
    status_code=status.HTTP_201_CREATED,
)
def allocate_invoice_advance(
    advance_id: int,
    payload: InvoiceAdvanceAllocationCreateRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    advance = (
        db.query(InvoiceAdvance).filter(InvoiceAdvance.id == advance_id)
        .with_for_update().first()
    )
    if advance is None:
        raise HTTPException(status_code=404, detail="Advance not found.")

    invoice = (
        db.query(Invoice).filter(Invoice.id == payload.invoice_id)
        .with_for_update().first()
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != "approved":
        raise HTTPException(status_code=409, detail="Advances can only be allocated to approved invoices.")
    if invoice.direction != advance.direction:
        raise HTTPException(status_code=422, detail="Advance direction does not match invoice direction.")
    if (invoice.currency or "").upper() != advance.currency.upper():
        raise HTTPException(status_code=422, detail="Advance currency does not match invoice currency.")
    if (invoice.supplier_name or "").strip().casefold() != advance.party_name.strip().casefold():
        raise HTTPException(status_code=422, detail="Advance partner does not match invoice partner.")

    advance_balance = get_advance_balances(db, advance)
    if payload.amount > advance_balance["available_amount"]:
        raise HTTPException(
            status_code=422,
            detail={"message": "Allocation exceeds available advance.", "available": str(advance_balance["available_amount"])},
        )

    net_paid = get_invoice_net_paid(db, invoice.id)
    adjusted_total = get_invoice_adjusted_total(db, invoice)
    invoice_balance = calculate_invoice_balance(adjusted_total, net_paid)
    if payload.amount > invoice_balance["outstanding_amount"]:
        raise HTTPException(
            status_code=422,
            detail={"message": "Allocation exceeds outstanding invoice balance.",
                    "outstanding": str(invoice_balance["outstanding_amount"])},
        )

    allocation = InvoiceAdvanceAllocation(
        advance_id=advance.id,
        invoice_id=invoice.id,
        amount=payload.amount,
        allocated_at=datetime.now(timezone.utc),
        created_by=current_admin.id,
    )
    db.add(allocation)
    new_balance = calculate_invoice_balance(adjusted_total, net_paid + payload.amount)
    invoice.payment_status = new_balance["payment_status"]
    if new_balance["payment_status"] == "paid":
        invoice.paid_by = current_admin.id

    record_invoice_event(
        db, invoice.id, current_admin.id, "advance_allocated",
        {"advance_id": advance.id, "allocation_amount": str(payload.amount),
         "currency": advance.currency, "remaining": str(new_balance["outstanding_amount"])},
    )
    db.commit()
    db.refresh(allocation)
    return allocation


@router.post(
    "/advances/{advance_id}/refunds",
    response_model=InvoiceAdvanceRefundResponse,
    status_code=status.HTTP_201_CREATED,
)
def refund_invoice_advance(
    advance_id: int,
    payload: InvoiceAdvanceRefundCreateRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    advance = (
        db.query(InvoiceAdvance).filter(InvoiceAdvance.id == advance_id)
        .with_for_update().first()
    )
    if advance is None:
        raise HTTPException(status_code=404, detail="Advance not found.")

    balances = get_advance_balances(db, advance)
    if payload.amount > balances["available_amount"]:
        raise HTTPException(
            status_code=422,
            detail={"message": "Refund exceeds unallocated advance balance.",
                    "refundable": str(balances["available_amount"])},
        )

    refund = InvoiceAdvanceRefund(
        advance_id=advance.id,
        amount=payload.amount,
        refunded_at=payload.refunded_at or datetime.now(timezone.utc),
        method=payload.method.strip() if payload.method else None,
        transaction_reference=payload.transaction_reference.strip() if payload.transaction_reference else None,
        reason=payload.reason.strip() if payload.reason else None,
        created_by=current_admin.id,
    )
    db.add(refund)
    db.flush()

    record_advance_event(
        db, advance.id, current_admin.id, "advance_refunded",
        {"advance_id": advance.id, "refund_id": refund.id,
         "amount": str(payload.amount), "currency": advance.currency,
         "reason": payload.reason},
    )
    db.commit()
    db.refresh(refund)
    return refund


@router.get(
    "/advances/{advance_id}/refunds",
    response_model=list[InvoiceAdvanceRefundResponse],
)
def list_invoice_advance_refunds(
    advance_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    advance = db.query(InvoiceAdvance).filter(InvoiceAdvance.id == advance_id).first()
    if advance is None:
        raise HTTPException(status_code=404, detail="Advance not found.")
    if not current_user.is_superuser and advance.created_by != current_user.id:
        raise HTTPException(status_code=404, detail="Advance not found.")
    return (
        db.query(InvoiceAdvanceRefund)
        .filter(InvoiceAdvanceRefund.advance_id == advance_id)
        .order_by(InvoiceAdvanceRefund.refunded_at.desc(), InvoiceAdvanceRefund.id.desc())
        .all()
    )

@router.get("/{invoice_id}", response_model=InvoiceResponse)
def get_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()

    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    ensure_invoice_access(invoice, current_user)
    net_paid = get_invoice_net_paid(db, invoice.id)
    refunded = Decimal(str(
        db.query(func.coalesce(func.sum(InvoiceRefund.amount), 0))
        .filter(InvoiceRefund.invoice_id == invoice.id).scalar() or 0
    ))
    advance_paid = Decimal(str(
        db.query(func.coalesce(func.sum(InvoiceAdvanceAllocation.amount), 0))
        .filter(InvoiceAdvanceAllocation.invoice_id == invoice.id).scalar() or 0
    ))
    credited = get_invoice_credit_total(db, invoice.id)
    adjusted_total = get_invoice_adjusted_total(db, invoice)
    balance = calculate_invoice_balance(adjusted_total, net_paid)
    result = {
        column.name: getattr(invoice, column.name)
        for column in Invoice.__table__.columns
    }
    result.update({
        "paid_amount": balance["paid_amount"],
        "refunded_amount": refunded,
        "credit_note_total": credited,
        "adjusted_total": adjusted_total,
        "outstanding_amount": balance["outstanding_amount"],
        "overpaid_amount": balance["overpaid_amount"],
        "advance_paid_amount": advance_paid,
    })
    return result


@router.get("/{invoice_id}/review")
def get_invoice_review(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
    ensure_invoice_access(invoice, current_user)
    try:
        extraction = json.loads(invoice.extraction_data or "{}")
    except (TypeError, ValueError):
        extraction = {}
    validation = validate_invoice({
        **extraction,
        "supplier_name": invoice.supplier_name,
        "invoice_number": invoice.invoice_number,
        "supplier_tax_id": invoice.supplier_tax_id,
        "invoice_date": invoice.invoice_date,
        "due_date": invoice.due_date,
        "currency": invoice.currency,
        "subtotal": invoice.subtotal,
        "tax_amount": invoice.tax_amount,
        "total_amount": invoice.total_amount,
    })
    return {"extraction": extraction, "validation": validation, "ocr_text": invoice.ocr_text,
            "status": invoice.status, "requires_human_review": True}


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()

    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    changes = payload.model_dump(exclude_unset=True)

    ensure_invoice_access(invoice, current_user)

    if invoice.status != "pending_review" or invoice.payment_status != "unpaid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only unpaid invoices pending review can be edited.",
        )

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No invoice fields were provided.",
        )

    for field in ("currency", "subtotal", "tax_amount", "total_amount"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field} cannot be null.",
            )

    if "currency" in changes:
        changes["currency"] = changes["currency"].upper().strip()
        if not ISO_CURRENCY_CODE.fullmatch(changes["currency"]):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Currency must be a valid three-letter ISO 4217 code.",
            )

    try:
        saved_extraction = json.loads(invoice.extraction_data or "{}")
    except (TypeError, ValueError):
        saved_extraction = {}
    candidate = {
        **saved_extraction,
        "supplier_name": invoice.supplier_name,
        "invoice_number": invoice.invoice_number,
        "supplier_tax_id": invoice.supplier_tax_id,
        "invoice_date": invoice.invoice_date,
        "due_date": invoice.due_date,
        "currency": invoice.currency,
        "subtotal": invoice.subtotal,
        "tax_amount": invoice.tax_amount,
        "total_amount": invoice.total_amount,
    }
    candidate.update(changes)

    validation = validate_invoice(candidate)

    if not validation["valid"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Invoice data failed validation.",
                "errors": validation["errors"],
                "warnings": validation["warnings"],
            },
        )

    for field, value in changes.items():
        setattr(invoice, field, value)

    saved_extraction.update({
        key: value for key, value in changes.items() if key != "review_notes"
    })
    invoice.extraction_data = json.dumps(saved_extraction, default=str)
    record_invoice_event(db, invoice.id, current_user.id, "edited", changes)

    try:
        db.commit()
        db.refresh(invoice)
    except Exception:
        db.rollback()
        raise

    return invoice


@router.post("/{invoice_id}/approve", response_model=InvoiceResponse)
def approve_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()

    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    if invoice.status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only invoices pending review can be approved.",
        )

    try:
        saved_extraction = json.loads(invoice.extraction_data or "{}")
    except (TypeError, ValueError):
        saved_extraction = {}
    validation = validate_invoice({
        **saved_extraction,
        "supplier_name": invoice.supplier_name,
        "invoice_number": invoice.invoice_number,
        "supplier_tax_id": invoice.supplier_tax_id,
        "invoice_date": invoice.invoice_date,
        "due_date": invoice.due_date,
        "currency": invoice.currency,
        "subtotal": invoice.subtotal,
        "tax_amount": invoice.tax_amount,
        "total_amount": invoice.total_amount,
    })

    if not validation["valid"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Invoice cannot be approved.",
                "errors": validation["errors"],
            },
        )

    invoice.status = "approved"
    invoice.reviewed_by = current_admin.id
    record_invoice_event(db, invoice.id, current_admin.id, "approved")

    db.commit()
    db.refresh(invoice)

    return invoice


@router.post("/{invoice_id}/mark-paid", response_model=InvoiceResponse)
def mark_invoice_paid(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    invoice = (
        db.query(Invoice)
        .filter(Invoice.id == invoice_id)
        .with_for_update()
        .first()
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != "approved":
        raise HTTPException(status_code=409, detail="Only approved invoices can be marked as settled.")

    paid_total = get_invoice_net_paid(db, invoice.id)
    adjusted_total = get_invoice_adjusted_total(db, invoice)
    balance = calculate_invoice_balance(adjusted_total, paid_total)
    remaining = balance["outstanding_amount"]
    if remaining <= 0:
        raise HTTPException(status_code=409, detail="Invoice is already fully settled.")

    db.add(InvoicePayment(
        invoice_id=invoice.id,
        amount=remaining,
        currency=invoice.currency,
        paid_at=datetime.now(timezone.utc),
        method="manual",
        created_by=current_user.id,
    ))
    invoice.payment_status = "paid"
    invoice.paid_by = current_user.id
    event_type = "receipt_recorded" if invoice.direction == "outgoing" else "payment_recorded"
    record_invoice_event(
        db, invoice.id, current_user.id, event_type,
        {"amount": str(remaining), "currency": invoice.currency, "source": "mark-paid"},
    )
    db.commit()
    db.refresh(invoice)
    return invoice


@router.get(
    "/{invoice_id}/credit-notes",
    response_model=list[InvoiceCreditNoteResponse],
)
def list_invoice_credit_notes(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    ensure_invoice_access(invoice, current_user)
    return (
        db.query(InvoiceCreditNote)
        .filter(InvoiceCreditNote.invoice_id == invoice_id)
        .order_by(InvoiceCreditNote.issued_at.desc(), InvoiceCreditNote.id.desc())
        .all()
    )


@router.post(
    "/{invoice_id}/credit-notes",
    response_model=InvoiceCreditNoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invoice_credit_note(
    invoice_id: int,
    payload: InvoiceCreditNoteCreateRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    invoice = (
        db.query(Invoice)
        .filter(Invoice.id == invoice_id)
        .with_for_update()
        .first()
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="Credit notes can only be issued for approved invoices.",
        )

    adjusted_total = get_invoice_adjusted_total(db, invoice)
    if payload.amount > adjusted_total:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Credit note exceeds the remaining invoice amount.",
                "remaining_creditable_amount": str(adjusted_total),
            },
        )

    credit_note = InvoiceCreditNote(
        invoice_id=invoice.id,
        credit_note_number=(
            payload.credit_note_number.strip()
            if payload.credit_note_number else None
        ),
        amount=payload.amount,
        currency=invoice.currency,
        reason=payload.reason.strip(),
        is_cancellation=False,
        issued_at=payload.issued_at or datetime.now(timezone.utc),
        created_by=current_admin.id,
    )
    db.add(credit_note)
    db.flush()

    new_adjusted_total = adjusted_total - payload.amount
    net_paid = get_invoice_net_paid(db, invoice.id)
    balance = calculate_invoice_balance(new_adjusted_total, net_paid)
    invoice.payment_status = balance["payment_status"]
    if balance["payment_status"] != "paid":
        invoice.paid_by = None

    record_invoice_event(
        db,
        invoice.id,
        current_admin.id,
        "credit_note_issued",
        {
            "credit_note_id": credit_note.id,
            "credit_note_number": credit_note.credit_note_number,
            "amount": str(payload.amount),
            "currency": invoice.currency,
            "adjusted_total": str(new_adjusted_total),
            "reason": payload.reason,
        },
    )
    db.commit()
    db.refresh(credit_note)
    return credit_note


@router.post(
    "/{invoice_id}/cancel",
    response_model=InvoiceCreditNoteResponse,
    status_code=status.HTTP_201_CREATED,
)
def cancel_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    invoice = (
        db.query(Invoice)
        .filter(Invoice.id == invoice_id)
        .with_for_update()
        .first()
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="Only approved invoices can be cancelled.",
        )

    remaining_creditable = get_invoice_adjusted_total(db, invoice)
    if remaining_creditable <= 0:
        raise HTTPException(
            status_code=409,
            detail="Invoice has already been fully credited.",
        )

    credit_note = InvoiceCreditNote(
        invoice_id=invoice.id,
        amount=remaining_creditable,
        currency=invoice.currency,
        reason="Invoice cancellation (storno).",
        is_cancellation=True,
        issued_at=datetime.now(timezone.utc),
        created_by=current_admin.id,
    )
    db.add(credit_note)
    db.flush()

    invoice.status = "cancelled"
    record_invoice_event(
        db,
        invoice.id,
        current_admin.id,
        "invoice_cancelled",
        {
            "credit_note_id": credit_note.id,
            "amount": str(remaining_creditable),
            "currency": invoice.currency,
            "original_total": str(invoice.total_amount),
            "credit_note_total": str(get_invoice_credit_total(db, invoice.id)),
        },
    )
    db.commit()
    db.refresh(credit_note)
    return credit_note


@router.get("/{invoice_id}/audit", response_model=list[InvoiceAuditEventResponse])
def list_invoice_audit(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    ensure_invoice_access(invoice, current_user)
    return (
        db.query(InvoiceAuditEvent)
        .filter(InvoiceAuditEvent.invoice_id == invoice_id)
        .order_by(InvoiceAuditEvent.created_at.desc(), InvoiceAuditEvent.id.desc())
        .all()
    )


@router.get("/{invoice_id}/payments", response_model=list[InvoicePaymentResponse])
def list_invoice_payments(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    ensure_invoice_access(invoice, current_user)
    return (
        db.query(InvoicePayment)
        .filter(InvoicePayment.invoice_id == invoice_id)
        .order_by(InvoicePayment.paid_at.desc(), InvoicePayment.id.desc())
        .all()
    )



@router.get("/{invoice_id}/refunds", response_model=list[InvoiceRefundResponse])
def list_invoice_refunds(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    ensure_invoice_access(invoice, current_user)
    return (
        db.query(InvoiceRefund)
        .filter(InvoiceRefund.invoice_id == invoice_id)
        .order_by(InvoiceRefund.refunded_at.desc(), InvoiceRefund.id.desc())
        .all()
    )


@router.post(
    "/{invoice_id}/refunds",
    response_model=InvoiceRefundResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invoice_refund(
    invoice_id: int,
    payload: InvoiceRefundCreateRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    invoice = (
        db.query(Invoice)
        .filter(Invoice.id == invoice_id)
        .with_for_update()
        .first()
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status not in {"approved", "cancelled"}:
        raise HTTPException(
            status_code=409,
            detail="Refunds can only be recorded for approved or cancelled invoices.",
        )

    gross_paid = Decimal(str(
        db.query(func.coalesce(func.sum(InvoicePayment.amount), 0))
        .filter(InvoicePayment.invoice_id == invoice.id).scalar() or 0
    ))
    already_refunded = Decimal(str(
        db.query(func.coalesce(func.sum(InvoiceRefund.amount), 0))
        .filter(InvoiceRefund.invoice_id == invoice.id).scalar() or 0
    ))
    refundable = gross_paid - already_refunded
    if payload.amount > refundable:
        raise HTTPException(
            status_code=422,
            detail={"message": "Refund exceeds the refundable amount.", "refundable": str(max(Decimal("0"), refundable))},
        )

    refund = InvoiceRefund(
        invoice_id=invoice.id,
        amount=payload.amount,
        currency=invoice.currency,
        refunded_at=payload.refunded_at or datetime.now(timezone.utc),
        method=payload.method.strip() if payload.method else None,
        transaction_reference=payload.transaction_reference.strip() if payload.transaction_reference else None,
        reason=payload.reason.strip() if payload.reason else None,
        created_by=current_admin.id,
    )
    db.add(refund)

    net_paid = gross_paid - already_refunded - payload.amount
    adjusted_total = get_invoice_adjusted_total(db, invoice)
    balance = calculate_invoice_balance(adjusted_total, net_paid)
    invoice.payment_status = balance["payment_status"]
    if balance["payment_status"] != "paid":
        invoice.paid_by = None

    record_invoice_event(
        db, invoice.id, current_admin.id, "refund_recorded",
        {
            "amount": str(payload.amount),
            "currency": invoice.currency,
            "net_paid": str(net_paid),
            "outstanding": str(balance["outstanding_amount"]),
            "reason": payload.reason,
            "transaction_reference": payload.transaction_reference,
        },
    )
    db.commit()
    db.refresh(refund)
    return refund


@router.post(
    "/{invoice_id}/payments",
    response_model=InvoicePaymentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invoice_payment(
    invoice_id: int,
    payload: InvoicePaymentCreateRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    invoice = (
        db.query(Invoice)
        .filter(Invoice.id == invoice_id)
        .with_for_update()
        .first()
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if invoice.status != "approved":
        raise HTTPException(status_code=409, detail="Transactions can only be recorded for approved invoices.")

    paid_total = get_invoice_net_paid(db, invoice.id)
    adjusted_total = get_invoice_adjusted_total(db, invoice)
    balance = calculate_invoice_balance(adjusted_total, paid_total)
    paid_total = balance["paid_amount"]
    total = balance["total_amount"]
    remaining = balance["outstanding_amount"]
    amount = payload.amount
    is_advance = (payload.method or "").strip().lower() == "advance"

    if remaining <= 0 and not is_advance:
        raise HTTPException(
            status_code=409,
            detail="Invoice has no outstanding balance. Use method='advance' to record an advance.",
        )
    if amount > remaining and not is_advance:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Overpayments must be explicitly recorded as an advance.",
                "remaining": str(remaining),
            },
        )

    payment = InvoicePayment(
        invoice_id=invoice.id,
        amount=amount,
        currency=invoice.currency,
        paid_at=payload.paid_at or datetime.now(timezone.utc),
        method=payload.method.strip() if payload.method else None,
        transaction_reference=(
            payload.transaction_reference.strip()
            if payload.transaction_reference else None
        ),
        created_by=current_admin.id,
    )
    db.add(payment)

    new_balance = calculate_invoice_balance(total, paid_total + amount)
    new_paid_total = new_balance["paid_amount"]
    fully_paid = new_balance["payment_status"] == "paid"
    invoice.payment_status = new_balance["payment_status"]
    if fully_paid:
        invoice.paid_by = current_admin.id

    event_type = "receipt_recorded" if invoice.direction == "outgoing" else "payment_recorded"
    record_invoice_event(
        db, invoice.id, current_admin.id, event_type,
        {
            "amount": str(amount),
            "currency": invoice.currency,
            "method": payment.method,
            "transaction_reference": payment.transaction_reference,
            "paid_total": str(new_paid_total),
            "remaining": str(new_balance["outstanding_amount"]),
            "overpaid": str(new_balance["overpaid_amount"]),
        },
    )
    db.commit()
    db.refresh(payment)
    return payment


@router.get("/{invoice_id}/preview")
def preview_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()

    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    ensure_invoice_access(invoice, current_user)

    upload_directory = Path(settings.upload_dir).resolve()
    stored_path = Path(invoice.file_path).resolve()

    if stored_path.parent != upload_directory or not stored_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice document file is unavailable.",
        )

    extension = stored_path.suffix.lower()
    if extension in {".tif", ".tiff"}:
        try:
            with Image.open(stored_path) as source_image:
                source_image.seek(0)
                image = source_image.convert("RGB")
                output = BytesIO()
                image.save(output, format="PNG", optimize=True)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Unable to generate a preview for this TIFF document.",
            ) from exc

        return Response(
            content=output.getvalue(),
            media_type="image/png",
            headers={"Content-Disposition": "inline"},
        )

    media_types = {
        ".pdf": "application/pdf",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
    }
    media_type = media_types.get(extension)
    if not media_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Preview is not supported for this file type.",
        )

    return FileResponse(
        path=stored_path,
        media_type=media_type,
        headers={"Content-Disposition": "inline"},
    )


@router.get("/{invoice_id}/download")
def download_invoice(
    invoice_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()

    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    upload_directory = Path(settings.upload_dir).resolve()
    stored_path = Path(invoice.file_path).resolve()

    ensure_invoice_access(invoice, current_user)

    if (
        stored_path.parent != upload_directory
        or not stored_path.is_file()
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice document file is unavailable.",
        )

    return FileResponse(
        path=stored_path,
        filename=invoice.original_filename,
        media_type="application/octet-stream",
    )


@router.post("/{invoice_id}/reject", response_model=InvoiceResponse)
def reject_invoice(
    invoice_id: int,
    reason: str = Body(..., embed=True, min_length=5, max_length=5000),
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()

    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    if invoice.status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only invoices pending review can be rejected.",
        )

    reason = reason.strip()
    if len(reason) < 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Rejection reason must contain at least 5 characters.",
        )

    invoice.status = "rejected"
    invoice.reviewed_by = current_admin.id
    invoice.review_notes = reason
    record_invoice_event(
        db, invoice.id, current_admin.id, "rejected", {"reason": reason}
    )

    db.commit()
    db.refresh(invoice)

    return invoice
