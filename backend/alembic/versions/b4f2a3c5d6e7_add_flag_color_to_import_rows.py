"""add flag_color to import_rows

Revision ID: b4f2a3c5d6e7
Revises: a3b1c2d4e5f6
Create Date: 2026-03-25
"""
from alembic import op
import sqlalchemy as sa

revision = 'b4f2a3c5d6e7'
down_revision = '68a8c6c9c098'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('import_rows', sa.Column('flag_color', sa.String(), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('import_rows', 'flag_color')
