"""Tests for the CSV upload and parsing service."""
from src.csv_service import parse_csv


SAMPLE_CSV = """Date,Memo,Inflow,Outflow,Label,Catégorie

2026-02-28,ACHAT/PRESTATION TWINT DU 28.02.2026 COMET CHESEAUX,,4.5,,Loisirs // Culture
2026-02-27,DÉBIT SUNRISE GMBH / YALLO ZURICH,,44.9,,Logement // Communication
2026-02-26,"CRÉDIT ETAT DE GENEVE",8613.5,,,Autres recettes
2026-02-25,ACHAT/PRESTATION TWINT DU 25.02.2026 MIGROS MORGES,,36.35,,Achats // Supermarchés

Invalid date
Invalid date
"""


def test_parse_csv_basic():
    txns = parse_csv(SAMPLE_CSV)
    assert len(txns) == 4


def test_parse_csv_skips_invalid():
    txns = parse_csv(SAMPLE_CSV)
    for t in txns:
        assert t.date.lower() != "invalid date"
        assert t.date != ""


def test_parse_csv_amounts():
    txns = parse_csv(SAMPLE_CSV)
    # First row: outflow of 4.5, so amount is -4.5
    assert txns[0].amount == -4.5
    # Third row: inflow of 8613.5, so amount is +8613.5
    assert txns[2].amount == 8613.5


def test_parse_csv_category():
    txns = parse_csv(SAMPLE_CSV)
    assert txns[0].source_category == "Loisirs // Culture"


def test_parse_csv_empty():
    txns = parse_csv("")
    assert len(txns) == 0
