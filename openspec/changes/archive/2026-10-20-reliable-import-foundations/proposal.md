# Proposal

## Why

Monthly PostFinance and Cashback imports need reliable money handling, preserved bank evidence, and repeatable writes before receipt or AI automation can be trusted. The current checkout still lacks Swisscard support and can overstate category confidence, duplicate repeat uploads, and discard failed rows after mixed write results.

## What Changes

- Detect Swisscard exports by headers; validate dates, currency, signed amounts and status, retain original fields, and default to Posted candidates with visible exclusion/error counts.
- Select and persist the target account before prediction; carry account context through reload and reclassification.
- **BREAKING**: Require target account context for new uploads and persisted row references for writes; update the bundled frontend and return actionable errors for legacy standalone write payloads.
- Use exact money conversion and consistent history/prediction units; keep raw evidence separate from edits.
- Separate category and payee confidence, account for conflicting history, and let payee-only rules continue to category inference while respecting explicit review/ignore actions.
- Introduce persistent, occurrence-aware source identities independent of predictions and upload batches, with conservative reconciliation against earlier YNAB imports.
- Correlate write responses by identity; persist confirmed outcomes and keep failed or unresolved rows available for retry, including after reload.
- Recognize repayment candidates as transfers; hold unresolved routing for review and explicitly write `approved=false` for subsequent YNAB audit.
- Retain the existing ML training label alignment fix and verify it with regression coverage.

## Capabilities

### New Capabilities

- `bank-csv-ingestion`: Supported CSV formats, precise money/date validation, account context, source evidence and import lifecycle metadata.
- `transaction-classification`: Layered payee/category inference, contextual history matching, confidence and review eligibility.
- `transaction-import-reconciliation`: Persistent source identity, transfer safeguards, deduplication, write outcome correlation and retry behavior.

### Modified Capabilities

None; the project currently has no durable OpenSpec capabilities.

## Impact

Backend: `csv_service.py`, `models.py`, Alembic migrations, `main.py`, `historical_matcher.py`, `orchestrator.py`, `write_back_service.py`, transaction sync and YNAB client response handling. Frontend: import account selection, API types, review state and push results in `App.tsx` and relevant components. Tests use synthetic fixtures, isolated databases and mocked YNAB responses; private samples remain ignored.

The app's import API gains persisted account, evidence, confidence, identity and result fields. Existing stored batches require a compatibility path; legacy identities cannot be reconstructed from missing raw fields with certainty. Proton, cloud AI, receipt matching, splits, evaluation tooling and deployment changes are outside this Phase 1 change. No account ownership, work-lunch or unknown-category policy is inferred.
