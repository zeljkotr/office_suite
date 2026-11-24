"""Create accounting period and currency tables."""

from alembic import op
from app.models import Base
from app.models.accounting_period import AccountingPeriod, AccountingPeriodAudit
from app.models.currency import CurrencySettings, CurrencyRate

revision = "0004_accounting_periods_currency"
down_revision = "0003_universal_tax"
branch_labels = None
depends_on = None

TABLES = (
    AccountingPeriod.__table__,
    AccountingPeriodAudit.__table__,
    CurrencySettings.__table__,
    CurrencyRate.__table__,
)


def upgrade():
    Base.metadata.create_all(
        bind=op.get_bind(),
        tables=list(TABLES),
        checkfirst=True,
    )


def downgrade():
    Base.metadata.drop_all(
        bind=op.get_bind(),
        tables=list(reversed(TABLES)),
        checkfirst=True,
    )
