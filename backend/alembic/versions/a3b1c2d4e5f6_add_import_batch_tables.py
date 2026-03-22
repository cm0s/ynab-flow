"""Add import batch tables

Revision ID: a3b1c2d4e5f6
Revises: 211df7b030a2
Create Date: 2026-03-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3b1c2d4e5f6'
down_revision: Union[str, Sequence[str], None] = '211df7b030a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'import_batches',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('plan_id', sa.String(), sa.ForeignKey('plans.id'), nullable=False),
        sa.Column('filename', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'import_rows',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('batch_id', sa.String(), sa.ForeignKey('import_batches.id'), nullable=False),
        sa.Column('row_index', sa.Integer(), nullable=False),
        sa.Column('date', sa.String(), nullable=False),
        sa.Column('original_memo', sa.String(), nullable=False),
        sa.Column('cleaned_memo', sa.String(), nullable=False, server_default=''),
        sa.Column('merchant_stem', sa.String(), nullable=False, server_default=''),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('label', sa.String(), nullable=False, server_default=''),
        sa.Column('source_category', sa.String(), nullable=False, server_default=''),
        sa.Column('payee', sa.String(), nullable=True),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('source', sa.String(), nullable=False, server_default='unclassified'),
        sa.Column('explanation', sa.String(), nullable=False, server_default=''),
        sa.Column('review_required', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('flag_ignore', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(), nullable=False, server_default='pending'),
        sa.Column('edited_payee', sa.String(), nullable=False, server_default=''),
        sa.Column('edited_category', sa.String(), nullable=False, server_default=''),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('import_rows')
    op.drop_table('import_batches')
