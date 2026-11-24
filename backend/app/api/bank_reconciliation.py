import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.bank_reconciliation import (
    BankAccount, BankAuditEvent, BankReconciliation, BankTransaction,
)
from app.models.financial_record import FinancialRecord
from app.models.user import User
from app.schemas.bank_reconciliation import (
    BankAccountCreate, BankAccountUpdate, BankMatchRequest,
    BankReconciliationCreate, BankTransactionCreate,
)
from app.services.bank_reconciliation import (
    csv_rows, matching_candidates, parse_csv_transaction, transaction_fingerprint,
)

router = APIRouter(prefix="/banking", tags=["Banking & reconciliation"])


def visible_accounts(db, user):
    q = db.query(BankAccount)
    if not user.is_superuser:
        q = q.filter(BankAccount.owner_id == user.id)
    return q


def get_account(db, account_id, user):
    item = visible_accounts(db, user).filter(BankAccount.id == account_id).first()
    if not item:
        raise HTTPException(404, "Bank account not found.")
    return item


def get_transaction(db, transaction_id, user):
    q = db.query(BankTransaction).filter(BankTransaction.id == transaction_id)
    if not user.is_superuser:
        q = q.filter(BankTransaction.owner_id == user.id)
    item = q.first()
    if not item:
        raise HTTPException(404, "Bank transaction not found.")
    return item


def audit(db, account_id, user, event, transaction_id=None, reconciliation_id=None, details=None):
    db.add(BankAuditEvent(
        account_id=account_id,
        transaction_id=transaction_id,
        reconciliation_id=reconciliation_id,
        user_id=user.id,
        event_type=event,
        details=details or {},
    ))


def account_dict(item):
    return {
        "id": item.id,
        "name": item.name,
        "bank_name": item.bank_name,
        "iban": item.iban,
        "account_number": item.account_number,
        "currency": item.currency,
        "opening_balance": str(item.opening_balance),
        "active": item.active,
        "owner_id": item.owner_id,
    }


def transaction_dict(item):
    return {
        "id": item.id,
        "account_id": item.account_id,
        "transaction_date": item.transaction_date.isoformat(),
        "value_date": item.value_date.isoformat() if item.value_date else None,
        "amount": str(item.amount),
        "currency": item.currency,
        "description": item.description,
        "counterparty": item.counterparty,
        "reference": item.reference,
        "external_id": item.external_id,
        "status": item.status,
        "matched_record_id": item.matched_record_id,
        "import_batch_id": item.import_batch_id,
    }


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [account_dict(x) for x in visible_accounts(db, user).order_by(BankAccount.name).all()]


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def create_account(
    payload: BankAccountCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    key = (payload.iban or payload.account_number or payload.name).strip().lower()
    item = BankAccount(**payload.model_dump(), owner_id=user.id, account_key=key)
    db.add(item)
    try:
        db.flush()
        audit(db, item.id, user, "account_created")
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "A bank account with this identifier already exists.") from exc
    return account_dict(item)


@router.patch("/accounts/{account_id}")
def update_account(
    account_id: int,
    payload: BankAccountUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_account(db, account_id, user)
    changes = payload.model_dump(exclude_unset=True)
    if "currency" in changes:
        changes["currency"] = changes["currency"].upper()
    for key, value in changes.items():
        setattr(item, key, value)
    audit(db, item.id, user, "account_updated", details={"changed": list(changes)})
    db.commit()
    db.refresh(item)
    return account_dict(item)


@router.get("/accounts/{account_id}/transactions")
def list_transactions(
    account_id: int,
    status_filter: str | None = Query(default=None, alias="status"),
    start_date: date | None = None,
    end_date: date | None = None,
    q: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = get_account(db, account_id, user)
    query = db.query(BankTransaction).filter(BankTransaction.account_id == account.id)
    if status_filter:
        query = query.filter(BankTransaction.status == status_filter)
    if start_date:
        query = query.filter(BankTransaction.transaction_date >= start_date)
    if end_date:
        query = query.filter(BankTransaction.transaction_date <= end_date)
    if q:
        term = f"%{q.strip()}%"
        query = query.filter(
            BankTransaction.description.ilike(term)
            | BankTransaction.counterparty.ilike(term)
            | BankTransaction.reference.ilike(term)
        )
    rows = query.order_by(
        BankTransaction.transaction_date.desc(), BankTransaction.id.desc()
    ).all()
    return {"items": [transaction_dict(x) for x in rows], "total": len(rows)}


@router.post("/accounts/{account_id}/transactions", status_code=status.HTTP_201_CREATED)
def create_transaction(
    account_id: int,
    payload: BankTransactionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = get_account(db, account_id, user)
    fingerprint = transaction_fingerprint(
        payload.transaction_date, payload.amount, payload.currency,
        payload.description, payload.counterparty, payload.reference,
        payload.external_id,
    )
    if db.query(BankTransaction).filter(
        BankTransaction.account_id == account.id,
        BankTransaction.transaction_fingerprint == fingerprint,
    ).first():
        raise HTTPException(409, "Duplicate bank transaction.")

    item = BankTransaction(
        account_id=account.id,
        owner_id=user.id,
        transaction_fingerprint=fingerprint,
        **payload.model_dump(),
    )
    db.add(item)
    try:
        db.flush()
        audit(db, account.id, user, "transaction_created", item.id)
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Duplicate bank transaction.") from exc
    return transaction_dict(item)


@router.post("/accounts/{account_id}/import-csv")
async def import_csv(
    account_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = get_account(db, account_id, user)
    filename = (file.filename or "").lower()
    if not filename.endswith(".csv"):
        raise HTTPException(415, "Only CSV bank statements are supported.")
    data = await file.read(10 * 1024 * 1024 + 1)
    await file.close()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "CSV file exceeds 10 MB.")
    try:
        rows = csv_rows(data)
    except Exception as exc:
        raise HTTPException(422, f"Could not parse CSV: {exc}") from exc

    batch = uuid.uuid4().hex
    created = 0
    duplicates = 0
    errors = []

    for index, row in enumerate(rows, start=2):
        try:
            parsed = parse_csv_transaction(row, account.currency)
            fingerprint = transaction_fingerprint(
                parsed["transaction_date"], parsed["amount"], parsed["currency"],
                parsed["description"], parsed["counterparty"],
                parsed["reference"], parsed["external_id"],
            )
            exists = db.query(BankTransaction).filter(
                BankTransaction.account_id == account.id,
                BankTransaction.transaction_fingerprint == fingerprint,
            ).first()
            if exists:
                duplicates += 1
                continue

            db.add(BankTransaction(
                account_id=account.id,
                owner_id=user.id,
                transaction_fingerprint=fingerprint,
                import_batch_id=batch,
                **parsed,
            ))
            created += 1
        except Exception as exc:
            errors.append({"row": index, "error": str(exc)})

    if created:
        audit(db, account.id, user, "statement_imported", details={
            "batch_id": batch, "created": created, "duplicates": duplicates,
            "errors": len(errors),
        })
        db.commit()
    else:
        db.rollback()

    return {
        "batch_id": batch,
        "created": created,
        "duplicates": duplicates,
        "errors": errors[:50],
        "total_rows": len(rows),
    }


@router.get("/transactions/{transaction_id}/matches")
def transaction_matches(
    transaction_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_transaction(db, transaction_id, user)
    return {"transaction": transaction_dict(item), "candidates": matching_candidates(db, item)}


@router.post("/transactions/{transaction_id}/match")
def match_transaction(
    transaction_id: int,
    payload: BankMatchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_transaction(db, transaction_id, user)
    record = db.query(FinancialRecord).filter(
        FinancialRecord.id == payload.financial_record_id,
        FinancialRecord.owner_id == user.id if not user.is_superuser else True,
    ).first()
    if not record:
        raise HTTPException(404, "Financial record not found.")

    expected_type = "income" if item.amount > 0 else "expense"
    if record.record_type != expected_type:
        raise HTTPException(422, "Transaction direction does not match the financial record.")

    if record.currency != item.currency:
        raise HTTPException(422, "Transaction currency does not match the financial record.")

    if item.status == "matched":
        raise HTTPException(409, "Transaction is already matched.")

    item.matched_record_id = record.id
    item.status = "matched"
    audit(
        db, item.account_id, user, "transaction_matched", item.id,
        details={"financial_record_id": record.id},
    )
    db.commit()
    db.refresh(item)
    return transaction_dict(item)


@router.post("/transactions/{transaction_id}/unmatch")
def unmatch_transaction(
    transaction_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_transaction(db, transaction_id, user)
    old = item.matched_record_id
    item.matched_record_id = None
    item.status = "unmatched"
    audit(
        db, item.account_id, user, "transaction_unmatched", item.id,
        details={"financial_record_id": old},
    )
    db.commit()
    return transaction_dict(item)


@router.post("/transactions/{transaction_id}/ignore")
def ignore_transaction(
    transaction_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_transaction(db, transaction_id, user)
    item.matched_record_id = None
    item.status = "ignored"
    audit(db, item.account_id, user, "transaction_ignored", item.id)
    db.commit()
    return transaction_dict(item)


@router.post("/transactions/{transaction_id}/unignore")
def unignore_transaction(
    transaction_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = get_transaction(db, transaction_id, user)
    if item.status != "ignored":
        return transaction_dict(item)
    item.status = "unmatched"
    audit(db, item.account_id, user, "transaction_unignored", item.id)
    db.commit()
    return transaction_dict(item)


@router.post("/accounts/{account_id}/reconcile")
def reconcile(
    account_id: int,
    payload: BankReconciliationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = get_account(db, account_id, user)
    if payload.period_end < payload.period_start:
        raise HTTPException(422, "Period end must be on or after period start.")

    total = db.query(func.coalesce(func.sum(BankTransaction.amount), 0)).filter(
        BankTransaction.account_id == account.id,
        BankTransaction.transaction_date >= payload.period_start,
        BankTransaction.transaction_date <= payload.period_end,
    ).scalar()

    total = Decimal(str(total or 0))
    calculated = Decimal(str(account.opening_balance)) + total
    difference = Decimal(str(payload.statement_closing_balance)) - calculated
    status_value = "reconciled" if difference == 0 else "difference"

    existing = db.query(BankReconciliation).filter(
        BankReconciliation.account_id == account.id,
        BankReconciliation.period_start == payload.period_start,
        BankReconciliation.period_end == payload.period_end,
    ).first()

    values = {
        "opening_balance": account.opening_balance,
        "statement_closing_balance": payload.statement_closing_balance,
        "calculated_closing_balance": calculated,
        "difference": difference,
        "status": status_value,
        "note": payload.note,
    }

    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        reconciliation = existing
        event = "reconciliation_updated"
    else:
        reconciliation = BankReconciliation(
            account_id=account.id,
            period_start=payload.period_start,
            period_end=payload.period_end,
            **values,
        )
        db.add(reconciliation)
        db.flush()
        event = "reconciliation_created"

    audit(
        db, account.id, user, event,
        reconciliation_id=reconciliation.id,
        details={
            "status": status_value,
            "difference": str(difference),
        },
    )
    db.commit()
    db.refresh(reconciliation)

    return {
        "id": reconciliation.id,
        "account_id": account.id,
        "period_start": payload.period_start.isoformat(),
        "period_end": payload.period_end.isoformat(),
        "opening_balance": str(reconciliation.opening_balance),
        "statement_closing_balance": str(reconciliation.statement_closing_balance),
        "calculated_closing_balance": str(reconciliation.calculated_closing_balance),
        "difference": str(reconciliation.difference),
        "status": reconciliation.status,
        "note": reconciliation.note,
    }


@router.get("/accounts/{account_id}/reconciliations")
def list_reconciliations(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = get_account(db, account_id, user)
    rows = db.query(BankReconciliation).filter(
        BankReconciliation.account_id == account.id
    ).order_by(BankReconciliation.period_end.desc()).all()
    return [{
        "id": x.id,
        "period_start": x.period_start.isoformat(),
        "period_end": x.period_end.isoformat(),
        "opening_balance": str(x.opening_balance),
        "statement_closing_balance": str(x.statement_closing_balance),
        "calculated_closing_balance": str(x.calculated_closing_balance),
        "difference": str(x.difference),
        "status": x.status,
        "note": x.note,
    } for x in rows]


@router.get("/accounts/{account_id}/audit")
def account_audit(
    account_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = get_account(db, account_id, user)
    rows = db.query(BankAuditEvent).filter(
        BankAuditEvent.account_id == account.id
    ).order_by(BankAuditEvent.created_at.desc()).limit(200).all()
    return [{
        "id": x.id,
        "transaction_id": x.transaction_id,
        "reconciliation_id": x.reconciliation_id,
        "event_type": x.event_type,
        "details": x.details or {},
        "user_id": x.user_id,
        "created_at": x.created_at.isoformat(),
    } for x in rows]
