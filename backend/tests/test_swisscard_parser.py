import pytest
from src.csv_service import parse_import, parse_csv, detect_format, CSVFormatError
from tests.swisscard_fixture import HEADER, row, build, export_174


def test_detects_by_headers_regardless_of_filename_and_reordered_columns():
    reordered = "Status,Amount,Debit/Credit,Currency,Card number,Merchant,Description,Transaction date"
    content = reordered + '\nPosted,"12.50",Debit,CHF,"1234","Shop","Shop buy",05.10.2026\n'
    report = parse_import(content)
    assert report.source_format == "swisscard"
    assert report.rows[0].amount_milliunits == -12500
    assert report.rows[0].date == "2026-10-05"


def test_bom_and_header_whitespace():
    content = build([row()], header=HEADER.replace("Merchant,", " Merchant ,"), bom=True)
    assert detect_format(content) == "swisscard"
    assert len(parse_import(content).rows) == 1


def test_debit_and_credits_exact_milliunits_without_double_reversal():
    content = build([
        row(amount="50.00", dc="Debit"),
        row(desc="Refund", amount="-43.70", dc="Credit"),
        row(desc="Payment", merchant="", amount="-6000.00", dc="Credit"),
    ])
    rows = parse_import(content).rows
    assert [r.amount_milliunits for r in rows] == [-50000, 43700, 6000000]
    assert [r.amount for r in rows] == [-50.0, 43.7, 6000.0]


def test_blank_merchant_credit_is_retained_with_evidence():
    r = parse_import(build([row(desc="Payment Thank You", merchant="", amount="-100.00", dc="Credit")])).rows[0]
    assert r.memo == "Payment Thank You"
    assert r.amount_milliunits == 100000
    assert r.evidence["Merchant"] == ""
    assert r.evidence["Card number"] == "1234 56** **** 7890"
    assert r.evidence["Merchant Category"] == "Grocery" and r.evidence["Registered Category"] == "Food"


def test_174_row_export_counts():
    report = parse_import(export_174())
    assert (report.detected_count, report.eligible_count, report.pending_count, report.invalid_count) == (174, 167, 7, 0)
    assert sum(1 for r in report.rows if r.amount_milliunits > 0) == 3
    assert all(r.source_status == "Posted" for r in report.rows)
    assert len(parse_csv(export_174())) == 167
    summary = report.summary()
    assert summary["pending"] == 7 and len(summary["pending_rows"]) == 7


@pytest.mark.parametrize("kwargs,fragment", [
    (dict(date="31.02.2026"), "Invalid date"),
    (dict(amount="NaN"), "Non-finite"),
    (dict(amount="1.2345"), "precision"),
    (dict(cur="EUR"), "currency"),
    (dict(dc="Debit", amount="-5.00"), "sign"),
    (dict(dc="Credit", amount="5.00"), "sign"),
    (dict(dc="Sideways"), "Debit/Credit"),
    (dict(status="Reversed"), "status"),
])
def test_row_level_failures_keep_other_rows(kwargs, fragment):
    report = parse_import(build([row(**kwargs), row(desc="Good")]))
    assert report.invalid_count == 1 and report.eligible_count == 1
    assert fragment.lower() in report.errors[0].message.lower()
    assert report.errors[0].line == 2


def test_unsupported_layout_is_a_format_error():
    with pytest.raises(CSVFormatError) as exc:
        parse_import("Foo,Bar\n1,2\n")
    assert "Supported formats" in str(exc.value)


def test_blank_file_is_empty_result():
    assert parse_import("").detected_count == 0


def test_generic_rejects_bad_values_per_row():
    report = parse_import("Date,Memo,Inflow,Outflow\n2026-02-30,Bad date,,5\n2026-02-01,Neg,,-5\n2026-02-02,Ok,,5.25\n")
    assert report.eligible_count == 1 and report.invalid_count == 2
    assert report.rows[0].amount_milliunits == -5250
