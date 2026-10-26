"""receipts, receipt matches, split lines, AI usage and AI suggestion columns

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""
from alembic import op
import sqlalchemy as sa

revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None

AI_COLUMNS = (
    ('ai_payee', sa.String()), ('ai_category_id', sa.String()), ('ai_splits_json', sa.Text()),
    ('ai_explanation', sa.String()), ('ai_uncertainty', sa.String()), ('ai_evidence_json', sa.Text()),
    ('ai_model', sa.String()), ('ai_requested_at', sa.DateTime()), ('alternatives_json', sa.Text()),
    ('sent_splits_json', sa.Text()),
)


def upgrade() -> None:
    op.create_table(
        'mailbox_cursors',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('mailbox', sa.String(), nullable=False),
        sa.Column('folder', sa.String(), nullable=False),
        sa.Column('uidvalidity', sa.BigInteger(), nullable=True),
        sa.Column('last_uid', sa.BigInteger(), nullable=False),
        sa.Column('last_fetch_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.String(), nullable=False),
        sa.UniqueConstraint('mailbox', 'folder', name='uq_mailbox_folder'),
    )
    op.create_table(
        'receipts',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('content_hash', sa.String(), nullable=False, unique=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('merchant', sa.String(), nullable=False),
        sa.Column('reference', sa.String(), nullable=False),
        sa.Column('purchase_date', sa.String(), nullable=True),
        sa.Column('total_milliunits', sa.BigInteger(), nullable=True),
        sa.Column('currency', sa.String(), nullable=False),
        sa.Column('items_json', sa.Text(), nullable=False),
        sa.Column('card_hint', sa.String(), nullable=False),
        sa.Column('extraction_method', sa.String(), nullable=False),
        sa.Column('local_total_milliunits', sa.BigInteger(), nullable=True),
        sa.Column('subject', sa.String(), nullable=False),
        sa.Column('sender', sa.String(), nullable=False),
        sa.Column('message_date', sa.String(), nullable=True),
        sa.Column('text_excerpt', sa.Text(), nullable=False),
        sa.Column('error', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_table(
        'receipt_sources',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('receipt_id', sa.String(), sa.ForeignKey('receipts.id'), nullable=False),
        sa.Column('mailbox', sa.String(), nullable=False),
        sa.Column('folder', sa.String(), nullable=False),
        sa.Column('uidvalidity', sa.BigInteger(), nullable=False),
        sa.Column('uid', sa.BigInteger(), nullable=False),
        sa.Column('message_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('mailbox', 'folder', 'uidvalidity', 'uid', name='uq_receipt_source_uid'),
    )
    op.create_index('ix_receipt_sources_receipt_id', 'receipt_sources', ['receipt_id'])
    op.create_table(
        'receipt_matches',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('receipt_id', sa.String(), sa.ForeignKey('receipts.id'), nullable=False),
        sa.Column('import_row_id', sa.String(), sa.ForeignKey('import_rows.id'), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('signals_json', sa.Text(), nullable=False),
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('origin', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('receipt_id', 'import_row_id', name='uq_receipt_match'),
    )
    op.create_index('ix_receipt_matches_receipt_id', 'receipt_matches', ['receipt_id'])
    op.create_index('ix_receipt_matches_import_row_id', 'receipt_matches', ['import_row_id'])
    op.create_table(
        'import_row_splits',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('row_id', sa.String(), sa.ForeignKey('import_rows.id'), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('amount_milliunits', sa.BigInteger(), nullable=False),
        sa.Column('category_id', sa.String(), nullable=False),
        sa.Column('payee', sa.String(), nullable=False),
        sa.Column('memo', sa.String(), nullable=False),
        sa.Column('origin', sa.String(), nullable=False),
    )
    op.create_index('ix_import_row_splits_row_id', 'import_row_splits', ['row_id'])
    op.create_table(
        'ai_usage',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('purpose', sa.String(), nullable=False),
        sa.Column('model', sa.String(), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cache_read_tokens', sa.Integer(), nullable=False),
        sa.Column('cache_write_tokens', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Float(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('detail', sa.String(), nullable=False),
    )
    op.create_index('ix_ai_usage_created_at', 'ai_usage', ['created_at'])
    with op.batch_alter_table('import_rows') as batch:
        for name, type_ in AI_COLUMNS:
            batch.add_column(sa.Column(name, type_, nullable=True))
        batch.add_column(sa.Column('ai_status', sa.String(), nullable=False, server_default='none'))


def downgrade() -> None:
    with op.batch_alter_table('import_rows') as batch:
        batch.drop_column('ai_status')
        for name, _ in reversed(AI_COLUMNS):
            batch.drop_column(name)
    op.drop_index('ix_ai_usage_created_at', table_name='ai_usage')
    op.drop_table('ai_usage')
    op.drop_index('ix_import_row_splits_row_id', table_name='import_row_splits')
    op.drop_table('import_row_splits')
    op.drop_index('ix_receipt_matches_import_row_id', table_name='receipt_matches')
    op.drop_index('ix_receipt_matches_receipt_id', table_name='receipt_matches')
    op.drop_table('receipt_matches')
    op.drop_index('ix_receipt_sources_receipt_id', table_name='receipt_sources')
    op.drop_table('receipt_sources')
    op.drop_table('receipts')
    op.drop_table('mailbox_cursors')
