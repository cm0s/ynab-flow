# Tasks

## 1. Corrections foundation

- [x] 1.1 Add the Alembic migration for `receipts.extracted_json`, `user_fields_json` and `excluded`, with `extracted_json` backfilled from current columns; verify upgrade, downgrade and re-upgrade in `test_migrations` (SQLite and the PostgreSQL check used for earlier migrations)
- [x] 1.2 Add `apply_extraction` (D1) and route fetch extraction, local-only reading and AI extraction through it, so user fields are never overwritten and the classification is kept on re-extraction; verify tests where a fetch and an AI re-extraction run after a merchant correction and a *not a receipt* classification
- [x] 1.3 Implement corrections and resets for merchant, date, total, currency, kind, reference and items, with validation (positive amounts, ISO dates, known kinds) and the exclusion flag; verify unit tests for each field, an invalid amount and a reset restoring the extracted value

## 2. Receipt review API

- [x] 2.1 Add `GET /receipts` with the filters, search and pagination of D8, plus the possible-duplicate marker; verify endpoint tests for the *needs a check* and *unlinked purchases* scenarios and that responses carry no credentials or mailbox usernames
- [x] 2.2 Add `GET /receipts/{id}` with fields, corrected fields, extracted values, items with resolved categories, sources, excerpt, extraction method and error, links and proposals; verify a test for the side-by-side totals of a *needs a check* receipt
- [x] 2.3 Add `PATCH /receipts/{id}` (corrections, reset, exclusion) and re-run matching for active rows afterwards (D9); verify tests for the corrected-total-now-matches and rejected-pairing-stays-rejected scenarios
- [x] 2.4 Add `GET /receipts/{id}/candidates` (any import, ±30 days, scored) and `POST /receipts/{id}/links` wrapping `decide`; verify a test that linking a pushed row stores the link without changing its write state, amount or YNAB payload, and that automatic matching still skips pushed rows

## 3. Classification, rescue and AI re-read

- [x] 3.1 Add `fetch_one` (D3): EXAMINE, UID FETCH with `BODY.PEEK[]`, and a `UID SEARCH HEADER Message-ID` fallback after a UIDVALIDITY change; verify with the fake Bridge that only allowlisted commands are sent and flags are unchanged, including the fallback
- [x] 3.2 Add `POST /receipts/{id}/classify` for *receipt* / *not a receipt*, with rescue extraction that skips the local candidate check, and Bridge-unreachable handling (status `candidate`, error kept); verify tests for the rescue and Bridge-not-running scenarios and that a later fetch keeps the user classification
- [x] 3.3 Add `POST /receipts/{id}/reread` with `estimate_only`, the budget check and unchanged-on-failure behaviour; verify tests with the fake transport for success, budget reached, refusal and a user-corrected field surviving the re-read
- [x] 3.4 Document review, corrections, rescue and re-read in `docs/receipts-ai-splits.md`; verify the documented endpoints and fields match the code

## 4. Item categories

- [x] 4.1 Implement item category resolution (D5): user, split line by description or one-to-one position, linked row category (sent, edited, suggested), AI, uncategorized, with deleted categories reported; verify tests for the split-line, single-category-row and deleted-category scenarios
- [x] 4.2 Add `PUT /receipts/{id}/items/{index}/category`, marking items as user-owned (D2); verify tests that a user category survives a re-read and a later item categorization
- [x] 4.3 Implement AI item categorization (D4), with purpose `categorize_items`, the cached category prefix, plain-string IDs validated against live categories, and only empty items filled; add `POST /receipts/categorize-items` with `estimate_only`; verify tests with the fake transport for filling unlinked receipts, an unknown category ID, untrusted-data framing and usage recording

## 5. Spending analysis

- [x] 5.1 Implement counting and exact scaling (D6): kinds, statuses, exclusion, refunds, the "Other" line, linked receipts scaled to bank amounts with largest-remainder rounding, and unlinked foreign receipts listed separately; verify unit tests for the foreign-receipt-linked, notices-not-purchases and unlinked-foreign scenarios and that every scaled receipt sums exactly
- [x] 5.2 Add `GET /spending` (D7) grouped by category, merchant or item, with month totals, search, drill-down references, uncategorized share and uncounted count; verify endpoint tests for the category-over-months and item-search scenarios and that no AI or external call is made
- [x] 5.3 Document spending rules (sources, counting, currency, exclusion) in `docs/receipts-ai-splits.md` and add an `AGENTS.md` architecture note; verify the docs match the code

## 6. Frontend

- [x] 6.1 Add the Receipts view (header icon) with the filter bar, paginated table and duplicate marker, plus API client types and calls; verify `pnpm run build`
- [x] 6.2 Add the receipt detail panel: evidence, side-by-side totals, edit form with per-field reset, classification and exclusion toggles, *Read again with AI* with estimate and confirm, candidate rows with Link/Unlink, and item category pickers with source tags; verify `pnpm run build` and a browser check on a throwaway stack with synthetic receipts
- [x] 6.3 Add the Spending tab: date range, grouping, search, month columns with CSS bars, drill-down, uncategorized share, uncounted list and *Categorize items* with estimate and confirm; verify `pnpm run build` and a browser check with synthetic data
- [x] 6.4 Link the 🧾 badge popover to the receipt detail; verify in the browser that it opens the right receipt

## 7. Integration

- [x] 7.1 Run the full backend suite, `pnpm run build`, flow tests and lint (no new errors); verify all pass
- [x] 7.2 End-to-end browser check on a throwaway stack (fake Bridge, fake Claude, fake YNAB): correct a receipt so it matches, rescue a skipped message, link a receipt to a pushed row, categorize items, and read the spending report; verify the totals by hand against the synthetic data

## Workflow follow-up

- With the user: open the real receipts list, review the *needs a check* receipts and run a small paid *Categorize items* sample after seeing the estimate.
- Propose a commit and wait for approval (AGENTS.md); archive after review.
