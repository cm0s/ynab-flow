"""Migration tests on disposable SQLite databases (no live data touched)."""
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

BACKEND = Path(__file__).resolve().parent.parent
PRIOR = "d6b4c5e7f9a0"


def _config(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


def test_fresh_upgrade_creates_new_schema(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    command.upgrade(_config(url, monkeypatch), "head")
    insp = sa.inspect(sa.create_engine(url))
    assert {"source_records", "source_exports"} <= set(insp.get_table_names())
    cols = {c["name"] for c in insp.get_columns("import_rows")}
    assert {"amount_milliunits", "payee_confidence", "category_confidence", "write_state",
            "reconciliation_state", "evidence_json", "account_id"} <= cols
    uniques = {u["name"] for u in insp.get_unique_constraints("source_records")}
    assert "uq_source_occurrence" in uniques


def test_upgrade_from_prior_backfills_legacy_rows(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    cfg = _config(url, monkeypatch)
    command.upgrade(cfg, PRIOR)
    engine = sa.create_engine(url)
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO plans (id, ynab_plan_id, name) VALUES ('p','yp','P')"))
        c.execute(sa.text("INSERT INTO accounts (id, plan_id, ynab_account_id, name, type) VALUES ('a','p','ya','A','checking')"))
        for tid, amt in [("t1", -50000.0), ("t2", 12345.5), ("t3", 0.5)]:
            c.execute(sa.text(
                "INSERT INTO transactions (id, plan_id, account_id, ynab_transaction_id, date, amount, cleared)"
                " VALUES (:i,'p','a',:y,'2026-01-01',:amt,'cleared')"), {"i": tid, "y": "y" + tid, "amt": amt})
        c.execute(sa.text("INSERT INTO import_batches (id, plan_id, status) VALUES ('b','p','active')"))
        for rid, idx, amt in [("r1", 0, -50.0), ("r2", 1, 19.99), ("r3", 2, 0.0004), ("r4", 3, 43.7)]:
            c.execute(sa.text(
                "INSERT INTO import_rows (id, batch_id, row_index, date, original_memo, amount)"
                " VALUES (:i,'b',:x,'2026-01-01','memo',:amt)"), {"i": rid, "x": idx, "amt": amt})

    command.upgrade(cfg, "head")
    with engine.connect() as c:
        hist = dict(c.execute(sa.text("SELECT id, amount_milliunits FROM transactions")).fetchall())
        assert hist["t1"] == -50000 and hist["t2"] is None and hist["t3"] is None
        rows = {r.id: r for r in c.execute(sa.text(
            "SELECT id, amount_milliunits, reconciliation_state, write_state, source_record_id FROM import_rows"))}
        assert rows["r1"].amount_milliunits == -50000
        assert rows["r2"].amount_milliunits == 19990
        assert rows["r4"].amount_milliunits == 43700
        assert rows["r3"].amount_milliunits is None and rows["r3"].reconciliation_state == "quarantined"
        assert rows["r1"].reconciliation_state == "legacy_unresolved"
        assert rows["r1"].source_record_id is None
        batch = c.execute(sa.text("SELECT account_id, account_confirmed FROM import_batches")).fetchone()
        assert batch.account_id is None and not batch.account_confirmed
        # legacy float columns keep their meaning
        assert c.execute(sa.text("SELECT amount FROM import_rows WHERE id='r2'")).scalar() == 19.99


def test_downgrade_returns_to_prior_schema(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'down.db'}"
    cfg = _config(url, monkeypatch)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, PRIOR)
    insp = sa.inspect(sa.create_engine(url))
    assert "source_records" not in insp.get_table_names()
    assert "amount_milliunits" not in {c["name"] for c in insp.get_columns("import_rows")}


def test_category_ids_and_provenance_backfill(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'ids.db'}"
    cfg = _config(url, monkeypatch)
    command.upgrade(cfg, "a1b2c3d4e5f6")
    engine = sa.create_engine(url)
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO plans (id, ynab_plan_id, name) VALUES ('p','yp','P')"))
        c.execute(sa.text("INSERT INTO accounts (id, plan_id, ynab_account_id, name, type) VALUES ('a','p','ya','A','checking')"))
        c.execute(sa.text("INSERT INTO category_groups (id, plan_id, ynab_category_group_id, name) VALUES ('g','p','yg','G'), ('h','p','yh','H')"))
        c.execute(sa.text("INSERT INTO categories (id, category_group_id, ynab_category_id, name, deleted) VALUES "
                          "('c1','g','yc1','Groceries',0), ('c2','g','yc2','Dup',0), ('c3','h','yc3','Dup',0), ('c4','g','yc4','Gone',1)"))
        for tid, imp, approved in [("t1", "YF1:abc:1", 0), ("t2", "YF1:def:1", 1), ("t3", None, 1)]:
            c.execute(sa.text("INSERT INTO transactions (id, plan_id, account_id, ynab_transaction_id, date, amount, cleared, approved, import_id)"
                              " VALUES (:i,'p','a',:i,'2026-01-01',-1000,'cleared',:ap,:imp)"), {"i": tid, "ap": approved, "imp": imp})
        c.execute(sa.text("INSERT INTO import_batches (id, plan_id, status) VALUES ('b','p','active')"))
        for rid, cat in [("r1", "Groceries"), ("r2", "Dup"), ("r3", "Gone"), ("r4", "")]:
            c.execute(sa.text("INSERT INTO import_rows (id, batch_id, row_index, date, original_memo, amount, category, edited_category)"
                              " VALUES (:i,'b',0,'2026-01-01','m',-1.0,:c,:c)"), {"i": rid, "c": cat})
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        prov = dict(c.execute(sa.text("SELECT id, provenance FROM transactions")).fetchall())
        assert prov == {"t1": "app_unreviewed", "t2": "app_confirmed", "t3": "external"}
        ids = {r.id: (r.category_id, r.edited_category_id) for r in c.execute(sa.text("SELECT id, category_id, edited_category_id FROM import_rows"))}
        assert ids == {"r1": ("c1", "c1"), "r2": (None, None), "r3": (None, None), "r4": (None, None)}
        assert "model_versions" in sa.inspect(engine).get_table_names()
    command.downgrade(cfg, "a1b2c3d4e5f6")
    assert "model_versions" not in sa.inspect(sa.create_engine(url)).get_table_names()


def test_receipts_ai_splits_upgrade_and_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'phase3.db'}"
    cfg = _config(url, monkeypatch)
    command.upgrade(cfg, "b2c3d4e5f6a7")
    engine = sa.create_engine(url)
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO plans (id, ynab_plan_id, name) VALUES ('p','yp','P')"))
        c.execute(sa.text("INSERT INTO import_batches (id, plan_id, status) VALUES ('b','p','active')"))
        c.execute(sa.text("INSERT INTO import_rows (id, batch_id, row_index, date, original_memo, amount, edited_category)"
                          " VALUES ('r1','b',0,'2026-01-01','m',-1.0,'')"))
    command.upgrade(cfg, "head")
    insp = sa.inspect(engine)
    new_tables = {"mailbox_cursors", "receipts", "receipt_sources", "receipt_matches", "import_row_splits", "ai_usage"}
    assert new_tables <= set(insp.get_table_names())
    cols = {c["name"] for c in insp.get_columns("import_rows")}
    assert {"ai_payee", "ai_category_id", "ai_splits_json", "ai_status", "alternatives_json", "sent_splits_json"} <= cols
    with engine.connect() as c:
        assert c.execute(sa.text("SELECT ai_status FROM import_rows WHERE id='r1'")).scalar() == "none"
    command.downgrade(cfg, "b2c3d4e5f6a7")
    insp = sa.inspect(sa.create_engine(url))
    assert not (new_tables & set(insp.get_table_names()))
    assert "ai_status" not in {c["name"] for c in insp.get_columns("import_rows")}
    with sa.create_engine(url).connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM import_rows")).scalar() == 1
