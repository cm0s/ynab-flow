# Proposal

## Why

The evaluation showed the limits of history-only classification on this household's data: payee 54% and category 71% accuracy, with most mistakes on vague descriptions (TWINT, PayPal, online shops), on the same merchant serving different purposes, and on mixed purchases. The receipts that explain these purchases already sit in the two household Proton mailboxes, and mixed purchases can only be recorded correctly as YNAB split transactions, which the app cannot create today.

## What Changes

- **Receipt ingestion:** a read-only IMAP adapter for a locally running Proton Mail Bridge, covering both mailboxes. It processes messages incrementally with stable message identities and per-mailbox cursors, extracts merchant, items, references, amounts, currency and dates from email bodies and PDF attachments, and keeps which mailbox each receipt came from. Credentials stay in `backend/.env` and are never logged.
- **Receipt matching:** receipts are linked to import rows using several signals. These are amount (including partial shipments and refunds), a purchase-versus-posting date window, the merchant, and the account and card. Ambiguous candidates are shown for the user to choose. Receipts never change bank amounts, dates, transfer routing or identities.
- **AI categorization with Claude:** suggestions for rows that history cannot classify confidently. Each request gets the row's bank evidence, any matched receipt, the plan's actual categories (by ID), relevant historical examples and the user's rules. It returns validated structured output: payee, category ID, optional split lines, evidence used, an explanation and its uncertainty.
  - **Configuration:** the provider is configurable. The default model is Claude Sonnet 5.5, with Anthropic's server-side refusal fallback.
  - **Cost control:** a monthly budget cap and usage accounting.
  - **Review:** AI suggestions always require review until their accuracy is measured.
- **YNAB split transactions:** import rows can carry split lines, each with its own category, amount and optional memo, whose amounts must sum exactly to the bank amount. Splits are suggested from receipts and AI, editable in review, and pushed as YNAB subtransactions (rows still unapproved). Invalid splits block the push.
- **Review UI:** each row shows its receipt evidence and the AI suggestion with the reason. A split editor is added to the review table. Settings gains a mailbox section (status and "Fetch receipts") and an AI section (model, usage this month, budget).

## Capabilities

### New Capabilities
- `receipt-ingestion`: read-only, incremental retrieval of receipts from local Proton Bridge mailboxes and extraction of structured purchase details.
- `receipt-matching`: linking extracted receipts to bank import rows with multi-signal, conservative matching.
- `ai-categorization`: Claude-backed payee/category/split suggestions with validated structured output, untrusted-input handling, cost control and auditability.
- `split-transactions`: representation, validation, review and YNAB write-back of split import rows.

### Modified Capabilities
- `transaction-classification`: AI suggestions become an additional, review-only prediction source that never overrides rules, user edits or confident history.

## Impact

- **Backend:**
  - **New modules:** mail adapter, receipt extraction, receipt matching, AI client and categorizer, splits.
  - **Changed modules:** `write_back_service.py` (subtransactions), `main.py` (receipt, AI and split endpoints), `models.py`.
- **Database:** one additive Alembic migration adds tables for mailbox cursors, receipts, receipt items, receipt matches, row split lines and AI usage, plus AI suggestion columns on import rows.
- **Dependencies:** `anthropic` (official SDK) and `pypdf` (PDF text). IMAP uses the standard library. No new frontend dependencies.
- **Configuration:**
  - **Proton Bridge:** host, port and per-mailbox credentials. Without them the mailbox features are off.
  - **Anthropic:** `ANTHROPIC_API_KEY`, `AI_MODEL` and `AI_MONTHLY_BUDGET_USD`. Without a key, AI is off.
- **Privacy:** receipts are stored only in the local database. Only the content needed for a suggestion is sent to the AI provider, in line with the user's approval of cloud AI.
- **Testing:** synthetic emails and PDFs, a fake IMAP server and a mocked Claude client. No live mailbox or paid API call in tests.
- **Out of scope:**
  - Cumulus/Supercard and phone receipts, and holiday/work purpose overrides (Phase 4).
  - Hosted access to Bridge.
  - Learning from split history.
  - Scheduled background fetching.
