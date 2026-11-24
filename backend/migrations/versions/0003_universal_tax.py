"""Add user-configurable universal tax management."""

from alembic import op
from app.models import Base

revision = "0003_universal_tax"
down_revision = "0002_finance_tables"
branch_labels = None
depends_on = None

TAX_TABLES = (
    "tax_jurisdictions", "tax_categories", "tax_rates", "tax_rules",
    "tax_registrations", "tax_transactions", "tax_audit_events",
    "tax_integrations",
)


def upgrade():
    tables = [Base.metadata.tables[name] for name in TAX_TABLES]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables, checkfirst=True)


def downgrade():
    tables = [Base.metadata.tables[name] for name in reversed(TAX_TABLES)]
    Base.metadata.drop_all(bind=op.get_bind(), tables=tables, checkfirst=True)
