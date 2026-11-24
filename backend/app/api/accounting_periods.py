from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_user
from app.models.accounting_period import AccountingPeriod, AccountingPeriodAudit
from app.models.base import Base

router = APIRouter(prefix="/accounting-periods", tags=["Accounting Periods"])


def _owner(user):
    return user.id


def _serialize(period):
    return {
        "id": period.id,
        "name": period.name,
        "period_type": period.period_type,
        "start_date": period.start_date.isoformat(),
        "end_date": period.end_date.isoformat(),
        "status": period.status,
        "snapshot": period.snapshot or {},
        "created_at": period.created_at.isoformat() if period.created_at else None,
        "closed_at": period.closed_at.isoformat() if period.closed_at else None,
        "reopened_at": period.reopened_at.isoformat() if period.reopened_at else None,
    }


def _get_period(db, user, period_id):
    period = db.execute(
        select(AccountingPeriod).where(
            AccountingPeriod.id == period_id,
            AccountingPeriod.owner_id == _owner(user),
        )
    ).scalar_one_or_none()
    if period is None:
        raise HTTPException(status_code=404, detail="Accounting period not found")
    return period


def _snapshot(db, owner_id, start_date, end_date):
    result = {}
    date_fields = (
        "record_date", "transaction_date", "value_date", "invoice_date",
        "tax_date", "payment_date", "effective_date", "paid_at",
        "refunded_at", "issued_at", "received_at", "allocated_at",
    )

    for table in Base.metadata.sorted_tables:
        date_column = next(
            (table.c.get(field) for field in date_fields if table.c.get(field) is not None),
            None,
        )
        if date_column is None:
            continue

        owner_conditions = []
        if table.c.get("owner_id") is not None:
            owner_conditions.append(table.c.owner_id == owner_id)
        elif table.c.get("uploaded_by") is not None:
            owner_conditions.append(table.c.uploaded_by == owner_id)
        else:
            invoice_id = table.c.get("invoice_id")
            invoice_table = Base.metadata.tables.get("invoices")
            if invoice_id is not None and invoice_table is not None:
                owner_conditions.append(
                    invoice_id.in_(
                        select(invoice_table.c.id).where(
                            invoice_table.c.uploaded_by == owner_id
                        )
                    )
                )

            advance_id = table.c.get("advance_id")
            advance_table = Base.metadata.tables.get("invoice_advances")
            if advance_id is not None and advance_table is not None:
                owner_conditions.append(
                    advance_id.in_(
                        select(advance_table.c.id).where(
                            advance_table.c.created_by == owner_id
                        )
                    )
                )

            if not owner_conditions and table.c.get("created_by") is not None:
                owner_conditions.append(table.c.created_by == owner_id)

        if not owner_conditions:
            continue

        count = db.execute(
            select(func.count()).select_from(table).where(
                or_(*owner_conditions),
                date_column >= start_date,
                date_column <= end_date,
            )
        ).scalar_one()
        result[table.name] = int(count)

    return result

@router.get("")
def list_periods(
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    periods = db.execute(
        select(AccountingPeriod)
        .where(AccountingPeriod.owner_id == _owner(user))
        .order_by(AccountingPeriod.start_date.desc())
    ).scalars().all()
    return [_serialize(period) for period in periods]


@router.post("")
def create_period(
    payload: dict,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    try:
        start_date = date.fromisoformat(str(payload.get("start_date", "")))
        end_date = date.fromisoformat(str(payload.get("end_date", "")))
    except ValueError:
        raise HTTPException(status_code=422, detail="Valid start_date and end_date are required")

    if end_date < start_date:
        raise HTTPException(status_code=422, detail="End date must not precede start date")

    owner_id = _owner(user)
    overlap = db.execute(
        select(AccountingPeriod).where(
            AccountingPeriod.owner_id == owner_id,
            AccountingPeriod.start_date <= end_date,
            AccountingPeriod.end_date >= start_date,
        ).limit(1)
    ).scalar_one_or_none()

    if overlap:
        raise HTTPException(
            status_code=409,
            detail=f"Date range overlaps existing period '{overlap.name}'",
        )

    name = str(payload.get("name") or "").strip()
    if not name:
        name = f"{start_date.isoformat()} – {end_date.isoformat()}"

    period = AccountingPeriod(
        owner_id=owner_id,
        name=name[:120],
        period_type=str(payload.get("period_type") or "custom")[:20],
        start_date=start_date,
        end_date=end_date,
        status="open",
        snapshot={},
    )
    db.add(period)
    db.flush()
    db.add(AccountingPeriodAudit(
        period_id=period.id,
        owner_id=owner_id,
        action="created",
        snapshot={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
    ))
    db.commit()
    db.refresh(period)
    return _serialize(period)


@router.get("/{period_id}/preview")
def preview_period(
    period_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    period = _get_period(db, user, period_id)
    snapshot = _snapshot(
        db, period.owner_id, period.start_date, period.end_date
    )
    return {
        "period": _serialize(period),
        "record_counts": snapshot,
        "total_records": sum(snapshot.values()),
        "ready_to_close": period.status == "open",
    }


@router.post("/{period_id}/close")
def close_period(
    period_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    period = _get_period(db, user, period_id)
    if period.status != "open":
        raise HTTPException(status_code=409, detail="Period is not open")

    snapshot = _snapshot(db, period.owner_id, period.start_date, period.end_date)
    now = datetime.now(timezone.utc)
    period.snapshot = snapshot
    period.status = "closed"
    period.closed_at = now
    db.add(AccountingPeriodAudit(
        period_id=period.id,
        owner_id=period.owner_id,
        action="closed",
        snapshot=snapshot,
    ))
    db.commit()
    db.refresh(period)
    return _serialize(period)


@router.post("/{period_id}/reopen")
def reopen_period(
    period_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    period = _get_period(db, user, period_id)
    if period.status != "closed":
        raise HTTPException(status_code=409, detail="Period is not closed")

    reason = str(payload.get("reason") or "").strip()
    if len(reason) < 5:
        raise HTTPException(status_code=422, detail="A reopening reason of at least 5 characters is required")

    now = datetime.now(timezone.utc)
    period.status = "open"
    period.reopened_at = now
    db.add(AccountingPeriodAudit(
        period_id=period.id,
        owner_id=period.owner_id,
        action="reopened",
        reason=reason[:2000],
        snapshot=period.snapshot or {},
    ))
    db.commit()
    db.refresh(period)
    return _serialize(period)


@router.get("/{period_id}/audit")
def period_audit(
    period_id: int,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    period = _get_period(db, user, period_id)
    events = db.execute(
        select(AccountingPeriodAudit)
        .where(
            AccountingPeriodAudit.period_id == period.id,
            AccountingPeriodAudit.owner_id == _owner(user),
        )
        .order_by(AccountingPeriodAudit.created_at.desc())
    ).scalars().all()

    return [{
        "action": item.action,
        "reason": item.reason,
        "snapshot": item.snapshot,
        "created_at": item.created_at.isoformat(),
    } for item in events]
