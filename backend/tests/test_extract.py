"""Local extraction from synthetic emails: PDF text, HTML, refund, shipping and a newsletter (no AI)."""
from src.mail.extract import analyze, content_hash, parse_message
from tests import mail_fixtures as fx


def test_pdf_invoice_fields_come_from_the_pdf_text():
    parsed = parse_message(fx.pdf_invoice())
    assert "Total CHF 120.00" in parsed.pdf_text and not parsed.scanned_pdfs
    f = analyze(parsed)
    assert f.is_candidate and f.kind == "purchase"
    assert (f.total_milliunits, f.currency, f.purchase_date) == (120000, "CHF", "2026-10-03")
    assert f.reference == "R-2026-88412" and f.card_hint == "7890" and f.merchant == "Digitec Galaxus"


def test_html_order_confirmation():
    parsed = parse_message(fx.html_order())
    assert "color:red" not in parsed.text and "Bio Milch 1l CHF 1.95" in parsed.text
    f = analyze(parsed)
    assert (f.total_milliunits, f.currency, f.reference, f.purchase_date) == (84900, "CHF", "MO-551203", "2026-10-03")
    assert f.merchant == "Migros Online"


def test_refund_and_shipping_kinds():
    refund = analyze(parse_message(fx.refund()))
    assert refund.kind == "refund" and refund.total_milliunits == 25000 and refund.reference == "10458812"
    ship = analyze(parse_message(fx.shipping_notice()))
    assert ship.is_candidate and ship.kind == "shipping" and ship.total_milliunits is None
    assert ship.reference == "R-2026-88412"


def test_newsletter_is_not_a_receipt():
    assert analyze(parse_message(fx.newsletter())).is_candidate is False


def test_scanned_pdf_has_no_text_layer_but_is_a_candidate():
    parsed = parse_message(fx.scanned_invoice())
    assert len(parsed.scanned_pdfs) == 1
    f = analyze(parsed)
    assert f.is_candidate and f.total_milliunits is None


def test_content_hash_ignores_recipient_and_transport_headers():
    a = parse_message(fx.html_order(to="me@proton.me", message_id="<1@x>"))
    b = parse_message(fx.html_order(to="partner@proton.me", message_id="<2@x>"))
    assert content_hash(a) == content_hash(b)
    assert content_hash(a) != content_hash(parse_message(fx.refund()))
