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
