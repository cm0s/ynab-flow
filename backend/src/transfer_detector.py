"""
FR-11: Transfer Detection

Heuristic-based detection of internal transfers between accounts.
Transfers default to review since false positives are risky.
"""

import re
from typing import Optional
from dataclasses import dataclass


TRANSFER_MEMO_PATTERNS = [
    re.compile(r"\bvirement\b", re.IGNORECASE),
    re.compile(r"\btransfer[t]?\b", re.IGNORECASE),
    re.compile(r"\b(vers|depuis)\s+(compte|account)\b", re.IGNORECASE),
    re.compile(r"\bunter\s+eigene\s+konten\b", re.IGNORECASE),  # German: between own accounts
    re.compile(r"\beigenuebertrag\b", re.IGNORECASE),
    re.compile(r"\binternal\s+transfer\b", re.IGNORECASE),
    re.compile(r"\bepargne\b", re.IGNORECASE),  # savings
]

TRANSFER_CATEGORY_KEYWORDS = [
    "transfer", "virement", "interne", "epargne", "savings",
    "zwischen konten", "eigene konten",
]


@dataclass
class TransferDetectionResult:
    is_transfer: bool
    confidence: float
    reason: str


def detect_transfer(
    memo: str,
    source_category: str = "",
    payee: str = "",
    amount: float = 0.0,
    historical_transfer_payees: Optional[set] = None,
) -> TransferDetectionResult:
    """
    Detect if a transaction is likely a transfer (FR-11.1).

    Returns a result with confidence level.
    Because false positives are risky, even high-confidence results
    default to requiring review (FR-11.2).
    """
    reasons = []
    score = 0.0

    # 1. Memo pattern matching
    normalized = memo.lower()
    for pattern in TRANSFER_MEMO_PATTERNS:
        if pattern.search(normalized):
            score += 0.4
            reasons.append(f"memo matches transfer pattern: {pattern.pattern}")
            break

    # 2. Source category indicates transfer
    cat_lower = source_category.lower()
    for keyword in TRANSFER_CATEGORY_KEYWORDS:
        if keyword in cat_lower:
            score += 0.35
            reasons.append(f"source category contains '{keyword}'")
            break

    # 3. Historical payee is known transfer
    if historical_transfer_payees and payee:
        if payee.lower() in {p.lower() for p in historical_transfer_payees}:
            score += 0.3
            reasons.append(f"payee '{payee}' is historical transfer payee")

    # 4. Memo contains account reference patterns (e.g. IBAN-like, account numbers)
    if re.search(r"CH\d{2}\s?\d{4}", normalized):
        score += 0.15
        reasons.append("memo contains Swiss IBAN/account pattern")

    # Cap at 1.0
    confidence = min(score, 1.0)

    return TransferDetectionResult(
        is_transfer=confidence >= 0.3,
        confidence=confidence,
        reason="; ".join(reasons) if reasons else "no transfer signals detected",
    )
