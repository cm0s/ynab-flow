"""add edited_memo to import_rows

Revision ID: c5a3b4d6e8f9
Revises: b4f2a3c5d6e7
Create Date: 2026-03-26
"""
from alembic import op
import sqlalchemy as sa

revision = 'c5a3b4d6e8f9'
down_revision = 'b4f2a3c5d6e7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('import_rows', sa.Column('edited_memo', sa.String(), nullable=False, server_default=''))


def downgrade() -> None:
    op.drop_column('import_rows', 'edited_memo')
