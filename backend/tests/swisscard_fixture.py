"""Synthetic Swisscard-layout exports (no private data)."""
HEADER = ("Transaction date,Description,Merchant,Card number,Currency,Amount,Foreign Currency,"
          "Amount in foreign currency,Debit/Credit,Status,Merchant Category,Registered Category")


def row(date="01.10.2026", desc="Shop One", merchant="Shop One", card="1234 56** **** 7890",
        cur="CHF", amount="50.00", fcur="", famount="", dc="Debit", status="Posted",
        mcat="Grocery", rcat="Food"):
    def q(v):
        return '"' + v.replace('"', '""') + '"'
    return ",".join(q(x) for x in [date, desc, merchant, card, cur, amount, fcur, famount, dc, status, mcat, rcat])


def build(rows, header=HEADER, bom=False):
    return ("﻿" if bom else "") + "\n".join([header, *rows]) + "\n"


def export_174():
    """174 entries: 164 Posted debits, 3 Posted credits (2 blank merchant), 7 Pending debits."""
    rows = [row(desc=f"Purchase {i}", merchant=f"Shop {i}", amount=f"{10 + i}.50") for i in range(164)]
    rows += [
        row(desc="Payment Thank You", merchant="", amount="-6000.00", dc="Credit"),
        row(desc="Payment Thank You 2", merchant="", amount="-250.10", dc="Credit"),
        row(desc="Refund Shop 1", merchant="Shop 1", amount="-43.70", dc="Credit"),
    ]
    rows += [row(desc=f"Pending {i}", merchant=f"Pending Shop {i}", amount="9.90", status="Pending") for i in range(7)]
    return build(rows)
