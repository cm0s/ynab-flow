# Tasks

## 1. Foundations

- [x] 1.1 Add pinned `anthropic` and `pypdf` to `backend/requirements.txt`, install them in the venv, and verify `python -c "import anthropic, pypdf"` succeeds
- [x] 1.2 Add `src/settings.py` (D1) with Bridge, AI and budget settings plus `GET /features` (no secrets in responses); document the variables in `backend/.env.example`; verify with tests for on/off reasons and that no credential value appears in the response
- [x] 1.3 Add the additive migration (D-Migration) for mailbox cursors, receipts, receipt sources, receipt matches, split lines, AI usage and the new import-row columns; verify upgrade/downgrade tests on SQLite, a disposable PostgreSQL check and `alembic check`

## 2. Split transactions

- [x] 2.1 Implement split storage and validation (`src/splits.py`, D8): at least two lines, exact milliunit sum, same sign, live non-deleted categories, no transfers; verify with unit tests for the rounding-mismatch and repayment scenarios
- [x] 2.2 Add `PUT`/`DELETE /import-rows/{id}/splits` and include split lines in batch payloads; verify that edits persist across reload and that invalid input returns 422 with the specific reason
- [x] 2.3 Push valid split rows as YNAB subtransactions (null parent category, import ID, `approved=false`, `sent_splits_json`), re-validating at push time; dry runs validate only. Verify with write-back tests for the split payload, a deleted category blocking the row, and a dry run sending nothing
- [x] 2.4 Build the split editor in the review table (category pickers by ID, exact amount entry, live unassigned amount, clear split); verify `pnpm run build`, flow tests for the unassigned-amount helper, and a browser check against a throwaway stack
- [x] 2.5 Document split behaviour in `docs/receipts-ai-splits.md` and verify the documented API matches the routes

## 3. Receipt ingestion

- [x] 3.1 Implement the read-only IMAP adapter with a command allowlist, `EXAMINE`, `BODY.PEEK` and the injectable connection (D2), plus a `FakeImapServer` test double; verify tests that no write command is issued and read flags are untouched
- [x] 3.2 Implement per-mailbox/folder cursors, UIDVALIDITY reset and content-hash deduplication with receipt sources; verify tests for the repeated-fetch, same-receipt-in-two-mailboxes and UIDVALIDITY-change scenarios
- [x] 3.3 Implement local extraction (text, HTML, `pypdf` PDF text, receipt pre-check, local total/date/currency) (D3); verify with synthetic `.eml` fixtures for a PDF invoice, an HTML order confirmation, a refund and a newsletter (no AI call)
- [x] 3.4 Add AI extraction for receipt candidates (text, or a PDF document block when there is no text layer) through the AI wrapper, with the `needs_check` flag on total disagreement; verify with a fake transport, including the scanned-PDF scenario and usage recording
- [x] 3.5 Add `POST /receipts/fetch` and `GET /receipts/status` (per-mailbox state, last fetch, errors in plain terms, Bridge unreachable); verify with endpoint tests using the fake IMAP server and with no mailbox configured
- [x] 3.6 Add the Settings mailbox section (status, Fetch receipts); verify `pnpm run build` and a browser check with the fake configuration off
- [x] 3.7 Document Bridge setup (both accounts, exporting the certificate, `.env` variables, folders) in `docs/receipts-ai-splits.md`, and verify the variable names match `src/settings.py`

## 4. Receipt matching

- [x] 4.1 Implement multi-signal scoring, the auto-link rule (≥ 0.75 and unique mutual best), candidate listing (≥ 0.45) and kind constraints (D4); verify tests for the card-purchase-posted-later, amount-alone, two-identical-orders and refund scenarios
- [x] 4.2 Support user link/unlink/reject and linking one receipt to several rows, with item-subset split suggestions for partial shipments; verify tests that links never change bank values or reconciliation state
- [x] 4.3 Run matching after receipt fetch and after upload, and add receipt evidence to batch payloads; verify with an import API test
- [x] 4.4 Add the receipt badge, popover and link/reject actions in the review table, and "Use receipt split"; verify `pnpm run build` and a browser check with synthetic receipts

## 5. AI categorization

- [x] 5.1 Implement the Claude wrapper (D5): lazy SDK import, structured output, adaptive thinking with effort, server-side refusal fallback when supported, refusal and error handling, prompt-caching prefix, usage and cost recording, monthly budget check, injectable transport; verify tests for budget reached, no key, refusal, authentication error and cost accounting
- [x] 5.2 Implement request construction and output validation (D6): row evidence, receipt details, allowed category IDs, up to 8 historical examples, rules, untrusted-data framing; verify tests for request content, unknown category, split sum mismatch and an injection fixture
- [x] 5.3 Implement the precedence merge (D7), shared by reclassify and AI suggestion, with alternatives kept; verify tests for the receipt-backed-AI-over-weak-history, user-edit-wins and confident-history-skips-AI scenarios
- [x] 5.4 Add `POST /import-batches/{id}/ai-suggest`, `POST /import-rows/{id}/ai-suggest` and `GET /ai/usage`, with bounded concurrency and per-row status; verify endpoint tests with the fake transport and that AI-suggested rows stay pending
- [x] 5.5 Add the AI source badge and explanation, "Ask AI (n rows, ≈ $x)", "Use AI split" and the Settings AI section; verify `pnpm run build` and a browser check with the fake transport
- [x] 5.6 Document AI behaviour, cost controls, precedence and privacy in `docs/receipts-ai-splits.md`, and update `AGENTS.md` architecture notes; verify the documented settings and endpoints match the code

## 6. Integration

- [x] 6.1 Run the full backend suite, `pnpm run build`, flow tests and lint (no new errors), and verify all pass
- [x] 6.2 End-to-end browser check on a throwaway stack: upload a synthetic file, fetch synthetic receipts from the fake mailbox, link a receipt, ask AI (fake transport), accept a split and dry-run push; verify the payload in the fake YNAB contains the expected subtransactions

## Workflow follow-up

- After Bridge is installed and a key is added: a read-only Bridge fetch and a small paid AI sample, checked with the user.
- Propose a commit and wait for approval (AGENTS.md); archive after review.
