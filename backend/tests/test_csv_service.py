"""Tests for the CSV upload and parsing service."""
from src.csv_service import parse_csv, _is_postfinance, _convert_date_dmy


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


# --- PostFinance format tests ---

POSTFINANCE_CSV = """\
Date de début:;="31.01.2026"
Catégorie:;="Tous"
Compte:;="CH3009000000121371632"
Monnaie:;="CHF"

Date;Type de transaction;Texte de notification;Crédit en CHF;Débit en CHF;Label;Catégorie

17.03.2026;Enregistrement comptable;"ENVOI D'ARGENT TWINT DU 17.03.2026 POUR NUMÉRO MOBILE. +41799120668 (EFPM) ELODIE COMMUNICATIONS: POUR LE CADEAU DE MARIA";;-15;;Finances // Transfert autres
05.03.2026;Enregistrement comptable;"CRÉDIT CH9409000000120003653 EXPÉDITEUR: CAISSE CANTONALE GE DE COMP. COMMUNICATIONS: INDEMNITÉ JOURNALIÈRE AI";4654.55;;;Autres recettes
12.02.2026;Enregistrement comptable;"ACHAT/PRESTATION TWINT DU 12.02.2026 MIGROS MORGES MORGES (CH)";;-36.35;;Achats // Supermarchés

Disclaimer:
Le contenu du document a été généré à partir des paramètres de filtrage.
"""


def test_is_postfinance_detected():
    assert _is_postfinance(POSTFINANCE_CSV) is True
    assert _is_postfinance(SAMPLE_CSV) is False


def test_convert_date_dmy():
    assert _convert_date_dmy("17.03.2026") == "2026-03-17"
    assert _convert_date_dmy("05.01.2025") == "2025-01-05"
    assert _convert_date_dmy("bad-date") == "bad-date"


def test_parse_postfinance_basic():
    txns = parse_csv(POSTFINANCE_CSV)
    assert len(txns) == 3


def test_parse_postfinance_dates_converted():
    txns = parse_csv(POSTFINANCE_CSV)
    assert txns[0].date == "2026-03-17"
    assert txns[1].date == "2026-03-05"
    assert txns[2].date == "2026-02-12"


def test_parse_postfinance_amounts():
    txns = parse_csv(POSTFINANCE_CSV)
    # Debit: -15 in file → outflow 15, amount -15
    assert txns[0].outflow == 15
    assert txns[0].inflow is None
    assert txns[0].amount == -15
    # Credit: 4654.55 → inflow
    assert txns[1].inflow == 4654.55
    assert txns[1].outflow is None
    assert txns[1].amount == 4654.55


def test_parse_postfinance_memo():
    txns = parse_csv(POSTFINANCE_CSV)
    assert "ENVOI D'ARGENT TWINT" in txns[0].memo
    assert "INDEMNITÉ JOURNALIÈRE AI" in txns[1].memo


def test_parse_postfinance_category():
    txns = parse_csv(POSTFINANCE_CSV)
    assert txns[0].source_category == "Finances // Transfert autres"
    assert txns[2].source_category == "Achats // Supermarchés"


def test_parse_postfinance_skips_disclaimer():
    """Disclaimer lines at end should not produce transactions."""
    txns = parse_csv(POSTFINANCE_CSV)
    for t in txns:
        assert "Disclaimer" not in t.memo


def test_generic_csv_still_works():
    """Ensure the generic format is not broken by PostFinance detection."""
    txns = parse_csv(SAMPLE_CSV)
    assert len(txns) == 4
    assert txns[0].date == "2026-02-28"
