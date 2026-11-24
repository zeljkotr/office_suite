from sqlalchemy import ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class EmailFolderItem(TimestampMixin, Base):
    __tablename__ = "email_folder_items"
    __table_args__ = (
        Index("ix_email_folder_owner_folder", "owner_id", "folder"),
        Index("ix_email_folder_account_folder", "account_id", "folder"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="SET NULL"), nullable=True
    )
    folder: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
