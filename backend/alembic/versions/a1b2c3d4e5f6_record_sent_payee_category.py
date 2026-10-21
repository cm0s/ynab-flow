"""record the payee and category actually sent to YNAB

Revision ID: a1b2c3d4e5f6
Revises: f8d6e7a9b2c3
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'f8d6e7a9b2c3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('import_rows') as batch:
        batch.add_column(sa.Column('sent_payee', sa.String(), nullable=True))
        batch.add_column(sa.Column('sent_category', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('import_rows') as batch:
        batch.drop_column('sent_category')
        batch.drop_column('sent_payee')
