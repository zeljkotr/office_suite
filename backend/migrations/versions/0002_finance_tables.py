"""Add advances, income/expenses, and bank reconciliation tables."""

from alembic import op
from app.models import Base

revision = "0002_finance_tables"
down_revision = "0001_legacy_schema"
branch_labels = None
depends_on = None

FINANCE_TABLES = (
    "invoice_credit_notes",
    "invoice_advances",
    "invoice_advance_audit_events",
    "invoice_advance_allocations",
    "invoice_advance_refunds",
    "financial_records",
    "financial_record_payments",
    "financial_record_audit_events",
    "bank_accounts",
    "bank_transactions",
    "bank_reconciliations",
    "bank_audit_events",
)


def upgrade():
    tables = [Base.metadata.tables[name] for name in FINANCE_TABLES]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables, checkfirst=True)


def downgrade():
    tables = [Base.metadata.tables[name] for name in FINANCE_TABLES]
    Base.metadata.drop_all(bind=op.get_bind(), tables=tables, checkfirst=True)
