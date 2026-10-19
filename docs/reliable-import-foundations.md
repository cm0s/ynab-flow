# Reliable import foundations

Reference for the `reliable-import-foundations` change. Sections are added as
each part lands.

## 1. Data model (migrations `e7c5d6f8a1b2`, `f8d6e7a9b2c3`)

Migrations are additive. Legacy columns keep their meaning:

| Table | Legacy column | Meaning (unchanged) | New authoritative column |
|---|---|---|---|
| `transactions` | `amount` (float) | YNAB **milliunits** | `amount_milliunits` (BIGINT) |
| `import_rows` | `amount` (float) | **currency units** | `amount_milliunits` (BIGINT) |

New columns:

- `transactions`: `import_id`, `transfer_account_ynab_id`, `transfer_transaction_ynab_id` (filled by sync).
- `import_batches`: `account_id`, `account_confirmed`, `source_format`, `report_json` (counts plus Pending/invalid evidence), `export_signature`.
- `import_rows`: `account_id`, `amount_milliunits`, `currency`, `source_format`, `source_status`, `evidence_json` (immutable raw source fields), `source_record_id`, `payee_*`/`category_*` confidence/source/explanation, `review_flag`, `transfer_state`, `transfer_account_id`, `reconciliation_state/_note`, `write_state`, `write_error`, `ynab_transaction_id`.
- `source_records` (new): the occurrence ledger. Unique on `(plan_id, account_id, fingerprint, occurrence)` and on `import_id`; carries `write_state` (`unsent|claimed|created|existing|error|reconcile_required`), `claim_token`, `claimed_at` and the known YNAB transaction id.
- `source_exports` (new): order-independent multiset signature per upload and account scope.

Review `status` (`pending|accepted|ignored`) stays separate from `write_state`.

### Backfill and legacy compatibility

- History: `amount_milliunits` is copied from the stored milliunit value (not multiplied). Non-integral values stay `NULL`.
- Import rows: `amount_milliunits = Decimal(amount) * 1000` when exact. Otherwise the value stays `NULL` and the row is `quarantined`; valid rows become `legacy_unresolved`.
- Legacy batches have `account_id NULL` and `account_confirmed = false`. They stay readable, but need account confirmation and reconciliation before their first new write. Missing evidence is never reconstructed.

### SQLite / PostgreSQL

Portable types only (`BigInteger`, `Text`, `String`); FK/unique constraints are added with `batch_alter_table`, which recreates tables on SQLite and alters in place on PostgreSQL. Claims use conditional `UPDATE ... WHERE write_state IN (...)` rather than dialect-specific locking.

Validation: fresh upgrade, legacy-data upgrade and downgrade on disposable SQLite (`tests/test_migrations.py`). On a disposable PostgreSQL container (`postgres:latest`, loopback, removed afterwards) the full upgrade and downgrade ran successfully; the legacy-data backfill was **not** exercised on PostgreSQL.

### Rollback limits

Disable writes and restore the previous release first. Downgrading drops the ledger and with it all deduplication evidence, so back up the database first. A schema rollback cannot undo transactions already created in YNAB.

## 2. Supported formats and upload requirements

`POST /upload-csv?plan_id=…&account_id=…` — **`account_id` is now required** (the local id of an open account in that plan). Without a valid account the upload is rejected with 422 and the current active batch is kept. The same happens for unsupported layouts and non-UTF-8 files.

| Format | Detection | Notes |
|---|---|---|
| Swisscard | headers: `Transaction date, Description, Merchant, Card number, Currency, Amount, Debit/Credit, Status` (any column order, BOM/whitespace tolerated, any filename) | `DD.MM.YYYY`; `Debit` = positive amount, `Credit` = negative amount; YNAB amount is the negation (Debit 50.00 → -50000, Credit -43.70 → +43700). CHF only. |
| PostFinance | first line starts `Date de d…` | semicolon separated; debit/credit columns |
| Generic | `Date, Memo, Inflow, Outflow` | `YYYY-MM-DD` or `DD.MM.YYYY` |

Swisscard `Posted` rows are candidates; `Pending` rows are kept as evidence in the batch report and are never written. The report separates `detected / eligible / pending / invalid`; invalid rows (impossible date, non-finite or over-precise amount, unsupported currency/status, sign contradicting Debit/Credit) are listed with their file line and do not stop the other rows. Original fields (description, merchant, card, both source categories, foreign-currency fields) are stored in `evidence_json` and never change when you edit payee/category/memo.

The source category used for rules is `Registered Category`, falling back to `Merchant Category`.

## 3. Confidence

Payee and category each have a confidence and provenance (`rule`, `exact_history`, `fuzzy_history`, `ml`). The row's aggregate `confidence` is the **minimum over the required targets** (a missing target counts as 0; repayments only require a payee). A row is auto-accepted only when both reach 0.95 and no rule requested review. A rule only locks the targets it supplies; an explicit review flag or ignore is never cleared by later evidence.

Exact-history confidence is `winning share × min(1, 0.85 + 0.05 × winning count)`, so a 3:2 category conflict scores 0.60. Same-account, same-sign evidence is preferred; otherwise a capped fallback is used (same sign ≤ 0.90, any sign ≤ 0.70). Fuzzy matches below 0.75 fall through to ML.

**These scores are operational confidence values, not measured accuracy.** Accuracy has not been evaluated (Phase 2).

## 4. Identity and reconciliation limits

Each eligible row gets a persistent identity per plan + target account: a SHA-256 fingerprint of the immutable evidence (format, date, signed milliunits, currency, masked card, raw description and merchant) plus an occurrence ordinal. The YNAB `import_id` is `YF1:<24 hex>:<occurrence>` (≤ 36 characters, as the YNAB API allows). Edits, predictions, batches and Pending/Posted state do not affect it.

- Identical purchases in one file get separate occurrences; uploading the same file again (or reordered) reuses them and creates no new identities. A file that shows more copies than the ledger knows adds only the extra occurrences.
- A file showing **fewer** copies than previously seen is **ambiguous** when the earlier copies are in different write states (e.g. one already in YNAB). The row is flagged: choose "same purchase as before" or "new purchase". The app does not guess, and there is no guarantee of automatic deduplication for indistinguishable subsets.
- Before writing, YNAB history is refreshed and each row is matched: explicit ledger link or matching `import_id` → existing; a single candidate with the same account, date and amount **plus** description evidence → linked; equal account/date/amount alone is only a **potential** match and blocks until you link it or mark it as new. If history cannot be refreshed, rows stay **unverified** and nothing is written. A link to a transaction that was deleted in YNAB needs explicit "recreate" (which uses a fresh occurrence and import id).
- Batches created before this change load read-only-ish: confirm their account (reassign) first; their identities are built from the stored memo and amount, so matching relies on the conservative YNAB-history rules above.

## 5. Repayments (transfers)

Positive credits whose description looks like a card repayment (e.g. "Payment – Thank you", "Zahlung … Danke") become transfer candidates with no ordinary category. A plain refund stays an ordinary row. The app does **not** infer who owns the card or any work-lunch policy.

- If a Post-side transfer into the card already exists in YNAB (same amount, ±3 days), its card-side counterpart is linked and nothing is created.
- Several possible counterparts, or an unresolved account, block the write.
- Choose "paid from" account to create exactly one transaction on the card account with the transfer payee and a null category (YNAB creates the counterpart); or "keep for later".

## 6. Writing to YNAB

`POST /write-back` now references persisted import rows. Only payee, category, memo, flag colour and cleared status are accepted from the caller; date, amount (integer milliunits), account and import id are read from the persisted source record (extra fields are ignored). The old `transactions` array is rejected with 422.

```json
// request
{"plan_id": "…", "mode": "dry_run|create|create_or_skip", "batch_id": "…",
 "rows": [{"row_id": "…", "payee_name": "Shop", "category_name": "Groceries", "memo": "", "flag_color": null}]}
// response
{"mode": "create", "total": 3, "created": 1, "existing": 1, "errors": 0, "unresolved": 1,
 "results": [{"index": 0, "row_id": "…", "source_id": "…", "import_id": "YF1:…:1", "date": "2026-10-01",
              "payee_name": "Shop", "amount": -50.0, "amount_milliunits": -50000,
              "status": "created|existing|error|unresolved|dry_run_ok|dry_run_error",
              "ynab_transaction_id": "…", "error": null}]}
```

- Every created transaction is sent with `approved=false`. Accepting a row in the review table is local only; YNAB approval remains a separate audit step.
- Outcomes are matched by `import_id`, never by response order. If YNAB returns only transaction ids, each is read back to learn its import id. Missing, repeated or contradictory outcomes are `unresolved`.
- A source record is claimed before the request (no database transaction is held over the network). Timeouts, network failures and 5xx responses leave it `reconcile_required`; the next push checks YNAB history and, if the transaction is absent, resends the **same** import id. A restart converts in-flight claims the same way. 4xx responses are `error` (retryable).
- Only `created` and `existing` rows leave the active queue (also after reload and reclassification). `error`/`unresolved` rows stay with their edits and error text. `POST /import-batches/{id}/complete` returns 409 while any non-ignored row is not yet in YNAB.
- **Dry run** performs the same validation and reconciliation but sends nothing, claims nothing, and rolls back any link or completion state; all review rows are retained.

Other endpoints: `POST /import-batches/{id}/reconcile`, `POST /import-rows/{id}/resolve` (`link_existing|confirm_new|confirm_same`), `POST /import-rows/{id}/transfer`, `POST /import-batches/{id}/reassign-account`.

## 7. Local checks

- Backend: `cd backend && pytest tests/ -v`
- Frontend: `cd frontend && pnpm run build` (use the placeholder `VITE_*` values in `AGENTS.md`) and `pnpm run test:flow` (push-result handling).
