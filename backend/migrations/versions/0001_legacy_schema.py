"""Initial Office Suite schema.

Creates the original application tables on a fresh installation.
Existing installations must stamp this revision before applying later revisions.
"""

from alembic import op
from app.models import Base

revision = "0001_legacy_schema"
down_revision = None
branch_labels = None
depends_on = None

LEGACY_TABLES = (
    "users",
    "invoices",
    "invoice_audit_events",
    "invoice_payments",
    "invoice_refunds",
    "office_records",
    "email_accounts",
    "email_folder_items",
    "user_ai_settings",
)


def upgrade():
    tables = [Base.metadata.tables[name] for name in LEGACY_TABLES]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables, checkfirst=True)


def downgrade():
    tables = [Base.metadata.tables[name] for name in LEGACY_TABLES]
    Base.metadata.drop_all(bind=op.get_bind(), tables=tables, checkfirst=True)
