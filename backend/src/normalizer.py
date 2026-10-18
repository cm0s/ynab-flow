"""
FR-3: Transaction Normalization Engine

Transforms noisy raw bank memos into stable merchant-like tokens.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Configurable stop-words / boilerplate patterns (FR-3.1)
# ---------------------------------------------------------------------------

# Generic payment boilerplate to strip before extracting a merchant stem
BOILERPLATE_PATTERNS = [
    # TWINT payments
    r"TWINT\s+DU\s+\d[\d/\.\s]*",
    r"TWINT\s+",
    # Common French payment labels
    r"CREDIT\s+EXPEDITEUR\s+",
    r"ACHAT[/\s]+PRESTATION\s*",
    r"D[ÉE]BIT\s+(DIRECT\s+)?",
    r"VIREMENT\s+(EN[TT]RANT\s+)?",
    r"ORDRE\s+PERMANENT\s+",
    r"PAIEMENT\s+(PAR\s+CARTE\s+)?",
    r"RETRAIT\s+",
    # Card payment noise
    r"CARTE\s+(DE\s+CR[ÉE]DIT\s+|DEBIT\s+)?",
    r"ACHATS?\s+",
    # Date patterns like "DU 15.03.2025" or "15/03/25"
    r"\bDU\s+\d{1,2}[.\-/]\d{1,2}([.\-/]\d{2,4})?\b",
    r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b",
    # IBANs and long account numbers (FR-3.1)
    r"\b[A-Z]{2}\d{2}[A-Z0-9]{4,30}\b",
    r"\b\d{6,}\b",
    # Swiss postal codes and city snippets like "(CH)" or "BERN (CH)"
    r"\([A-Z]{2}\)\s*$",
    r"\b[A-Z]{2}-\d{4,5}\b",
    r"\bCH-\d{4}\b",
    # GMBH, SA, AG, SARL suffixes — kept for stem, stripped from noise later
    # Reference numbers / IDs
    r"\b(REF|NR|NO|ID)\.?\s*:?\s*[A-Z0-9\-]+\b",
    # Long hex references (unique per transaction, e.g. BF2B0A1C125E4E95A491D7489E5F2641)
    r"\b[A-F0-9]{16,}\b",
]

# Combined regex (case-insensitive, applied on accented-normalized text)
_BOILERPLATE_RE = re.compile(
    "|".join(f"(?:{p})" for p in BOILERPLATE_PATTERNS),
    re.IGNORECASE,
)

# Stopwords that should be removed from the final merchant stem
DEFAULT_STOPWORDS = {
    "sa", "gmbh", "ag", "sarl", "sàrl", "llc", "ltd", "inc",
    "et", "und", "and", "or",
}

# ---------------------------------------------------------------------------
# Result dataclass (FR-3.3)
# ---------------------------------------------------------------------------

@dataclass
class NormalizationResult:
    original_memo: str
    cleaned_memo: str
    merchant_stem: str
    matched_rule_name: Optional[str] = None   # Filled in later by the rule engine


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(memo: str, stopwords: set[str] | None = None) -> NormalizationResult:
    """
    Normalise a raw bank memo into a stable merchant token.

    Returns a NormalizationResult with original_memo, cleaned_memo,
    and merchant_stem populated. matched_rule_name is left None here —
    it should be filled in by the rule engine after matching.
    """
    if stopwords is None:
        stopwords = DEFAULT_STOPWORDS

    original = memo

    # 1. Unicode normalisation + accent stripping (FR-3.1)
    text = _strip_accents(memo)

    # 2. Uppercase (FR-3.1)
    text = text.upper()

    # 3. Strip boilerplate patterns (FR-3.1)
    text = _BOILERPLATE_RE.sub(" ", text)

    # 4. Collapse whitespace / punctuation cleanup (FR-3.1)
    text = re.sub(r"[^\w\s/&'-]", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()

    cleaned = text

    # 5. Merchant stem: remove stopwords and short tokens (FR-3.2)
    tokens = text.split()
    stem_tokens = [
        t for t in tokens
        if t.lower() not in stopwords and len(t) > 1
    ]
    stem = " ".join(stem_tokens)

    return NormalizationResult(
        original_memo=original,
        cleaned_memo=cleaned,
        merchant_stem=stem,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    """Decompose and drop combining diacritical marks."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))
