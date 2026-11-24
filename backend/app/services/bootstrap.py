import os

from sqlalchemy.orm import Session

from app.models.user import User
from app.services.security import hash_password


def create_initial_admin(db: Session) -> None:
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD", "")
    full_name = os.getenv("ADMIN_NAME", "System Administrator").strip()

    if not email or not password:
        raise RuntimeError(
            "ADMIN_EMAIL and ADMIN_PASSWORD must be configured."
        )

    if len(password) < 12:
        raise RuntimeError(
            "ADMIN_PASSWORD must contain at least 12 characters."
        )

    existing_admin = db.query(User).filter(
        User.is_superuser.is_(True)
    ).first()

    if existing_admin:
        return

    existing_user = db.query(User).filter(User.email == email).first()

    if existing_user:
        raise RuntimeError(
            "The configured administrator email already belongs to a "
            "non-administrator account."
        )

    admin = User(
        email=email,
        full_name=full_name,
        hashed_password=hash_password(password),
        is_active=True,
        is_superuser=True,
        role="admin",
    )

    db.add(admin)
    db.commit()
