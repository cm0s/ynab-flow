"""Persist historical sync job status.

Revision ID: d6b4c5e7f9a0
Revises: c5a3b4d6e8f9
"""
from alembic import op
import sqlalchemy as sa

revision = 'd6b4c5e7f9a0'
down_revision = 'c5a3b4d6e8f9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sync_jobs',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('plan_id', sa.String(), sa.ForeignKey('plans.id'), nullable=False, unique=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('message', sa.String(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table('sync_jobs')
