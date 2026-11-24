from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.services.receivables_payables import build_receivables_payables

router = APIRouter(
    prefix="/api/receivables-payables",
    tags=["Receivables & Payables"],
)


def _resolve_db():
    from app.database import get_db
    return get_db


def _resolve_current_user():
    candidates = (
        ("app.auth.dependencies", "get_current_user"),
        ("app.dependencies.auth", "get_current_user"),
        ("app.api.dependencies", "get_current_user"),
    )

    for module_name, function_name in candidates:
        try:
            module = __import__(module_name, fromlist=[function_name])
            return getattr(module, function_name)
        except Exception:
            continue

    return None


def _owner_id(user):
    if user is None:
        return None

    if getattr(user, "is_superuser", False):
        return None

    return getattr(user, "id", None)


@router.get("")
def receivables_payables(
    db: Session = Depends(_resolve_db()),
    current_user=Depends(_resolve_current_user()) if _resolve_current_user() else None,
    direction: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    aging: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    as_of: Optional[date] = Query(None),
):
    return build_receivables_payables(
        db,
        owner_id=_owner_id(current_user),
        direction=direction,
        status=status,
        aging=aging,
        search=search,
        as_of=as_of,
    )
