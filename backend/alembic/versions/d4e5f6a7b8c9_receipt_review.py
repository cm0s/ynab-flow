"""receipt review: extracted-value snapshot, user-corrected fields and spending exclusion

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""
import json

from alembic import op
import sqlalchemy as sa

revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None

SNAPSHOT_COLUMNS = ('merchant', 'purchase_date', 'total_milliunits', 'currency', 'kind', 'reference', 'status')


def upgrade() -> None:
    with op.batch_alter_table('receipts') as batch:
        batch.add_column(sa.Column('extracted_json', sa.Text(), nullable=False, server_default='{}'))
        batch.add_column(sa.Column('user_fields_json', sa.Text(), nullable=False, server_default='[]'))
        batch.add_column(sa.Column('excluded', sa.Boolean(), nullable=False, server_default=sa.false()))
    # Everything stored so far was extracted, so the current values are the extracted snapshot
    conn = op.get_bind()
    rows = conn.execute(sa.text(f"SELECT id, items_json, {', '.join(SNAPSHOT_COLUMNS)} FROM receipts")).mappings().all()
    for row in rows:
        try:
            items = json.loads(row['items_json'] or '[]')
        except ValueError:
            items = []
        snapshot = {name: row[name] for name in SNAPSHOT_COLUMNS}
        snapshot['items'] = items
        conn.execute(sa.text("UPDATE receipts SET extracted_json = :s WHERE id = :id"),
                     {"s": json.dumps(snapshot, ensure_ascii=False), "id": row['id']})


def downgrade() -> None:
    with op.batch_alter_table('receipts') as batch:
        batch.drop_column('excluded')
        batch.drop_column('user_fields_json')
        batch.drop_column('extracted_json')
