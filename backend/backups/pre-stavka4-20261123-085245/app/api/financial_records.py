import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import settings
from app.database import get_db
from app.models.financial_record import (
    FinancialRecord, FinancialRecordAuditEvent, FinancialRecordPayment
)
from app.models.invoice import Invoice
from app.models.user import User
from app.schemas.financial_record import (
    FinancialPaymentCreate, FinancialRecordCreate, FinancialRecordUpdate
)
from app.services.financial_records import payment_totals, financial_status

router = APIRouter(prefix="/financial-records", tags=["Financial records"])
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def visible_query(db: Session, user: User):
    query = db.query(FinancialRecord)
    if not user.is_superuser:
        query = query.filter(FinancialRecord.owner_id == user.id)
    return query


def get_visible_record(db: Session, record_id: int, user: User):
    item = visible_query(db, user).filter(FinancialRecord.id == record_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Financial record not found.")
    return item


def duplicate_fingerprint(payload):
    data = {
        "record_type": payload.record_type,
        "record_date": payload.record_date.isoformat(),
        "amount": str(payload.amount),
        "currency": payload.currency.upper(),
        "category": payload.category.strip().lower(),
        "counterparty": (payload.counterparty or "").strip().lower(),
        "document_number": (payload.document_number or "").strip().lower(),
        "invoice_id": payload.invoice_id,
    }
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def audit(db, item, user, event, details=None):
    db.add(FinancialRecordAuditEvent(
        record_id=item.id,
        user_id=user.id,
        event_type=event,
        details=details or {},
        created_at=datetime.now(timezone.utc),
    ))


def serialize(db, item):
    paid = payment_totals(db, item.id)
    return {
        "id": item.id,
        "record_type": item.record_type,
        "record_date": item.record_date.isoformat(),
        "amount": str(item.amount),
        "currency": item.currency,
        "category": item.category,
        "counterparty": item.counterparty,
        "description": item.description,
        "payment_method": item.payment_method,
        "document_number": item.document_number,
        "invoice_id": item.invoice_id,
        "attachments": item.attachments or [],
        "paid_amount": str(paid),
        "outstanding_amount": str(max(Decimal("0"), Decimal(item.amount) - paid)),
        "payment_status": financial_status(item.amount, paid),
        "owner_id": item.owner_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


@router.get("")
def list_financial_records(
    record_type: str | None = Query(default=None, pattern="^(income|expense)$"),
    start_date: date | None = None,
    end_date: date | None = None,
    category: str | None = Query(default=None, max_length=100),
    currency: str | None = Query(default=None, min_length=3, max_length=3),
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = visible_query(db, user)
    if record_type:
        query = query.filter(FinancialRecord.record_type == record_type)
    if start_date:
        query = query.filter(FinancialRecord.record_date >= start_date)
    if end_date:
        query = query.filter(FinancialRecord.record_date <= end_date)
    if category:
        query = query.filter(FinancialRecord.category == category)
    if currency:
        query = query.filter(FinancialRecord.currency == currency.upper())
    if q:
        term = f"%{q.strip()}%"
        query = query.filter(
            (FinancialRecord.description.ilike(term))
            | (FinancialRecord.counterparty.ilike(term))
            | (FinancialRecord.document_number.ilike(term))
        )
    total = query.count()
    items = query.order_by(
        FinancialRecord.record_date.desc(), FinancialRecord.id.desc()
    ).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [serialize(db, item) for item in items],
        "total": total, "page": page, "page_size": page_size,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_financial_record(
    payload: FinancialRecordCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.invoice_id is not None:
        invoice = db.query(Invoice).filter(Invoice.id == payload.invoice_id).first()
        if invoice is None or (not user.is_superuser and invoice.uploaded_by != user.id):
            raise HTTPException(status_code=404, detail="Linked invoice not found.")

    document_number = payload.document_number.strip() if payload.document_number else None
    if document_number:
        duplicate = visible_query(db, user).filter(
            FinancialRecord.record_type == payload.record_type,
            func.lower(FinancialRecord.document_number) == document_number.lower(),
        ).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="A record with this document number already exists.")

    item = FinancialRecord(
        **payload.model_dump(exclude={"document_number"}),
        document_number=document_number,
        duplicate_fingerprint=duplicate_fingerprint(payload),
        owner_id=user.id,
    )
    db.add(item)
    try:
        db.flush()
        audit(db, item, user, "created")
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate financial record.") from exc
    return serialize(db, item)


@router.get("/summary")
def financial_summary(
    start_date: date | None = None,
    end_date: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = visible_query(db, user)
    if start_date:
        query = query.filter(FinancialRecord.record_date >= start_date)
    if end_date:
        query = query.filter(FinancialRecord.record_date <= end_date)

    result = {}
    for kind in ("income", "expense"):
        rows = query.filter(FinancialRecord.record_type == kind).with_entities(
            FinancialRecord.currency,
            func.sum(FinancialRecord.amount),
        ).group_by(FinancialRecord.currency).all()
        result[kind] = {currency: str(amount or 0) for currency, amount in rows}

    currencies = set(result["income"]) | set(result["expense"])
    result["net"] = {
        currency: str(
            Decimal(result["income"].get(currency, "0"))
            - Decimal(result["expense"].get(currency, "0"))
        )
        for currency in currencies
    }
    result["generated_on"] = date.today().isoformat()
    return result


@router.get("/{record_id}")
def get_financial_record(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return serialize(db, get_visible_record(db, record_id, user))


@router.patch("/{record_id}")
def update_financial_record(
    record_id: int,
    payload: FinancialRecordUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_visible_record(db, record_id, user)
    changes = payload.model_dump(exclude_unset=True)
    if "amount" in changes:
        already_paid = payment_totals(db, item.id)
        if Decimal(changes["amount"]) < already_paid:
            raise HTTPException(
                status_code=409,
                detail="Amount cannot be lower than payments already recorded.",
            )
    if "invoice_id" in changes and changes["invoice_id"] is not None:
        invoice = db.query(Invoice).filter(Invoice.id == changes["invoice_id"]).first()
        if invoice is None or (not user.is_superuser and invoice.uploaded_by != user.id):
            raise HTTPException(status_code=404, detail="Linked invoice not found.")
    candidate = {
        "record_type": item.record_type,
        "record_date": changes.get("record_date", item.record_date),
        "amount": changes.get("amount", item.amount),
        "currency": changes.get("currency", item.currency),
        "category": changes.get("category", item.category),
        "counterparty": changes.get("counterparty", item.counterparty),
        "document_number": changes.get("document_number", item.document_number),
        "invoice_id": changes.get("invoice_id", item.invoice_id),
    }
    raw = json.dumps({
        "record_type": candidate["record_type"],
        "record_date": candidate["record_date"].isoformat(),
        "amount": str(candidate["amount"]),
        "currency": str(candidate["currency"]).upper(),
        "category": str(candidate["category"]).strip().lower(),
        "counterparty": str(candidate["counterparty"] or "").strip().lower(),
        "document_number": str(candidate["document_number"] or "").strip().lower(),
        "invoice_id": candidate["invoice_id"],
    }, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(raw.encode()).hexdigest()

    duplicate = visible_query(db, user).filter(
        FinancialRecord.duplicate_fingerprint == fingerprint,
        FinancialRecord.id != item.id,
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="An identical financial record already exists.")

    old = {key: str(getattr(item, key)) for key in changes}
    for key, value in changes.items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(item, key, value)
    item.duplicate_fingerprint = fingerprint
    audit(db, item, user, "updated", {"before": old, "changed": list(changes)})
    db.commit()
    db.refresh(item)
    return serialize(db, item)


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_financial_record(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_visible_record(db, record_id, user)
    raise HTTPException(
        status_code=409,
        detail="Financial records are retained for audit integrity and cannot be permanently deleted.",
    )


@router.post("/{record_id}/payments", status_code=status.HTTP_201_CREATED)
def add_financial_payment(
    record_id: int,
    payload: FinancialPaymentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_visible_record(db, record_id, user)
    paid = payment_totals(db, item.id)
    remaining = Decimal(item.amount) - paid
    if payload.amount > remaining:
        raise HTTPException(status_code=422, detail="Payment exceeds the outstanding amount.")
    payment = FinancialRecordPayment(
        record_id=item.id,
        owner_id=user.id,
        **payload.model_dump(),
    )
    db.add(payment)
    audit(db, item, user, "payment_added", {"amount": str(payload.amount)})
    db.commit()
    db.refresh(payment)
    return {
        "id": payment.id,
        "record_id": payment.record_id,
        "amount": str(payment.amount),
        "payment_date": payment.payment_date.isoformat(),
        "payment_method": payment.payment_method,
        "reference": payment.reference,
        "notes": payment.notes,
    }


@router.get("/{record_id}/payments")
def list_financial_payments(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_visible_record(db, record_id, user)
    rows = db.query(FinancialRecordPayment).filter(
        FinancialRecordPayment.record_id == item.id
    ).order_by(FinancialRecordPayment.payment_date.desc()).all()
    return [{
        "id": row.id, "amount": str(row.amount),
        "payment_date": row.payment_date.isoformat(),
        "payment_method": row.payment_method,
        "reference": row.reference, "notes": row.notes,
    } for row in rows]


@router.get("/{record_id}/audit")
def list_financial_audit(
    record_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_visible_record(db, record_id, user)
    rows = db.query(FinancialRecordAuditEvent).filter(
        FinancialRecordAuditEvent.record_id == item.id
    ).order_by(FinancialRecordAuditEvent.created_at.desc()).all()
    return [{
        "id": row.id,
        "event_type": row.event_type,
        "details": row.details or {},
        "user_id": row.user_id,
        "created_at": row.created_at.isoformat(),
    } for row in rows]


@router.post("/{record_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_financial_attachment(
    record_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_visible_record(db, record_id, user)
    original = (file.filename or "attachment").replace("\\", "/").split("/")[-1]
    extension = Path(original).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported attachment type.")
    limit = settings.max_upload_size_mb * 1024 * 1024
    data = await file.read(limit + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=400, detail="Attachment is empty.")
    if len(data) > limit:
        raise HTTPException(status_code=413, detail="Attachment exceeds the upload size limit.")

    signatures = {
        ".pdf": lambda b: b.startswith(b"%PDF-"),
        ".jpg": lambda b: b.startswith(b"\xff\xd8\xff"),
        ".jpeg": lambda b: b.startswith(b"\xff\xd8\xff"),
        ".png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
        ".tif": lambda b: b.startswith((b"II*\x00", b"MM\x00*")),
        ".tiff": lambda b: b.startswith((b"II*\x00", b"MM\x00*")),
    }
    if not signatures[extension](data):
        raise HTTPException(status_code=415, detail="File content does not match its extension.")

    directory = Path(settings.upload_dir).resolve() / "financial-records"
    directory.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{extension}"
    path = directory / stored
    path.write_bytes(data)
    entry = {"filename": original[:255], "stored_filename": stored, "size": len(data)}
    item.attachments = list(item.attachments or []) + [entry]
    audit(db, item, user, "attachment_added", {"filename": original[:255]})
    try:
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return entry


@router.get("/{record_id}/attachments/{stored_filename}")
def download_financial_attachment(
    record_id: int,
    stored_filename: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_visible_record(db, record_id, user)
    entry = next(
        (x for x in (item.attachments or []) if x.get("stored_filename") == stored_filename),
        None,
    )
    if entry is None or Path(stored_filename).name != stored_filename:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    directory = (Path(settings.upload_dir).resolve() / "financial-records").resolve()
    path = (directory / stored_filename).resolve()
    if path.parent != directory or not path.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found.")
    return FileResponse(path, filename=entry["filename"])
