"""Add canonical money, account context, evidence, source ledger and write state.

Additive only: legacy columns keep their meaning.

Revision ID: e7c5d6f8a1b2
Revises: d6b4c5e7f9a0
"""
from alembic import op
import sqlalchemy as sa

revision = 'e7c5d6f8a1b2'
down_revision = 'd6b4c5e7f9a0'
branch_labels = None
depends_on = None


def _s(name, default=""):
    return sa.Column(name, sa.String(), nullable=False, server_default=default)


def upgrade():
    # source ledger first: import_rows references it
    op.create_table(
        'source_records',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('plan_id', sa.String(), sa.ForeignKey('plans.id'), nullable=False),
        sa.Column('account_id', sa.String(), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('fingerprint_version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('fingerprint', sa.String(), nullable=False),
        sa.Column('occurrence', sa.Integer(), nullable=False, server_default='1'),
        _s('source_format'),
        sa.Column('date', sa.String(), nullable=False),
        sa.Column('amount_milliunits', sa.BigInteger(), nullable=False),
        _s('currency'),
        sa.Column('evidence_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('import_id', sa.String(), nullable=False, unique=True),
        _s('reconciliation_state', 'pending'),
        sa.Column('ynab_transaction_id', sa.String(), nullable=True),
        _s('write_state', 'unsent'),
        sa.Column('claim_token', sa.String(), nullable=True),
        sa.Column('claimed_at', sa.DateTime(), nullable=True),
        _s('last_error'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('plan_id', 'account_id', 'fingerprint', 'occurrence', name='uq_source_occurrence'),
    )
    op.create_table(
        'source_exports',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('plan_id', sa.String(), sa.ForeignKey('plans.id'), nullable=False),
        sa.Column('account_id', sa.String(), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('signature', sa.String(), nullable=False),
        sa.Column('counts_json', sa.Text(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_source_exports_signature', 'source_exports', ['signature'])

    with op.batch_alter_table('transactions') as batch:
        batch.add_column(sa.Column('amount_milliunits', sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column('import_id', sa.String(), nullable=True))
        batch.add_column(sa.Column('transfer_account_ynab_id', sa.String(), nullable=True))
        batch.add_column(sa.Column('transfer_transaction_ynab_id', sa.String(), nullable=True))
        batch.create_index('ix_transactions_import_id', ['import_id'])

    with op.batch_alter_table('import_batches') as batch:
        batch.add_column(sa.Column('account_id', sa.String(), nullable=True))
        batch.create_foreign_key('fk_import_batches_account', 'accounts', ['account_id'], ['id'])
        batch.add_column(sa.Column('account_confirmed', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(_s('source_format'))
        batch.add_column(sa.Column('report_json', sa.Text(), nullable=False, server_default='{}'))
        batch.add_column(_s('export_signature'))

    with op.batch_alter_table('import_rows') as batch:
        batch.add_column(sa.Column('account_id', sa.String(), nullable=True))
        batch.create_foreign_key('fk_import_rows_account', 'accounts', ['account_id'], ['id'])
        batch.add_column(sa.Column('amount_milliunits', sa.BigInteger(), nullable=True))
        batch.add_column(_s('currency'))
        batch.add_column(_s('source_format'))
        batch.add_column(_s('source_status'))
        batch.add_column(sa.Column('evidence_json', sa.Text(), nullable=False, server_default='{}'))
        batch.add_column(sa.Column('source_record_id', sa.String(), nullable=True))
        batch.create_foreign_key('fk_import_rows_source_record', 'source_records', ['source_record_id'], ['id'])
        batch.add_column(sa.Column('payee_confidence', sa.Float(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('category_confidence', sa.Float(), nullable=False, server_default='0'))
        batch.add_column(_s('payee_source', 'unclassified'))
        batch.add_column(_s('category_source', 'unclassified'))
        batch.add_column(_s('payee_explanation'))
        batch.add_column(_s('category_explanation'))
        batch.add_column(sa.Column('review_flag', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(_s('transfer_state', 'none'))
        batch.add_column(sa.Column('transfer_account_id', sa.String(), nullable=True))
        batch.add_column(_s('reconciliation_state', 'pending'))
        batch.add_column(_s('reconciliation_note'))
        batch.add_column(_s('write_state', 'unsent'))
        batch.add_column(_s('write_error'))
        batch.add_column(sa.Column('ynab_transaction_id', sa.String(), nullable=True))


def downgrade():
    # Removing the ledger removes deduplication evidence: back up first.
    with op.batch_alter_table('import_rows') as batch:
        for fk in ('fk_import_rows_source_record', 'fk_import_rows_account'):
            batch.drop_constraint(fk, type_='foreignkey')
        for col in (
            'ynab_transaction_id', 'write_error', 'write_state', 'reconciliation_note',
            'reconciliation_state', 'transfer_account_id', 'transfer_state', 'review_flag',
            'category_explanation', 'payee_explanation', 'category_source', 'payee_source',
            'category_confidence', 'payee_confidence', 'source_record_id', 'evidence_json',
            'source_status', 'source_format', 'currency', 'amount_milliunits', 'account_id',
        ):
            batch.drop_column(col)
    with op.batch_alter_table('import_batches') as batch:
        batch.drop_constraint('fk_import_batches_account', type_='foreignkey')
        for col in ('export_signature', 'report_json', 'source_format', 'account_confirmed', 'account_id'):
            batch.drop_column(col)
    with op.batch_alter_table('transactions') as batch:
        batch.drop_index('ix_transactions_import_id')
        for col in ('transfer_transaction_ynab_id', 'transfer_account_ynab_id', 'import_id', 'amount_milliunits'):
            batch.drop_column(col)
    op.drop_index('ix_source_exports_signature', table_name='source_exports')
    op.drop_table('source_exports')
    op.drop_table('source_records')
