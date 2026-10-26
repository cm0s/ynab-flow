"""Local receipt extraction: message parsing, a receipt pre-check and unambiguous fields.

Nothing here calls an AI. Messages that fail the pre-check (newsletters,
notifications) are marked ``not_receipt`` and cost nothing. Candidates keep a
bounded text excerpt locally; AI extraction (``src.ai.extract``) reads that
text, or the PDF itself when it has no text layer.
"""

import email
import email.policy
import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import List, Optional

from src.money import MoneyError, to_milliunits

EXCERPT_LIMIT = 8 * 1024
PDF_TEXT_MIN_CHARS = 20

CURRENCIES = {"CHF": "CHF", "FR.": "CHF", "SFR.": "CHF", "EUR": "EUR", "€": "EUR", "USD": "USD", "$": "USD",
              "GBP": "GBP", "£": "GBP"}
_CUR = r"(CHF|SFR\.|Fr\.|EUR|€|USD|\$|GBP|£)"
_NUM = r"(\d{1,3}(?:['’ ]\d{3})*(?:[.,]\d{2})|\d+(?:[.,]\d{2}))"
AMOUNT_RE = re.compile(rf"{_CUR}\s?{_NUM}(?!\d)|(?<![\d.,]){_NUM}\s?{_CUR}", re.I)
TOTAL_WORDS = r"(?:grand\s+total|total|gesamt(?:betrag|summe)?|summe|endbetrag|montant(?:\s+total)?|totale|importo|betrag|amount\s+(?:due|paid|charged)|order\s+total|rechnungsbetrag)"
TOTAL_RE = re.compile(rf"{TOTAL_WORDS}\b[^\n\d€$£]{{0,40}}", re.I)
MARKERS = re.compile(
    r"\b(order|bestellung|commande|ordine|invoice|rechnung|facture|fattura|receipt|quittung|quittance|beleg|"
    r"kassenbon|ricevuta|total|gesamt|montant|refund|r[uü]ckerstattung|gutschrift|remboursement|rimborso|"
    r"payment|zahlung|paiement|shipped|versandt|exp[ée]di[ée]e?)\b", re.I)
REFUND_RE = re.compile(r"\b(refund|refunded|r[uü]ckerstattung|gutschrift|remboursement|rimborso|credit note)\b", re.I)
SHIPPING_RE = re.compile(r"\b(shipped|has been dispatched|versandt|verschickt|exp[ée]di[ée]e?|shipping confirmation|"
                         r"versandbest[äa]tigung|on its way|unterwegs)\b", re.I)
PAYMENT_RE = re.compile(r"\b(payment (?:received|confirmation)|zahlungsbest[äa]tigung|zahlung erhalten|"
                        r"paiement (?:re[çc]u|confirm[ée]))\b", re.I)
REFERENCE_RE = re.compile(
    r"\b(?:order|bestellung|commande|ordine|invoice|rechnung|facture|fattura|receipt|beleg)"
    r"(?:\s*(?:no\.?|nr\.?|number|nummer|num[ée]ro|n°|#|id))?\s*[:#]?\s*((?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{3,})", re.I)
CARD_RE = re.compile(r"(?:\*{2,}|x{2,}|•{2,}|…|ending in|endet auf|se terminant par)\s?(\d{4})\b", re.I)
DATE_PATTERNS = (
    (re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b"), ("d", "m", "y")),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b"), ("d", "m", "y")),
    (re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b"), ("y", "m", "d")),
)


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes
    text: str = ""          # extracted text layer for PDFs ("" when none)


@dataclass
class ParsedMessage:
    subject: str
    sender_name: str
    sender_address: str
    message_id: str
    message_date: Optional[str]          # YYYY-MM-DD
    text: str                            # plain text, or HTML converted to text
    pdfs: List[Attachment] = field(default_factory=list)

    @property
    def pdf_text(self) -> str:
        return "\n".join(p.text for p in self.pdfs if p.text)

    @property
    def all_text(self) -> str:
        return "\n".join(t for t in (self.subject, self.text, self.pdf_text) if t)

    @property
    def scanned_pdfs(self) -> List[Attachment]:
        return [p for p in self.pdfs if len(p.text.strip()) < PDF_TEXT_MIN_CHARS]


@dataclass
class LocalFields:
    is_candidate: bool
    kind: str = "purchase"
    total_milliunits: Optional[int] = None
    currency: str = ""
    purchase_date: Optional[str] = None
    reference: str = ""
    card_hint: str = ""
    merchant: str = ""


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    return normalize_whitespace("".join(parser.parts))


def normalize_whitespace(text: str) -> str:
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# pypdf warns about every font it cannot fully decode (with the font table attached); the text is still read
logging.getLogger("pypdf").setLevel(logging.ERROR)


def pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return normalize_whitespace("\n".join(page.extract_text() or "" for page in reader.pages))
    except Exception:                         # damaged or encrypted PDFs fall back to AI document reading
        return ""


def parse_message(raw: bytes) -> ParsedMessage:
    msg: EmailMessage = email.message_from_bytes(raw, policy=email.policy.default)
    name, address = parseaddr(str(msg.get("From", "")))
    try:
        when = parsedate_to_datetime(str(msg["Date"])).date().isoformat() if msg.get("Date") else None
    except (TypeError, ValueError):
        when = None
    plain, html, pdfs = [], [], []
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        filename = part.get_filename() or ""
        if ctype == "application/pdf" or filename.lower().endswith(".pdf"):
            data = part.get_payload(decode=True) or b""
            pdfs.append(Attachment(filename=filename or "attachment.pdf", content_type="application/pdf",
                                   data=data, text=pdf_text(data)))
        elif part.get_content_disposition() == "attachment":
            continue
        elif ctype == "text/plain":
            plain.append(part.get_content())
        elif ctype == "text/html":
            html.append(html_to_text(part.get_content()))
    text = normalize_whitespace("\n".join(plain)) if plain else "\n".join(html)
    return ParsedMessage(
        subject=str(msg.get("Subject", "") or "").strip(), sender_name=name.strip(), sender_address=address.lower(),
        message_id=str(msg.get("Message-ID", "") or "").strip(), message_date=when, text=text, pdfs=pdfs,
    )


def content_hash(parsed: ParsedMessage) -> str:
    """Identity of the receipt content, independent of mailbox, recipients and transport headers."""
    h = hashlib.sha256()
    h.update(re.sub(r"\s+", " ", parsed.text).strip().lower().encode())
    for pdf in sorted(hashlib.sha256(p.data).hexdigest() for p in parsed.pdfs):
        h.update(pdf.encode())
    return h.hexdigest()


def _amounts(text: str):
    for match in AMOUNT_RE.finditer(text):
        cur = (match.group(1) or match.group(4) or "").upper()
        number = match.group(2) or match.group(3)
        yield match.start(), CURRENCIES.get(cur, cur), number


def _to_milliunits(number: str) -> Optional[int]:
    text = re.sub(r"['’ ]", "", number)
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return to_milliunits(text.replace(",", ""))
    except MoneyError:
        return None


def local_total(text: str):
    """(milliunits, currency) of the last amount following a total keyword; (None, '') when ambiguous."""
    best = None
    for keyword in TOTAL_RE.finditer(text):
        window = text[keyword.start(): keyword.end() + 40]
        found = next(_amounts(window), None)
        if found:
            best = found
    if best is None:
        amounts = list(_amounts(text))
        if len({a[2] for a in amounts}) == 1:            # a single distinct amount is unambiguous
            best = amounts[0]
    if best is None:
        return None, ""
    return _to_milliunits(best[2]), best[1]


def local_date(text: str) -> Optional[str]:
    for pattern, order in DATE_PATTERNS:
        for match in pattern.finditer(text):
            parts = dict(zip(order, (int(g) for g in match.groups())))
            try:
                return date(parts["y"], parts["m"], parts["d"]).isoformat()
            except ValueError:
                continue
    return None


def merchant_from_sender(parsed: ParsedMessage) -> str:
    name = re.sub(r"\b(no-?reply|noreply|info|service|support|team|shop|orders?)\b", "", parsed.sender_name, flags=re.I)
    name = name.strip(" -|·,")
    if name:
        return name
    domain = parsed.sender_address.rpartition("@")[2]
    labels = [l for l in domain.split(".") if l not in ("mail", "email", "news", "info", "noreply", "shop")]
    return labels[-2].capitalize() if len(labels) >= 2 else domain


def receipt_kind(text: str, has_total: bool) -> str:
    if REFUND_RE.search(text):
        return "refund"
    if SHIPPING_RE.search(text) and not has_total:
        return "shipping"
    if PAYMENT_RE.search(text):
        return "payment"
    return "purchase"


def analyze(parsed: ParsedMessage) -> LocalFields:
    """Pre-check plus the fields that can be read without AI."""
    text = parsed.all_text
    has_amount = any(True for _ in _amounts(text)) or bool(TOTAL_RE.search(text) and re.search(r"\d", text))
    reference = REFERENCE_RE.search(text)
    notice = bool(SHIPPING_RE.search(text) or PAYMENT_RE.search(text)) and reference is not None
    is_candidate = bool(MARKERS.search(text)) and (has_amount or bool(parsed.pdfs) or notice)
    if not is_candidate:
        return LocalFields(is_candidate=False)
    total, currency = local_total(text)
    card = CARD_RE.search(text)
    return LocalFields(
        is_candidate=True,
        kind=receipt_kind(text, total is not None),
        total_milliunits=abs(total) if total is not None else None,
        currency=currency,
        purchase_date=local_date(text) or parsed.message_date,
        reference=reference.group(1) if reference else "",
        card_hint=card.group(1) if card else "",
        merchant=merchant_from_sender(parsed),
    )


def excerpt(parsed: ParsedMessage) -> str:
    return parsed.all_text[:EXCERPT_LIMIT]
