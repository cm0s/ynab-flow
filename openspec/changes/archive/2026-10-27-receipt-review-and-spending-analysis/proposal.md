# Proposal

## Why

The first real fetch (about 1,500 messages from two mailboxes, 60 days) produced 240 receipts, 25 marked *needs a check*, receipts with a total of 0 or no amount, and 1,670 messages skipped as not receipts. Today a receipt can only be opened from the 🧾 badge of a matched import row, so the receipts that most need attention (unmatched, doubtful or wrongly skipped) cannot be seen or fixed at all. Receipts are also the only source that says what a purchase contained: once they are reliable they can show where money goes at item level (for example cleaning products inside a supermarket bill), which YNAB categories alone cannot.

## What Changes

- New **Receipts** page (its own view next to Dashboard and Settings) listing every stored receipt and skipped message, with filters (status, kind, linked or not, mailbox, merchant, date range) and search.
- Receipt **detail view**: extracted fields and items beside the evidence (subject, sender, mailbox sources, stored text excerpt), the AI and locally read totals side by side for *needs a check*, and the rows it is linked to.
- **Corrections**: edit merchant, date, total, currency, kind, reference and items; mark *not a receipt* or *this is a receipt*; mark *exclude from spending* for duplicates such as an order confirmation and an invoice for the same purchase. Corrections are kept as the user's values and are never overwritten by a later fetch or AI re-read.
- **Read again with AI** for one receipt, with the cost estimate shown first. A skipped message is re-read from the mailbox through the existing read-only Bridge access (UID FETCH with `BODY.PEEK`), because only its subject and sender were stored.
- **Manual linking**: find candidate rows for a receipt in any import (including rows already pushed to YNAB, for evidence and analysis only) and link or unlink them. Matching runs again after a correction.
- **Spending analysis** (a tab of the Receipts page): spending by item, merchant and category over a chosen period, from receipt items.
  - Each item's category comes from, in order: the user's choice, the linked row's split line for that item, the linked row's single category, an AI suggestion, otherwise *Uncategorized*.
  - AI item categorization runs only when the user clicks *Categorize items*, after a cost estimate, and its suggestions stay editable.
  - Amounts are in the plan currency: linked receipts are scaled to the bank amount; unlinked receipts in another currency are listed separately and not converted.
  - Refunds are subtracted; shipping and payment notices and excluded receipts are not counted.
- Unchanged: the mailbox stays read-only, receipts never change bank values, AI never writes to YNAB, and the monthly AI budget applies to the new AI requests.

## Capabilities

### New Capabilities
- `receipt-review`: browsing, inspecting and correcting stored receipts and skipped messages, re-reading one receipt, and keeping user corrections authoritative over later extraction.
- `spending-analysis`: assigning categories to receipt items (from splits, row category, on-demand AI or the user) and reporting spending by item, merchant and category over time.

### Modified Capabilities
- `receipt-matching`: matching is re-run after a receipt is corrected, and the user can link a receipt to rows of any import, including pushed rows, as evidence only.

## Impact

- **Database**: new columns on `receipts` (user-corrected fields, exclusion flag) and per-item category data; an Alembic migration that keeps existing receipts.
- **Backend**: new endpoints to list, view, correct, re-read and link receipts, to suggest item categories, and to query spending; new AI purpose `categorize_items` in the usage records; a read-only single-message fetch in `src/mail/`.
- **Frontend**: new Receipts view with list, detail, correction form and spending tab; the existing 🧾 badge links to the receipt's detail.
- **Cost**: AI is used only when the user clicks *Read again with AI* or *Categorize items*, always after an estimate and within `AI_MONTHLY_BUDGET_USD`.
- **Docs**: `docs/receipts-ai-splits.md` gains review and spending sections.
