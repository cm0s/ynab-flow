"""Tests for transfer detection (FR-11)."""
from src.transfer_detector import detect_transfer


def test_virement_memo():
    result = detect_transfer("VIREMENT EN FAVEUR DE COMPTE EPARGNE")
    assert result.is_transfer is True
    assert result.confidence >= 0.3


def test_transfer_category():
    result = detect_transfer("Some memo", source_category="Virement interne")
    assert result.is_transfer is True


def test_no_transfer():
    result = detect_transfer("ACHAT MIGROS MORGES", source_category="Achats")
    assert result.is_transfer is False
    assert result.confidence < 0.3


def test_german_transfer():
    result = detect_transfer("EIGENUEBERTRAG AUF SPARKONTO")
    assert result.is_transfer is True


def test_combined_signals():
    result = detect_transfer(
        "VIREMENT VERS COMPTE CH12 3456",
        source_category="Transfer interne",
    )
    assert result.is_transfer is True
    assert result.confidence >= 0.5
