"""stable category ids on import rows, transaction provenance, model versions

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""
import json

from alembic import op
import sqlalchemy as sa

revision = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None

APP_PREFIX = "YF1:"


def upgrade() -> None:
    with op.batch_alter_table('import_rows') as batch:
        batch.add_column(sa.Column('category_id', sa.String(), nullable=True))
        batch.add_column(sa.Column('edited_category_id', sa.String(), nullable=True))
        batch.add_column(sa.Column('sent_category_id', sa.String(), nullable=True))
    with op.batch_alter_table('transactions') as batch:
        batch.add_column(sa.Column('provenance', sa.String(), nullable=False, server_default='external'))
        batch.add_column(sa.Column('is_split', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        'model_versions',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('activated_at', sa.DateTime(), nullable=True),
        sa.Column('plan_id', sa.String(), nullable=True),
        sa.Column('training_json', sa.Text(), nullable=False),
        sa.Column('exclusions_json', sa.Text(), nullable=False),
        sa.Column('comparison_json', sa.Text(), nullable=False),
        sa.Column('note', sa.String(), nullable=False),
    )

    conn = op.get_bind()
    # Provenance of app-created transactions (corrections are derived at the next sync)
    conn.execute(sa.text(
        "UPDATE transactions SET provenance = CASE WHEN approved THEN 'app_confirmed' ELSE 'app_unreviewed' END "
        "WHERE import_id LIKE :p"), {"p": APP_PREFIX + "%"})

    # Category ids for import rows whose stored name resolves to exactly one live category of the batch's plan
    names = {}
    for plan_id, cat_id, name in conn.execute(sa.text(
            "SELECT g.plan_id, c.id, c.name FROM categories c JOIN category_groups g ON c.category_group_id = g.id "
            "WHERE c.deleted = :f"), {"f": False}):
        names.setdefault((plan_id, name), []).append(cat_id)
    resolved = unresolved = 0
    rows = conn.execute(sa.text(
        "SELECT r.id, b.plan_id, r.category, r.edited_category, r.sent_category FROM import_rows r "
        "JOIN import_batches b ON r.batch_id = b.id")).fetchall()
    for row_id, plan_id, category, edited, sent in rows:
        values = {}
        for column, name in (("category_id", category), ("edited_category_id", edited), ("sent_category_id", sent)):
            if not name:
                continue
            ids = names.get((plan_id, name), [])
            if len(ids) == 1:
                values[column] = ids[0]
                resolved += 1
            else:
                unresolved += 1
        if values:
            sets = ", ".join(f"{k} = :{k}" for k in values)
            conn.execute(sa.text(f"UPDATE import_rows SET {sets} WHERE id = :id"), {**values, "id": row_id})
    print(json.dumps({"category_id_backfill": {"resolved": resolved, "unresolved": unresolved}}))


def downgrade() -> None:
    op.drop_table('model_versions')
    with op.batch_alter_table('transactions') as batch:
        batch.drop_column('is_split')
        batch.drop_column('provenance')
    with op.batch_alter_table('import_rows') as batch:
        batch.drop_column('sent_category_id')
        batch.drop_column('edited_category_id')
        batch.drop_column('category_id')
