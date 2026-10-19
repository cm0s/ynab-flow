"""Backfill integer milliunits for legacy history and import rows.

History ``amount`` already holds YNAB milliunits; import-row ``amount`` holds
currency units. Non-finite, non-integral or otherwise invalid values are left
NULL and quarantined (``reconciliation_state='quarantined'``) instead of
being rounded. Legacy batches stay readable but need account confirmation.

Revision ID: f8d6e7a9b2c3
Revises: e7c5d6f8a1b2
"""
import math
from decimal import Decimal, InvalidOperation

from alembic import op
import sqlalchemy as sa

revision = 'f8d6e7a9b2c3'
down_revision = 'e7c5d6f8a1b2'
branch_labels = None
depends_on = None


def _history_milliunits(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return int(number)


def _import_milliunits(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        number = Decimal(str(value)) * 1000
    except InvalidOperation:
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return int(number)


def upgrade():
    conn = op.get_bind()
    txns = sa.table('transactions', sa.column('id'), sa.column('amount'), sa.column('amount_milliunits'))
    for row in conn.execute(sa.select(txns.c.id, txns.c.amount)).fetchall():
        value = _history_milliunits(row.amount)
        if value is not None:
            conn.execute(txns.update().where(txns.c.id == row.id).values(amount_milliunits=value))

    rows = sa.table(
        'import_rows', sa.column('id'), sa.column('amount'), sa.column('amount_milliunits'),
        sa.column('reconciliation_state'), sa.column('reconciliation_note'),
    )
    for row in conn.execute(sa.select(rows.c.id, rows.c.amount)).fetchall():
        value = _import_milliunits(row.amount)
        if value is None:
            conn.execute(rows.update().where(rows.c.id == row.id).values(
                reconciliation_state='quarantined',
                reconciliation_note='Legacy amount is not exactly representable in milliunits.',
            ))
        else:
            conn.execute(rows.update().where(rows.c.id == row.id).values(
                amount_milliunits=value,
                reconciliation_state='legacy_unresolved',
                reconciliation_note='Legacy row: confirm account and reconcile before writing.',
            ))


def downgrade():
    # Columns are dropped by the previous revision's downgrade.
    pass
