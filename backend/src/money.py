"""Exact money and date helpers shared by CSV adapters and the YNAB boundary.

YNAB amounts are signed integer milliunits. Bank files use decimal currency
units; conversions go through ``Decimal`` and never through binary floats.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Union

MILLI = Decimal(1000)


class MoneyError(ValueError):
    """Raised when a money or date value cannot be represented exactly."""


def to_decimal(value: Union[str, int, Decimal]) -> Decimal:
    """Parse a bank amount string into a finite Decimal."""
    if isinstance(value, float):
        raise MoneyError("Binary floats are not accepted for money values")
    text = str(value).strip().replace("’", "").replace("'", "").replace(" ", "")
    if not text:
        raise MoneyError("Amount is empty")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise MoneyError(f"Invalid amount: {value!r}") from exc
    if not number.is_finite():
        raise MoneyError(f"Non-finite amount: {value!r}")
    return number


def to_milliunits(value: Union[str, int, Decimal]) -> int:
    """Convert a currency-unit amount to exact integer milliunits.

    Rejects values needing more than three decimal places.
    """
    number = to_decimal(value)
    scaled = number * MILLI
    if scaled != scaled.to_integral_value():
        raise MoneyError(f"Amount {value!r} has more precision than milliunits allow")
    return int(scaled)


def milliunits_to_decimal(milliunits: int) -> Decimal:
    """Currency-unit Decimal for an integer milliunit amount."""
    return Decimal(int(milliunits)) / MILLI


def milliunits_to_units(milliunits: int) -> float:
    """Currency-unit float used only for display and rule/ML amount features."""
    return float(milliunits_to_decimal(milliunits))


def parse_date(value: str, formats: Iterable[str]) -> str:
    """Parse ``value`` with one of the declared formats and return YYYY-MM-DD.

    ``strptime`` requires a full match, so partially matching text and
    impossible calendar dates are rejected.
    """
    text = (value or "").strip()
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise MoneyError(f"Invalid date: {value!r}")


def is_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return len(value) == 10
