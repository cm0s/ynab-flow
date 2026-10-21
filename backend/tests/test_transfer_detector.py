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


from src.transfer_detector import detect_repayment


def test_blank_merchant_repayment_detected_but_refund_is_not():
    assert detect_repayment("Payment Thank You", 6000.0, merchant="").is_transfer
    assert detect_repayment("Zahlung - Danke", 250.0).is_transfer
    assert not detect_repayment("Refund Shop 1", 43.7, merchant="Shop 1").is_transfer
    assert not detect_repayment("Payment Thank You", -5.0).is_transfer       # debit never a repayment
    assert not detect_repayment("Some Positive Credit", 10.0).is_transfer    # positive alone is not enough


def test_real_french_swisscard_repayment_text_is_detected_but_a_paypal_refund_is_not():
    assert detect_repayment("VOTRE PAIEMENT – MERCI BEAUCOUP", 3014.90, merchant="").is_transfer
    assert not detect_repayment("PAYPAL *EXAMPLESHOP, 0445759800", 43.70, merchant="PAYPAL *EXAMPLESHOP").is_transfer
