from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class EmailAccount(TimestampMixin, Base):
    __tablename__ = "email_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "email_address", name="uq_email_account_user_address"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="other")
    email_address: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(150), nullable=False, default="")
    imap_host: Mapped[str] = mapped_column(String(255), nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    imap_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    smtp_host: Mapped[str] = mapped_column(String(255), nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, nullable=False, default=587)
    smtp_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    smtp_starttls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mailbox: Mapped[str] = mapped_column(String(255), nullable=False, default="INBOX")
    encrypted_password: Mapped[str] = mapped_column(String(2000), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_tested_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    connection_status: Mapped[str] = mapped_column(String(30), nullable=False, default="untested")
