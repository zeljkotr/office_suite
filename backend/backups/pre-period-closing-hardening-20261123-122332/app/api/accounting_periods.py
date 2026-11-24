from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
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
        "record_date", "transaction_date", "invoice_date", "tax_date",
        "payment_date", "effective_date",
    )

    for table in Base.metadata.sorted_tables:
        owner_column = table.c.get("owner_id") or table.c.get("uploaded_by")
        if owner_column is None:
            continue

        date_column = next(
            (table.c.get(field) for field in date_fields if table.c.get(field) is not None),
            None,
        )
        if date_column is None:
            continue

        try:
            count = db.execute(
                select(table.c.id).where(
                    owner_column == owner_id,
                    date_column >= start_date,
                    date_column <= end_date,
                )
            ).all()
            if count:
                result[table.name] = len(count)
        except Exception:
            # A table that cannot be summarized must not prevent period closing.
            continue

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
