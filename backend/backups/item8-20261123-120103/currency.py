from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.currency import CurrencySettings, CurrencyRate
from app.schemas.currency import CurrencySettingsUpdate, CurrencyRateCreate
from app.services.currency import (
    get_base_currency, convert_amount, fx_report,
)

router = APIRouter(prefix="/currency", tags=["Multi-currency"])


@router.get("/settings")
def read_settings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return {"base_currency": get_base_currency(db, user.id)}


@router.put("/settings")
def update_settings(
    payload: CurrencySettingsUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = db.query(CurrencySettings).filter(
        CurrencySettings.owner_id == user.id
    ).first()

    if item is None:
        item = CurrencySettings(owner_id=user.id)
        db.add(item)

    item.base_currency = payload.base_currency
    db.commit()
    return {"base_currency": item.base_currency}


@router.get("/rates")
def list_rates(
    from_currency: str | None = None,
    to_currency: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(CurrencyRate).filter(CurrencyRate.owner_id == user.id)

    if from_currency:
        query = query.filter(
            CurrencyRate.from_currency == from_currency.upper()
        )
    if to_currency:
        query = query.filter(CurrencyRate.to_currency == to_currency.upper())
    if start_date:
        query = query.filter(CurrencyRate.effective_date >= start_date)
    if end_date:
        query = query.filter(CurrencyRate.effective_date <= end_date)

    rows = query.order_by(
        CurrencyRate.effective_date.desc(), CurrencyRate.id.desc()
    ).all()

    return [{
        "id": row.id,
        "from_currency": row.from_currency,
        "to_currency": row.to_currency,
        "effective_date": row.effective_date.isoformat(),
        "rate": str(row.rate),
    } for row in rows]


@router.post("/rates", status_code=201)
def create_rate(
    payload: CurrencyRateCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.from_currency == payload.to_currency:
        raise HTTPException(422, "Source and target currencies must differ.")

    item = CurrencyRate(owner_id=user.id, **payload.model_dump())
    db.add(item)

    try:
        db.commit()
        db.refresh(item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "An exchange rate already exists for this pair and date."
        ) from exc

    return {
        "id": item.id,
        "from_currency": item.from_currency,
        "to_currency": item.to_currency,
        "effective_date": item.effective_date.isoformat(),
        "rate": str(item.rate),
    }


@router.get("/convert")
def convert(
    amount: Decimal = Query(gt=0),
    from_currency: str = Query(min_length=3, max_length=3),
    to_currency: str = Query(min_length=3, max_length=3),
    on_date: date = Query(default_factory=date.today),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result, rate = convert_amount(
            db, user.id, amount,
            from_currency.upper(), to_currency.upper(), on_date,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    return {
        "amount": str(amount),
        "from_currency": from_currency.upper(),
        "to_currency": to_currency.upper(),
        "date": on_date.isoformat(),
        "rate": str(rate),
        "converted_amount": str(result.quantize(Decimal("0.01"))),
    }


@router.get("/fx-report")
def get_fx_report(
    as_of_date: date = Query(default_factory=date.today),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return fx_report(db, user.id, as_of_date)
