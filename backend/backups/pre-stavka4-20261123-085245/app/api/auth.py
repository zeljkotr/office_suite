from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from app.models.user import User
from app.services.security import hash_password
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.schemas.auth import TokenResponse, UserResponse
from app.services.security import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["Authentication"])



class FirstRunSetupRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)


@router.get("/setup/status")
def setup_status(db: Session = Depends(get_db)):
    exists = db.scalar(
        select(User.id).where(User.is_superuser.is_(True)).limit(1)
    ) is not None
    return {"setup_required": not exists}


@router.post("/setup")
def first_run_setup(
    payload: FirstRunSetupRequest,
    db: Session = Depends(get_db),
):
    existing_admin = db.scalar(
        select(User.id).where(User.is_superuser.is_(True)).limit(1)
    )
    if existing_admin is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Initial administrator has already been created.",
        )

    existing_email = db.scalar(
        select(User.id).where(User.email == payload.email).limit(1)
    )
    if existing_email is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    admin = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        is_active=True,
        is_superuser=True,
        role="admin",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)

    return {"created": True}

@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = (
        db.query(User)
        .filter(User.email == form_data.username.lower())
        .first()
    )

    if (
        user is None
        or not user.is_active
        or not verify_password(form_data.password, user.hashed_password)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(subject=str(user.id))

    return {
        "access_token": token,
        "token_type": "bearer",
    }


@router.get("/me", response_model=UserResponse)
def get_profile(
    current_user: User = Depends(get_current_user),
):
    return current_user
