"""Tests for the transaction normalizer (FR-3)."""
from src.normalizer import normalize


def test_sbb_mobile():
    """FR-3.2 example: SBB MOBILE extraction."""
    result = normalize("ACHAT/PRESTATION TWINT DU 15.03.2025 SBB MOBILE BERN (CH)")
    assert "SBB MOBILE" in result.merchant_stem
    assert result.original_memo.startswith("ACHAT")


def test_sunrise_yallo():
    """FR-3.2 example: SUNRISE / YALLO extraction."""
    result = normalize("DÉBIT SUNRISE GMBH / YALLO ZURICH")
    assert "SUNRISE" in result.merchant_stem
    assert "YALLO" in result.merchant_stem


def test_etat_de_vaud():
    """FR-3.2 example: accent stripping."""
    result = normalize("DÉBIT ETAT DE VAUD IMPÔTS")
    assert "ETAT" in result.merchant_stem
    assert "VAUD" in result.merchant_stem
    assert "IMPOTS" in result.merchant_stem


def test_whitespace_collapse():
    result = normalize("  SOME   MERCHANT   NAME  ")
    assert "  " not in result.cleaned_memo


def test_iban_stripped():
    result = normalize("PAIEMENT CH9300762011623852957 MERCHANT")
    # The IBAN should be removed from the cleaned memo
    assert "CH93" not in result.cleaned_memo


def test_empty_memo():
    result = normalize("")
    assert result.cleaned_memo == ""
    assert result.merchant_stem == ""
