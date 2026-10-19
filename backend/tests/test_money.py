import pytest
from decimal import Decimal
from src.money import (
    MoneyError, to_milliunits, milliunits_to_decimal, milliunits_to_units,
    parse_date, to_decimal,
)


def test_debit_refund_repayment_values():
    assert to_milliunits("-50.00") == -50000
    assert to_milliunits("43.70") == 43700
    assert to_milliunits("6000.00") == 6000000
    assert to_milliunits("0.1") == 100
    assert to_milliunits("19.99") == 19990  # float truncation would give 19989


def test_precision_limit():
    assert to_milliunits("1.234") == 1234
    with pytest.raises(MoneyError):
        to_milliunits("1.2345")


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-inf", "", "abc", "1,5x"])
def test_rejects_invalid_and_non_finite(bad):
    with pytest.raises(MoneyError):
        to_milliunits(bad)


def test_rejects_binary_float():
    with pytest.raises(MoneyError):
        to_decimal(1.1)


def test_milliunit_views():
    assert milliunits_to_decimal(-50000) == Decimal("-50")
    assert milliunits_to_units(-50000) == -50.0


def test_declared_date_validation():
    assert parse_date("01.10.2026", ["%d.%m.%Y"]) == "2026-10-01"
    assert parse_date("2026-02-28", ["%Y-%m-%d"]) == "2026-02-28"
    for bad in ["31.02.2026", "2026-10-01", "1.10.26x", ""]:
        with pytest.raises(MoneyError):
            parse_date(bad, ["%d.%m.%Y"])
