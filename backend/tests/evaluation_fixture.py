"""Synthetic YNAB plan export (no private data) for evaluation tests."""

import csv
import io
import zipfile
from pathlib import Path

HEADER = ["Account", "Flag", "Date", "Payee", "Category Group/Category", "Category Group", "Category",
          "Memo", "Outflow", "Inflow", "Cleared"]


def line(date, payee, group, category, memo, outflow="0.00", inflow="0.00", account="Post"):
    full = f"{group}: {category}" if category else ""
    return [account, "", date, payee, full, group, category, memo, outflow, inflow, "Reconciled"]


def history():
    """Monthly 2024 history with recurring merchants, plus special rows."""
    rows = [line("01.01.2024", "Starting Balance", "Inflow", "Ready to Assign", "", inflow="1000.00")]
    for month in range(1, 13):
        d = f"{month:02d}"
        rows += [
            line(f"03.{d}.2024", "Migros", "Everyday expenses", "Groceries", f"MIGROS LAUSANNE {month}", "45.10"),
            line(f"05.{d}.2024", "Coop", "Everyday expenses", "Groceries", "COOP PRONTO GENEVE", "12.40"),
            line(f"07.{d}.2024", "SBB", "Fun + learn", "Transport", "SBB MOBILE TICKET", "3.80"),
            line(f"09.{d}.2024", "Employer", "Inflow", "Ready to Assign", "SALAIRE ACME SA", inflow="5000.00"),
            line(f"11.{d}.2024", "Transfer : Cashback", "", "", "PAIEMENT CARTE CASHBACK", "300.00"),
            line(f"13.{d}.2024", "Swisscom", "Fixed bills (monthly)", "Phone", "SWISSCOM MOBILE", "39.00"),
        ]
    rows += [
        line("15.06.2024", "Ikea", "Everyday expenses", "Household", "Split (1/2) IKEA AUBONNE", "80.00"),
        line("15.06.2024", "Ikea", "Fun + learn", "Gifts", "Split (2/2) IKEA AUBONNE", "20.00"),
        line("16.06.2024", "Cash", "Everyday expenses", "Groceries", "", "20.00"),
        line("17.06.2024", "Unknown", "", "", "SOMETHING UNCATEGORIZED", "5.00"),
        line("18.11.2024", "Migros", "Everyday expenses", "Groceries", "MIGROS LAUSANNE RETOUR", inflow="10.00"),
        line("19.11.2024", "Old Shop", "Hidden Categories", "Old stuff", "OLD SHOP BERN", "7.00"),
        line("20.12.2024", "Unique Shop", "Fun + learn", "Gifts", "ONLY IN DECEMBER SHOP", "55.00"),
        line("20.12.2024", "Unique Shop", "Fun + learn", "Gifts", "ONLY IN DECEMBER SHOP", "55.00"),
    ]
    return rows


def write_export(path: Path, rows=None, as_zip=True) -> Path:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HEADER)
    writer.writerows(rows if rows is not None else history())
    data = buf.getvalue().encode("utf-8-sig")
    if as_zip:
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("My Budget as of 2024-12-31 - Register.csv", data)
            z.writestr("My Budget as of 2024-12-31 - Plan.csv", b"Month\n")
    else:
        path.write_bytes(data)
    return path
